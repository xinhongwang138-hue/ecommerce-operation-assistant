"""知识库资料落盘、阶段映射与上传体积上限的测试。

刻意全部不依赖智库服务、不加载 torch：这几段逻辑（白名单、按租户分目录落盘、
路径穿越防护、阶段推进、请求体上限）正是出事故时会静默丢数据或跨租户串号的
地方，必须能在任何机器上稳定重跑。
"""
import asyncio
import io
import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.middleware import BodySizeLimitMiddleware
from app.models import Base, KnowledgeDocument, KnowledgeDocumentStatus, Platform, Product
from app.services import knowledge_documents as kd
from app.services.knowledge_documents import _stage_of, create_document, store_upload
from tests.support import make_engine


@pytest.fixture()
def db():
    engine = make_engine()
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def upload_root(tmp_path, monkeypatch):
    """把上传根目录换到 tmp_path，避免测试往仓库 api/uploads 里写文件"""
    monkeypatch.setattr(kd, "UPLOAD_DIR", tmp_path.resolve())
    return tmp_path.resolve()


def make_upload(name: str, content: bytes) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(content))


def settings_with(max_bytes: int):
    return SimpleNamespace(knowledge_max_upload_bytes=max_bytes)


def test_store_upload_rejects_unsupported_suffix(upload_root, monkeypatch):
    monkeypatch.setattr(kd, "get_settings", lambda: settings_with(1024 * 1024))
    with pytest.raises(HTTPException) as caught:
        store_upload(1, make_upload("手册.exe", b"MZ"))
    assert caught.value.status_code == 400
    assert list((upload_root / "knowledge").glob("**/*")) == []


def test_store_upload_rejects_empty_file(upload_root, monkeypatch):
    monkeypatch.setattr(kd, "get_settings", lambda: settings_with(1024 * 1024))
    with pytest.raises(HTTPException) as caught:
        store_upload(1, make_upload("empty.md", b""))
    assert caught.value.status_code == 400


def test_store_upload_enforces_size_cap_and_leaves_no_orphan(upload_root, monkeypatch):
    monkeypatch.setattr(kd, "get_settings", lambda: settings_with(100))
    with pytest.raises(HTTPException) as caught:
        store_upload(1, make_upload("big.md", b"x" * 5000))
    assert caught.value.status_code == 413
    # 超限必须把半成品文件删掉，否则残留会被后续排查误当成已入库资料；
    # 空的租户目录留着无害，不该算残留
    assert [p for p in upload_root.glob("knowledge/**/*") if p.is_file()] == []


def test_store_upload_strips_directory_from_filename(upload_root, monkeypatch):
    """文件名带 ../ 或绝对路径时只能取 basename，写到 uploads 之外就是路径穿越"""
    monkeypatch.setattr(kd, "get_settings", lambda: settings_with(1024 * 1024))
    title, relative, target, size = store_upload(
        7, make_upload("../../etc/evil.md", b"# hello")
    )
    assert title == "evil"
    assert relative.startswith("knowledge/7/")
    assert target.is_relative_to(upload_root)
    assert size == len(b"# hello")


def test_store_upload_isolates_directories_per_workspace(upload_root, monkeypatch):
    monkeypatch.setattr(kd, "get_settings", lambda: settings_with(1024 * 1024))
    _, relative_a, _, _ = store_upload(11, make_upload("a.md", b"# a"))
    _, relative_b, _, _ = store_upload(22, make_upload("b.md", b"# b"))
    assert relative_a.startswith("knowledge/11/")
    assert relative_b.startswith("knowledge/22/")


def test_create_document_persists_row_with_pending_status(db, upload_root, monkeypatch):
    monkeypatch.setattr(kd, "get_settings", lambda: settings_with(1024 * 1024))
    document = create_document(
        db,
        workspace_id=3,
        upload=make_upload("产品手册.md", "# 手册".encode("utf-8")),
        user_id=None,
        product_id=None,
    )
    assert document.status is KnowledgeDocumentStatus.PENDING
    stored = db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == document.id))
    assert stored is not None and stored.title == "产品手册"


