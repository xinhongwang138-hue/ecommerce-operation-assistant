"""知识库导入进度回传状态机的测试（不需要智库真跑，也不需要 torch）。

用 stub 智库按阶段推进 done_list，验证的是电商这一侧真正会出问题的部分：
阶段映射、终态判定、失败与超时的落库形态，以及每次外呼都带对了租户头。
"""
import base64
import hashlib
import hmac
import io
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Base, KnowledgeDocument, KnowledgeDocumentStatus
from app.services import knowledge_auth
from app.services import knowledge_documents as kd
from app.services import knowledge_proxy
from app.services.knowledge_proxy import KnowledgeUpstreamError
from tests.support import make_engine

SECRET = "import-flow-test-secret"


class StubKB(BaseHTTPRequestHandler):
    """按 script 逐步返回 /status 结果；/upload 固定返回一个 task_id"""

    script: list[dict] = []
    calls: list[dict] = []
    upload_status: tuple[int, dict] = (200, {"code": 200, "task_ids": ["task-1"]})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        self.rfile.read(length)
        type(self).calls.append(("upload", {k.lower(): v for k, v in self.headers.items()}))
        code, payload = type(self).upload_status
        self._reply(code, payload)

    def do_GET(self):  # noqa: N802
        type(self).calls.append(("status", {k.lower(): v for k, v in self.headers.items()}))
        if not type(self).script:
            self._reply(200, {"code": 200, "status": "processing", "done_list": [],
                              "running_list": [], "failed_list": []})
            return
        payload = type(self).script.pop(0)
        self._reply(200, payload)

    def _reply(self, code: int, payload: dict):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):
        """静音默认访问日志"""


def tenant_of(token: str) -> str:
    payload, _sig = token.split(".", 1)
    padded = payload + "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))["tenant"]


@pytest.fixture()
def kb_stub(monkeypatch):
    StubKB.script = []
    StubKB.calls = []
    StubKB.upload_status = (200, {"code": 200, "task_ids": ["task-1"]})
    server = HTTPServer(("127.0.0.1", 0), StubKB)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    settings = SimpleNamespace(
        kb_import_base_url=base,
        kb_query_base_url=base,
        kb_internal_secret=SECRET,
        kb_internal_token_ttl_seconds=120,
        kb_timeout_seconds=10,
        knowledge_max_upload_bytes=1024 * 1024,
        task_queue_backend="inline",
    )
    monkeypatch.setattr(kd, "get_settings", lambda: settings)
    monkeypatch.setattr(knowledge_auth, "get_settings", lambda: settings)
    # 这三个模块各自 from app.core.config import get_settings，漏一个就会读到真实
    # 空配置而抛"未配置"——是 import 风格的代价，不是产品缺陷
    monkeypatch.setattr(knowledge_proxy, "get_settings", lambda: settings)
    # 轮询间隔在测试里不真等
    monkeypatch.setattr(kd, "time_sleep", lambda _s: None)
    try:
        yield base
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(kd, "UPLOAD_DIR", tmp_path.resolve())
    engine = make_engine()
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        yield db


def new_document(db, workspace_id: int = 7) -> KnowledgeDocument:
    return kd.create_document(
        db,
        workspace_id=workspace_id,
        upload=UploadFile(filename="烫金机手册.md", file=io.BytesIO("# 熔带更换".encode("utf-8"))),
        user_id=None,
        product_id=None,
    )


def observed_statuses(db, document_id: int) -> tuple[list, object]:
    """在每次轮询睡眠点记录当前状态，得到真实推进序列。

    复用同一个 Session 并 expire_all：内存 SQLite 用 StaticPool 共享一条连接，
    另开 Session 会和被测代码的事务互相踩。
    """
    trail: list[KnowledgeDocumentStatus] = []

    def spy(_seconds):
        db.expire_all()
        current = db.scalar(
            select(KnowledgeDocument.status).where(KnowledgeDocument.id == document_id)
        )
        if current is not None:
            trail.append(current)

    return trail, spy


