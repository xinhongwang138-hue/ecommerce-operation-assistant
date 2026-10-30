"""合同审批（P05）测试。

分四层：
- 抽取与切分是纯函数，钉住"什么文件能进""条款边界在哪"；
- 规则是纯函数，每条都要有正例和反例——只测正例的规则库会越涨越吵；
- 语义复核测的是那道 quote 校验，而不是模型水平（模型水平测不了）；
- 服务与接口测租户隔离、重跑幂等、审批留痕。
"""

from __future__ import annotations

import io
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routes.auth import get_current_user
from app.contracts import clauses as clause_splitter
from app.contracts import extract, rules, scoring, semantic, service
from app.db.session import get_db
from app.main import app
from app.models import Base, Contract, ContractApproval, ContractRisk
from tests.dataplatform_support import auth_payload, seed_tenant
from tests.support import make_engine, make_session_override

BASE = "/api/v1/contracts"

CONTRACT = """采购框架合同

甲方：杭州云帆贸易有限公司
乙方：上海麦丰食品有限公司
签订日期：2026 年 10 月 1 日

第一条 合作内容
乙方向甲方供应低糖燕麦饼干等预包装食品，具体品项与规格以双方确认的订单为准。

第二条 付款方式
甲方在收到发票后 90 日内付款，账期自发票开具之日起算。
本合同签订后 3 日内，甲方支付定金 50%。

第三条 交付
乙方应在收到订单后 5 日内发货，运费由乙方承担。

第四条 违约责任
任何一方逾期履行的，应按合同金额的 40% 向对方支付违约金。
乙方因产品质量问题给甲方造成的损失，由乙方承担全部损失，不以本合同金额为限。

第五条 合同的解除
甲方有权单方面解除本合同，无需说明理由。

第六条 合同期限
本合同有效期一年，期满自动续期一年。

第七条 争议解决
因本合同产生的争议，提交香港国际仲裁中心仲裁。

第八条 保密
双方对合作中知悉的商业秘密负保密义务。
"""

# 一份"干净"的合同：同样的主题，但每条都写对了。用来验证规则不误报。
CLEAN_CONTRACT = """采购框架合同

第一条 合作内容
乙方向甲方供应低糖燕麦饼干，品项与规格以双方确认的订单为准。

第二条 付款方式
甲方在收到发票后 30 日内付款。无需预付定金。

第三条 交付与验收
乙方应在收到订单后 5 日内发货。甲方应在到货后 7 日内完成验收，
抽检比例 5%，未提出书面异议视为验收合格。

第四条 违约责任
逾期交付的，每逾期一日按未交付部分金额的 0.5% 支付违约金，累计不超过合同金额的 5%。

第五条 保密
双方对合作中知悉的商业秘密负保密义务，保密期限自签署起三年。

第六条 争议解决
因本合同产生的争议，提交上海国际经济贸易仲裁委员会仲裁。
"""


def _docx_bytes(paragraphs: list[str]) -> bytes:
    from docx import Document

    document = Document()
    for line in paragraphs:
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# --- 抽取 ---------------------------------------------------------------------


def test_extract_plain_text():
    text = extract.extract("合同.txt", CONTRACT.encode("utf-8"))

    assert "第二条 付款方式" in text
    assert text.startswith("采购框架合同")


def test_extract_gbk_encoded_text():
    """中文合同一半是 GBK。只认 UTF-8 的话，用户会拿到一句"编码无法识别"。"""
    text = extract.extract("合同.txt", CONTRACT.encode("gb18030"))

    assert "第二条 付款方式" in text


def test_extract_docx_keeps_tables():
    """条款常写在表格里（付款节点表、验收标准表），漏了表就等于漏了条款。"""
    from docx import Document

    document = Document()
    document.add_paragraph("第一条 付款方式")
    document.add_paragraph("按下表节点付款：" * 3)
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "节点"
    table.cell(0, 1).text = "比例"
    table.cell(1, 0).text = "首付"
    table.cell(1, 1).text = "50%"
    buffer = io.BytesIO()
    document.save(buffer)

    text = extract.extract("合同.docx", buffer.getvalue())

    assert "首付" in text and "50%" in text