def test_agent_knowledge_refs_skips_upstream_without_ready_documents(db, monkeypatch):
    """本租户没有已入库资料时不该打智库：既省一次必然空手的往返，
    也避免在没配置知识库的环境里给每次 Agent 运行添一次失败外呼。"""
    calls = []
    monkeypatch.setattr(kd, "kb_search", lambda *a, **k: calls.append(a) or {})
    assert kd.agent_knowledge_refs(db, 5, "熔带怎么更换") == []
    assert calls == []


def test_agent_knowledge_refs_degrades_to_empty_on_upstream_failure(db, monkeypatch):
    """知识库是增强项：它挂了不能把整条文案生成链路一起拖停"""
    db.add(
        KnowledgeDocument(
            workspace_id=5,
            title="手册",
            original_filename="手册.md",
            stored_path="knowledge/5/x.md",
            status=KnowledgeDocumentStatus.READY,
        )
    )
    db.commit()

    def boom(*_args, **_kwargs):
        raise kd.KnowledgeUpstreamError("上游挂了")

    monkeypatch.setattr(kd, "kb_search", boom)
    assert kd.agent_knowledge_refs(db, 5, "问题") == []


def test_agent_knowledge_refs_maps_sources_and_links_document_id(db, monkeypatch):
    db.add(
        KnowledgeDocument(
            workspace_id=5,
            title="烫金机手册",
            original_filename="烫金机手册.md",
            stored_path="knowledge/5/y.md",
            status=KnowledgeDocumentStatus.READY,
        )
    )
    db.commit()
    monkeypatch.setattr(
        kd,
        "kb_search",
        lambda *_a, **_k: {
            "sources": [
                {"file_title": "烫金机手册", "chunk_id": 91, "title": "保养", "snippet": "每季度清洁", "score": 0.81},
                {"file_title": "别家的文档", "chunk_id": 92, "title": "x", "snippet": "y"},
            ]
        },
    )
    refs = kd.agent_knowledge_refs(db, 5, "保养周期")
    assert len(refs) == 2
    # 对得上本租户登记号的才给链接，对不上的说明不是自家文档
    assert refs[0]["document_id"] is not None
    assert refs[1]["document_id"] is None
    assert refs[0]["parent_title"] == "保养"


def test_disambiguation_reply_is_flagged_not_silent(db, monkeypatch, caplog):
    """智库没认出商品主体时会回一句追问，sources 为空但 HTTP 200。

    不区分这种空，Agent 就会长期静默地拿不到资料而无人知情。
    """
    db.add(
        KnowledgeDocument(
            workspace_id=5,
            title="手册",
            original_filename="手册.md",
            stored_path="knowledge/5/z.md",
            status=KnowledgeDocumentStatus.READY,
        )
    )
    db.commit()
    monkeypatch.setattr(
        kd,
        "kb_search",
        lambda *_a, **_k: {
            "answer": "您是想问以下哪个产品？请明确型号。",
            "sources": [],
            "item_names": [],
            "done_list": ["node_item_name_confirm", "node_answer_output"],
        },
    )
    with caplog.at_level("WARNING"):
        assert kd.agent_knowledge_refs(db, 5, "随便") == []
    assert "未执行检索" in caplog.text


def test_stage_of_picks_deepest_completed_node():
    assert _stage_of([]) is KnowledgeDocumentStatus.SUBMITTING
    assert _stage_of(["node_document_split"]) is KnowledgeDocumentStatus.SPLITTING
    # 智库 done_list 里节点顺序不保证，取最深一档而不是最后一个
    assert _stage_of(["node_bge_embedding", "node_md_img"]) is KnowledgeDocumentStatus.EMBEDDING
    assert (
        _stage_of(["node_import_milvus", "node_bge_embedding"])
        is KnowledgeDocumentStatus.IMPORTING
    )


