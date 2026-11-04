"""知识库代理层的请求契约测试。

用一个本地 stub HTTP 服务把"代理层到底往外发了什么"钉死。这几条是整个租户
隔离设计的地基，一旦回归就是跨租户事故，所以不能只靠人眼读代码：

  1. 租户只出现在 X-KB-Internal 请求头里，请求体/查询参数里不得出现任何租户字段；
  2. 令牌必须能用同一密钥验回来，且 tenant 等于 ws_<workspace_id>；
  3. 上游 5xx 时，响应文案不得带上游原文（里面常有内网地址与栈信息）；
  4. 未配置时抛 KnowledgeNotConfiguredError，绝不退化成"不带令牌匿名请求"。
"""
import base64
import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from app.services import knowledge_auth
from app.services import knowledge_proxy as proxy
from app.services.knowledge_proxy import (
    KnowledgeNotConfiguredError,
    KnowledgeUpstreamError,
    search,
    upload_document,
)

SECRET = "unit-test-kb-secret-not-for-deployment"


class Stub(BaseHTTPRequestHandler):
    """记录收到的请求并按 stub_response 回复"""

    received: list[dict] = []
    stub_response: tuple[int, dict] = (200, {"code": 200})

    def _capture(self, body: bytes):
        type(self).received.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body,
            }
        )

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        self._capture(self.rfile.read(length))
        status_code, payload = type(self).stub_response
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):
        """静音默认访问日志，避免污染测试输出"""


@pytest.fixture()
def stub_server():
    Stub.received = []
    Stub.stub_response = (200, {"code": 200, "task_ids": ["t-1"], "details": []})
    server = HTTPServer(("127.0.0.1", 0), Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def configured(stub_server, monkeypatch):
    def _configured(**overrides):
        values = {
            "kb_import_base_url": stub_server,
            "kb_query_base_url": stub_server,
            "kb_internal_secret": SECRET,
            "kb_internal_token_ttl_seconds": 120,
            "kb_timeout_seconds": 5,
        }
        values.update(overrides)
        settings = SimpleNamespace(**values)
        # 代理层与令牌签发各自 import 了一份 get_settings，两处都要指向同一配置，
        # 否则测试里会出现"代理以为配好了、签发器还在读真实空配置"的假失败
        monkeypatch.setattr(proxy, "get_settings", lambda: settings)
        monkeypatch.setattr(knowledge_auth, "get_settings", lambda: settings)
        return settings

    return _configured


def decode_tenant(token: str, secret: str = SECRET) -> dict:
    """按智库侧同样的方式验签，确认两端格式契约一致"""
    payload, signature = token.split(".", 1)
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert hmac.compare_digest(signature, expected), "令牌签名与 HMAC 约定不符"
    padded = payload + "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def test_upload_sends_tenant_only_in_header(configured, stub_server, tmp_path):
    configured()
    document = tmp_path / "手册.md"
    document.write_bytes(b"# content")

    task_ids = upload_document(42, document, "产品手册.md")

    assert task_ids == ["t-1"]
    request = Stub.received[0]
    assert request["method"] == "POST" and request["path"] == "/upload"
    assert decode_tenant(request["headers"]["x-kb-internal"])["tenant"] == "ws_42"
    # 请求体里不得出现租户：智库只认令牌，出现即说明我们把归属交给了调用方可控的位置
    body = request["body"].decode("utf-8", errors="replace").lower()
    assert "tenant" not in body
    assert "workspace" not in body
    assert "产品手册.md" in body


def test_search_body_carries_no_tenant_field(configured, stub_server):
    configured()
    Stub.stub_response = (200, {"answer": "a", "sources": [{"file_title": "手册"}], "session_id": "s"})

    result = search(7, "熔带怎么更换")

    assert result["sources"] == [{"file_title": "手册"}]
    request = Stub.received[0]
    assert request["path"] == "/query"
    payload = json.loads(request["body"].decode("utf-8"))
    assert set(payload) == {"query", "is_stream"}, f"请求体出现多余字段: {set(payload)}"
    assert decode_tenant(request["headers"]["x-kb-internal"])["tenant"] == "ws_7"


def test_upstream_error_does_not_leak_detail(configured, stub_server, tmp_path):
    """上游原文常带内网地址与栈信息，只能进日志，不能进异常文案"""
    configured()
    Stub.stub_response = (
        500,
        {"detail": "connection to 10.0.3.7:19530 failed Traceback ..."},
    )
    document = tmp_path / "x.md"
    document.write_bytes(b"# x")

    with pytest.raises(KnowledgeUpstreamError) as caught:
        upload_document(1, document, "x.md")

    message = str(caught.value)
    assert "10.0.3.7" not in message and "Traceback" not in message


def test_missing_configuration_refuses_anonymous_request(configured, monkeypatch):
    """没配好时必须关掉功能，而不是"少带一个头"继续发请求"""
    configured(kb_internal_secret="")
    with pytest.raises(KnowledgeNotConfiguredError):
        search(1, "问题")

    configured(kb_import_base_url="", kb_query_base_url="")
    with pytest.raises(KnowledgeNotConfiguredError):
        search(1, "问题")

    assert Stub.received == []  # 一次外呼都没发生


def test_unreachable_upstream_raises_typed_error(configured, monkeypatch):
    configured(kb_import_base_url="http://127.0.0.1:1", kb_query_base_url="http://127.0.0.1:1")
    with pytest.raises(KnowledgeUpstreamError):
        search(1, "问题")