def test_extract_rejects_unsupported_suffix():
    with pytest.raises(extract.UnsupportedFormat):
        extract.extract("合同.doc", b"not a real doc")


def test_extract_rejects_scan_like_pdf_with_no_text():
    """抽不出字就报错，不要拿乱码去打分——乱码打出来的分数是假的。"""
    with pytest.raises((extract.EmptyText, extract.UnsupportedFormat)):
        extract.extract("扫描件.pdf", b"%PDF-1.4\n%%EOF\n")


# --- 条款切分 -----------------------------------------------------------------


def test_split_by_article_numbering():
    pieces = clause_splitter.split(CONTRACT)

    # 8 条 + 第 1 条之前的前言（标题与甲乙方）
    assert len(pieces) == 9
    assert pieces[0].heading == "采购框架合同"
    assert pieces[1].heading.startswith("第一条")
    assert [item.ordinal for item in pieces] == list(range(1, 10))


def test_split_clause_offsets_point_back_into_the_text():
    for piece in clause_splitter.split(CONTRACT):
        assert CONTRACT[piece.char_start : piece.char_end].strip().startswith(
            piece.heading.split("\n")[0][:6]
        )


def test_split_handles_contract_without_numbering():
    """没有编号的合同也要能审：整篇当一条，不要让用户因为格式被拒。"""
    pieces = clause_splitter.split("本合同约定甲方向乙方采购饼干，账期 90 天，双方签字生效。")

    assert len(pieces) == 1
    assert pieces[0].ordinal == 1


def test_classify_picks_the_dominant_topic():
    assert clause_splitter.classify("甲方在收到发票后 30 日内付款，账期自开票日起算") == "payment"
    assert clause_splitter.classify("因本合同产生的争议提交仲裁委员会仲裁") == "dispute"
    assert clause_splitter.classify("双方对商业秘密负保密义务") == "confidentiality"
    assert clause_splitter.classify("今天天气不错") == "other"


# --- 规则 ---------------------------------------------------------------------


def _keys(findings: list[rules.Finding]) -> set[str]:
    return {item.rule_key for item in findings}


def test_rules_catch_the_planted_risks():
    findings = rules.evaluate(clause_splitter.split(CONTRACT))
    keys = _keys(findings)

    assert "payment_days_too_long" in keys
    assert "deposit_too_high" in keys
    assert "penalty_too_high" in keys
    assert "unilateral_termination" in keys
    assert "auto_renewal_without_notice" in keys
    assert "unlimited_liability" in keys
    assert "foreign_jurisdiction" in keys
    assert "missing_acceptance_clause" in keys
    # 这份合同写了争议和违约条款，不该报"缺失"
    assert "missing_dispute_clause" not in keys
    assert "missing_breach_clause" not in keys


def test_clean_contract_produces_no_findings():
    """反例比正例重要：一个见谁都报的规则库等于没有规则库。"""
    findings = rules.evaluate(clause_splitter.split(CLEAN_CONTRACT))

    assert findings == []


def test_every_finding_carries_a_quote():
    """没有原句的风险条目一律不许进报告——审批人核对不了就没法担责。"""
    for finding in rules.evaluate(clause_splitter.split(CONTRACT)):
        assert finding.quote.strip()
        assert finding.suggestion.strip()
        assert finding.severity in ("low", "medium", "high")


def test_payment_days_ignores_shipping_days():
    """"5 日内发货"也是"天"，但它不是账期。"""
    pieces = clause_splitter.split(CONTRACT)
    delivery = next(item for item in pieces if item.kind == "delivery")

    assert rules._payment_days(delivery) == []


def test_unilateral_termination_not_reported_when_symmetric():
    pieces = clause_splitter.split("甲方有权解除本合同，乙方也有权解除本合同。")

    assert rules._unilateral(pieces[0]) == []


def test_auto_renewal_with_notice_is_fine():
    pieces = clause_splitter.split("本合同期满自动续期一年，任一方可在期满前 30 日书面通知不再续约。")

    assert rules._auto_renew(pieces[0]) == []