async def _drive_middleware(path: str, method: str, body: bytes, limit: int):
    """跑一遍 BodySizeLimitMiddleware，返回（上游收到的字节数, 响应状态码或 None）"""
    received = {"bytes": 0}

    async def app(scope, receive, send):
        while True:
            message = await receive()
            if message["type"] == "http.request":
                received["bytes"] += len(message.get("body", b""))
                if not message.get("more_body"):
                    break
            elif message["type"] == "http.disconnect":
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    status = {"code": None}

    async def send(message):
        if message["type"] == "http.response.start":
            status["code"] = message["status"]

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(b"content-length", str(len(body)).encode())],
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    middleware = BodySizeLimitMiddleware(app)
    monkey_settings = limit
    from app.api import middleware as middleware_module

    original = middleware_module.get_settings
    middleware_module.get_settings = lambda: SimpleNamespace(
        max_request_bytes=monkey_settings,
        knowledge_max_upload_bytes=64 * 1024 * 1024,
    )
    try:
        await middleware(scope, receive, send)
    finally:
        middleware_module.get_settings = original
    return received["bytes"], status["code"]


def test_body_size_limit_widens_only_for_knowledge_upload():
    """全局 16MB 是 DoS 防线，不该为一个功能整站放宽；这里两边都要钉住"""
    body = b"x" * (20 * 1024 * 1024)
    read, code = asyncio.run(
        _drive_middleware("/api/v1/knowledge/documents", "POST", body, 16 * 1024 * 1024)
    )
    assert code == 200 and read == len(body)

    read, code = asyncio.run(
        _drive_middleware("/api/v1/products", "POST", body, 16 * 1024 * 1024)
    )
    assert code == 413 and read == 0


def test_body_size_limit_still_caps_knowledge_upload_above_its_own_ceiling():
    body = b"x" * (70 * 1024 * 1024)
    read, code = asyncio.run(
        _drive_middleware("/api/v1/knowledge/documents", "POST", body, 16 * 1024 * 1024)
    )
    assert code == 413 and read == 0


def test_product_context_carries_knowledge_refs(monkeypatch):
    """_load_product_context 是"资料 → 文案生成"的唯一接缝。

    出处就是在这里断掉的：检索取到了、但没被塞进 ProductContext，
    后面 prompt 与落库全都看不到。所以这个接缝必须有测试钉住。
    """
    from app.agents.contracts import ProductContext
    from app.services import agent_runs

    engine = make_engine()
    Base.metadata.create_all(bind=engine)

    captured: dict = {}

    def fake_refs(db, workspace_id, query, *, limit: int = 4):
        captured["workspace_id"] = workspace_id
        captured["query"] = query
        return [{
            "file_title": "烫金机手册", "chunk_id": "9", "parent_title": "保养",
            "snippet": "每季度清洁", "score": 0.8, "document_id": 3,
        }]

    monkeypatch.setattr(agent_runs, "SessionLocal", lambda: Session(engine))
    monkeypatch.setattr(agent_runs, "agent_knowledge_refs", fake_refs)

    with Session(engine) as db:
        db.add(Product(id=101, workspace_id=7, name="HAK180烫金机",
                       platform=Platform.DOUYIN, category="包装设备"))
        db.commit()

    context = agent_runs._load_product_context(101)

    assert isinstance(context, ProductContext)
    assert captured["workspace_id"] == 7, "租户没从商品行取，跨租户风险"
    assert "HAK180烫金机" in captured["query"]
    assert len(context.knowledge_refs) == 1
    assert context.knowledge_refs[0].file_title == "烫金机手册"
    assert context.knowledge_refs[0].document_id == 3


def test_product_context_without_workspace_skips_knowledge(monkeypatch):
    """历史数据里 workspace_id 可为空；这种商品不该被随便归到某个租户下去检索"""
    from app.services import agent_runs

    engine = make_engine()
    Base.metadata.create_all(bind=engine)
    calls: list = []

    monkeypatch.setattr(agent_runs, "SessionLocal", lambda: Session(engine))
    monkeypatch.setattr(
        agent_runs, "agent_knowledge_refs",
        lambda *a, **k: calls.append(a) or [],
    )

    with Session(engine) as db:
        db.add(Product(id=102, workspace_id=None, name="无主商品", platform=Platform.DOUYIN))
        db.commit()

    context = agent_runs._load_product_context(102)

    assert context.knowledge_refs == []
    assert calls == []