def test_import_advances_through_stages_to_ready(session, kb_stub, monkeypatch):
    StubKB.script = [
        {"status": "processing", "done_list": ["upload_file", "node_entry"], "running_list": [], "failed_list": []},
        {"status": "processing", "done_list": ["node_pdf_to_md", "node_document_split"], "running_list": [], "failed_list": []},
        {"status": "processing", "done_list": ["node_bge_embedding"], "running_list": [], "failed_list": []},
        {"status": "processing", "done_list": ["node_import_milvus"], "running_list": [], "failed_list": []},
        {"status": "completed", "done_list": ["node_import_milvus"], "running_list": [], "failed_list": []},
    ]
    document = new_document(session)
    trail, spy = observed_statuses(session, document.id)
    monkeypatch.setattr(kd, "time_sleep", spy)

    result = kd.import_document(session, document.id)

    assert result.status is KnowledgeDocumentStatus.READY
    assert result.kb_task_id == "task-1"
    assert result.submitted_at is not None and result.completed_at is not None
    assert result.error_message is None
    # 阶段必须单调前进，不能一步从"提交"跳到"完成"——那意味着进度条是假的
    assert KnowledgeDocumentStatus.SPLITTING in trail
    assert KnowledgeDocumentStatus.EMBEDDING in trail
    assert KnowledgeDocumentStatus.IMPORTING in trail
    assert trail.index(KnowledgeDocumentStatus.SPLITTING) < trail.index(KnowledgeDocumentStatus.EMBEDDING)
    assert trail.index(KnowledgeDocumentStatus.EMBEDDING) < trail.index(KnowledgeDocumentStatus.IMPORTING)


def test_every_poll_carries_the_tenant_header(session, kb_stub):
    document = new_document(session, workspace_id=42)
    StubKB.script = [{"status": "completed", "done_list": [], "running_list": [], "failed_list": []}]

    kd.import_document(session, document.id)

    kinds = [kind for kind, _headers in StubKB.calls]
    assert kinds[0] == "upload" and "status" in kinds
    for _kind, headers in StubKB.calls:
        token = headers.get("x-kb-internal")
        assert token, f"外呼缺少 X-KB-Internal：{headers.keys()}"
        assert tenant_of(token) == "ws_42"


def test_failed_node_marks_document_failed_with_node_name(session, kb_stub):
    StubKB.script = [
        {"status": "failed", "done_list": ["node_document_split"], "running_list": [],
         "failed_list": ["node_bge_embedding"]},
    ]
    document = new_document(session)

    result = kd.import_document(session, document.id)

    assert result.status is KnowledgeDocumentStatus.FAILED
    assert "node_bge_embedding" in (result.error_message or "")


def test_upload_rejection_marks_failed_and_keeps_upstream_detail_out(session, kb_stub):
    StubKB.upload_status = (400, {"detail": "不支持的文件类型 .exe at /srv/internal/path"})
    document = new_document(session)

    with pytest.raises(KnowledgeUpstreamError):
        kd.import_document(session, document.id)

    stored = session.scalar(
        select(KnowledgeDocument).where(KnowledgeDocument.id == document.id)
    )
    assert stored.status is KnowledgeDocumentStatus.FAILED
    # 上游原文常带内部路径，只进日志不进库
    assert "/srv/internal" not in (stored.error_message or "")


def test_timeout_is_reported_as_failure_not_silence(session, kb_stub, monkeypatch):
    """智库卡住时必须落到 failed，不能永远停在"处理中"让前端空转"""
    monkeypatch.setattr(kd, "IMPORT_DEADLINE_SECONDS", 0)
    StubKB.script = []
    document = new_document(session)

    result = kd.import_document(session, document.id)

    assert result.status is KnowledgeDocumentStatus.FAILED
    assert "分钟" in (result.error_message or "")


def test_missing_file_marks_failed(session, kb_stub):
    document = new_document(session)
    document.stored_path = "knowledge/7/does-not-exist.md"
    session.commit()

    with pytest.raises(KnowledgeUpstreamError):
        kd.import_document(session, document.id)

    stored = session.scalar(
        select(KnowledgeDocument).where(KnowledgeDocument.id == document.id)
    )
    assert stored.status is KnowledgeDocumentStatus.FAILED