def test_chinese_numerals_are_parsed():
    assert rules.to_int("三十") == 30
    assert rules.to_int("九十") == 90
    assert rules.to_int("十五") == 15
    assert rules.to_int("100") == 100
    assert rules.to_int("一百二十") == 120


def test_payment_days_reads_chinese_numeral_terms():
    pieces = clause_splitter.split("第二条 付款方式\n甲方应在收到发票后九十日内付款。")

    findings = rules._payment_days(pieces[0])

    assert findings and findings[0].rule_key == "payment_days_too_long"
    assert "90" in findings[0].title


# --- 评分 ---------------------------------------------------------------------


def test_scoring_adds_by_severity_not_by_count():
    assert scoring.score_of(["high"]) > scoring.score_of(["low", "low", "low"])
    assert scoring.score_of(["high"]) == 34
    assert scoring.score_of(["high"] * 5) == 100  # 封顶


def test_level_thresholds():
    assert scoring.level_of(0) == "low"
    assert scoring.level_of(19) == "low"
    assert scoring.level_of(20) == "medium"
    assert scoring.level_of(49) == "medium"
    assert scoring.level_of(50) == "high"


# --- 语义复核 -----------------------------------------------------------------


def _stub_llm(monkeypatch, payload: dict):
    monkeypatch.setattr(
        semantic, "create_json_chat_completion", lambda *_a, **_k: (payload, {})
    )


def test_semantic_review_drops_fabricated_quotes(monkeypatch):
    """模型会编原文。引用在正文里找不到，整条丢掉——这是本模块最重要的一道闸。"""
    _stub_llm(
        monkeypatch,
        {
            "summary": "整体偏向我方不利",
            "risks": [
                {
                    "title": "验收标准由甲方单方决定",
                    "detail": "以甲方确认为准",
                    "quote": "以双方确认的订单为准",
                    "severity": "medium",
                    "suggestion": "改为明确标准",
                },
                {
                    "title": "不存在的一句话",
                    "detail": "模型编的",
                    "quote": "本合同未尽事宜由甲方全权解释",
                    "severity": "high",
                    "suggestion": "无",
                },
            ],
        },
    )

    result = semantic.review(CONTRACT, clause_splitter.split(CONTRACT), [])

    assert len(result.findings) == 1
    assert result.findings[0].title == "验收标准由甲方单方决定"
    assert result.findings[0].source == "llm"
    assert result.dropped == 1
    assert "引用不在原文中" in result.dropped_reason


def test_semantic_review_ignores_whitespace_differences(monkeypatch):
    """模型常把原文的换行变成空格。这类差异不该算编造。"""
    _stub_llm(
        monkeypatch,
        {
            "summary": "",
            "risks": [
                {
                    "title": "验收口径含糊",
                    "detail": "需要明确",
                    "quote": "以 双方 确认的订单为准",
                    "severity": "low",
                    "suggestion": "写清标准",
                }
            ],
        },
    )

    result = semantic.review(CONTRACT, clause_splitter.split(CONTRACT), [])

    assert len(result.findings) == 1
    assert result.findings[0].clause_ordinal == 2


def test_semantic_review_caps_the_number_of_risks(monkeypatch):
    _stub_llm(
        monkeypatch,
        {
            "summary": "",
            "risks": [
                {
                    "title": f"风险 {index}",
                    "detail": "d",
                    "quote": "以双方确认的订单为准",
                    "severity": "low",
                    "suggestion": "s",
                }
                for index in range(9)
            ],
        },
    )

    result = semantic.review(CONTRACT, clause_splitter.split(CONTRACT), [])

    assert len(result.findings) <= semantic.MAX_SEMANTIC_RISKS


# --- 服务与接口 ---------------------------------------------------------------


@pytest.fixture()
def engine():
    return make_engine()


@pytest.fixture()
def db(engine):
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def tenant(db):
    return seed_tenant(db, with_user=True)


def client(engine, user) -> TestClient:
    app.dependency_overrides[get_db] = make_session_override(engine)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_upload_persists_clauses_and_risks(db, tenant):
    result = service.upload(
        db,
        tenant.workspace_id,
        user_id=tenant.user.id,
        filename="采购合同.txt",
        content_type="text/plain",
        data=CONTRACT.encode("utf-8"),
    )

    assert result.status == "extracted"
    assert result.clause_count == 9
    assert result.high_risk_count >= 4
    assert result.risk_level == "high"
    contract = db.scalar(select(Contract).where(Contract.id == result.id))
    assert contract is not None
    assert len(db.scalars(select(ContractRisk).where(ContractRisk.contract_id == result.id)).all()) == result.risk_count


def test_upload_reports_duplicates_without_blocking(db, tenant):
    """合同改一版是常态，同一个文件重复传更常见。提示，不拦。"""
    first = service.upload(
        db, tenant.workspace_id, user_id=tenant.user.id,
        filename="合同.txt", content_type="text/plain", data=CONTRACT.encode("utf-8"),
    )
    second = service.upload(
        db, tenant.workspace_id, user_id=tenant.user.id,
        filename="合同(1).txt", content_type="text/plain", data=CONTRACT.encode("utf-8"),
    )

    assert first.duplicate_of is None
    assert second.duplicate_of == first.id


def test_upload_rejects_unparsable_file(db, tenant):
    with pytest.raises(service.ContractError) as exc:
        service.upload(
            db, tenant.workspace_id, user_id=tenant.user.id,
            filename="扫描件.docx", content_type="", data=b"not a zip",
        )

    assert exc.value.status_code == 415


def test_semantic_review_is_idempotent(db, tenant, monkeypatch):
    """重跑不能叠加：否则同一份合同的风险会越审越多。"""
    uploaded = service.upload(
        db, tenant.workspace_id, user_id=tenant.user.id,
        filename="合同.txt", content_type="text/plain", data=CONTRACT.encode("utf-8"),
    )
    payload = {
        "summary": "偏向我方不利",
        "risks": [
            {
                "title": "验收口径含糊",
                "detail": "需要明确",
                "quote": "以双方确认的订单为准",
                "severity": "medium",
                "suggestion": "写清标准",
            }
        ],
    }
    _stub_llm(monkeypatch, payload)

    first = service.review_semantic(db, tenant.workspace_id, uploaded.id)
    second = service.review_semantic(db, tenant.workspace_id, uploaded.id)

    assert first.added == 1
    assert second.added == 1
    assert first.risk_count == second.risk_count
    llm_rows = db.scalars(
        select(ContractRisk).where(
            ContractRisk.contract_id == uploaded.id, ContractRisk.source == "llm"
        )
    ).all()
    assert len(llm_rows) == 1


def test_approving_a_high_risk_contract_requires_a_comment(db, tenant):
    uploaded = service.upload(
        db, tenant.workspace_id, user_id=tenant.user.id,
        filename="合同.txt", content_type="text/plain", data=CONTRACT.encode("utf-8"),
    )

    with pytest.raises(service.ContractError) as exc:
        service.approve(
            db, tenant.workspace_id, uploaded.id,
            user_id=tenant.user.id, decision="approved", comment="   ",
        )

    assert exc.value.status_code == 422
    assert "高风险" in exc.value.message


def test_approval_snapshots_the_risks_it_saw(db, tenant):
    """规则改版后也要能说明"当时看到的是这几条"。"""
    uploaded = service.upload(
        db, tenant.workspace_id, user_id=tenant.user.id,
        filename="合同.txt", content_type="text/plain", data=CONTRACT.encode("utf-8"),
    )

    approval = service.approve(
        db, tenant.workspace_id, uploaded.id,
        user_id=tenant.user.id, decision="approved", comment="账期已与对方谈到 45 天，先用这一版签约",
    )

    assert approval.decision == "approved"
    assert len(approval.risk_snapshot) == uploaded.risk_count
    assert {"rule_key", "severity", "title", "quote"} <= set(approval.risk_snapshot[0])
    stored = db.scalar(select(ContractApproval).where(ContractApproval.id == approval.id))
    assert stored is not None


def test_contract_from_other_workspace_is_404(db, tenant):
    other = seed_tenant(db, slug="other", with_user=True)
    uploaded = service.upload(
        db, tenant.workspace_id, user_id=tenant.user.id,
        filename="合同.txt", content_type="text/plain", data=CONTRACT.encode("utf-8"),
    )

    with pytest.raises(service.ContractError) as exc:
        service.get(db, other.workspace_id, uploaded.id)

    assert exc.value.status_code == 404


def test_upload_endpoint_returns_201(engine, db, tenant):
    headers, stub = auth_payload(tenant)
    with client(engine, stub) as http:
        response = http.post(
            f"{BASE}/upload",
            headers=headers,
            files={"file": ("合同.txt", CONTRACT.encode("utf-8"), "text/plain")},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 201
    body = response.json()
    assert body["clause_count"] == 9
    assert body["risk_level"] == "high"


def test_upload_endpoint_rejects_bad_format_with_415(engine, db, tenant):
    headers, stub = auth_payload(tenant)
    with client(engine, stub) as http:
        response = http.post(
            f"{BASE}/upload",
            headers=headers,
            files={"file": ("合同.doc", b"\xd0\xcf\x11\xe0", "application/msword")},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 415


def test_summary_counts_by_level_and_rule_key(engine, db, tenant):
    service.upload(
        db, tenant.workspace_id, user_id=tenant.user.id,
        filename="a.txt", content_type="text/plain", data=CONTRACT.encode("utf-8"),
    )
    service.upload(
        db, tenant.workspace_id, user_id=tenant.user.id,
        filename="b.txt", content_type="text/plain", data=CLEAN_CONTRACT.encode("utf-8"),
    )
    headers, stub = auth_payload(tenant)

    with client(engine, stub) as http:
        response = http.get(f"{BASE}/summary", headers=headers)

    app.dependency_overrides.clear()

    body = response.json()
    assert body["contracts"] == 2
    assert body["pending_semantic"] == 2
    assert body["by_level"]["high"] == 1
    assert body["by_level"]["low"] == 1
    assert body["by_risk_key"][0]["count"] >= 1


def test_viewer_can_read_but_cannot_upload(engine, db, tenant):
    from types import SimpleNamespace

    from app.models import User, UserRole, WorkspaceMembership

    viewer = User(
        email="contract-viewer@example.com", display_name="v", role=UserRole.VIEWER, password_hash="x"
    )
    db.add(viewer)
    db.flush()
    db.add(
        WorkspaceMembership(
            workspace_id=tenant.workspace_id, user_id=viewer.id, role=UserRole.VIEWER
        )
    )
    db.commit()

    headers, _ = auth_payload(tenant)
    with client(engine, SimpleNamespace(id=viewer.id, role=UserRole.VIEWER)) as http:
        readable = http.get(f"{BASE}/summary", headers=headers)
        forbidden = http.post(
            f"{BASE}/upload",
            headers=headers,
            files={"file": ("合同.txt", CONTRACT.encode("utf-8"), "text/plain")},
        )

    app.dependency_overrides.clear()

    assert readable.status_code == 200
    assert forbidden.status_code == 403


def test_review_endpoint_reports_llm_not_configured(engine, db, tenant, monkeypatch):
    """模型没配要回 400 并说清楚，不是 500——这是配置问题，不是系统故障。"""
    from app.services.llm import LLMNotConfiguredError

    uploaded = service.upload(
        db, tenant.workspace_id, user_id=tenant.user.id,
        filename="合同.txt", content_type="text/plain", data=CONTRACT.encode("utf-8"),
    )

    def _boom(*_args, **_kwargs):
        raise LLMNotConfiguredError("LLM provider is not configured.")

    monkeypatch.setattr(semantic, "create_json_chat_completion", _boom)
    headers, stub = auth_payload(tenant)

    with client(engine, stub) as http:
        response = http.post(f"{BASE}/{uploaded.id}/review", headers=headers)

    app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "模型未配置" in response.json()["detail"]
