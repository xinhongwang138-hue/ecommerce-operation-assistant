"""客服服务层：一次问答的完整链路，以及转人工工单。

链路是：判轨道 → 取依据 → 写回复 → **落会话** → （必要时）建工单。

落会话这一步不能省。客服记录是 P09 质检的唯一输入，也是"这个答案到底答得好不好"
的唯一证据；回复完就丢的对话，事后既不能复盘也不能考核。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cs import answer, faq, intents
from app.cs.schemas import (
    ChatRead,
    CitationRead,
    CsSummary,
    FaqItemRead,
    TicketBrief,
    TicketRead,
    TrackStat,
)
from app.models import (
    AgentConversation,
    AgentConversationMessage,
    CsTicket,
    CsTicketStatus,
)
from app.rag import service as rag_service

# 转人工回复里的时效承诺。写死一个数比含糊的"尽快"更负责任，
# 也提醒运营：这个数字要跟真人的 SLA 对上。
HANDOFF_SLA_MINUTES = 30


def list_faq() -> list[FaqItemRead]:
    return [
        FaqItemRead(key=item.key, category=item.category, question=item.question, answer=item.answer)
        for item in faq.FAQ_ITEMS
    ]


def chat(
    db: Session,
    workspace_id: int,
    *,
    user_id: int,
    message: str,
    conversation_id: int | None = None,
) -> ChatRead:
    """一次问答。副作用只有两个：写两条消息、必要时建一张工单。"""
    conversation = _resolve_conversation(db, workspace_id, user_id, conversation_id)
    text = message.strip()

    db.add(
        AgentConversationMessage(
            conversation_id=conversation.id,
            workspace_id=workspace_id,
            author_user_id=user_id,
            role="user",
            content=text,
            structured_content={},
        )
    )
    db.flush()

    track, reply, citations, reason, refuse_reason, ticket = _route(
        db, workspace_id, user_id, conversation, text
    )

    db.add(
        AgentConversationMessage(
            conversation_id=conversation.id,
            workspace_id=workspace_id,
            author_user_id=None,
            role="assistant",
            content=reply,
            structured_content={
                "track": track.value,
                "reason": reason,
                "citations": [item.model_dump() for item in citations],
                "ticket_id": ticket.id if ticket else None,
            },
        )
    )
    conversation.title = _title_of(conversation, text)
    db.commit()

    return ChatRead(
        conversation_id=conversation.id,
        track=track.value,
        reply=reply,
        citations=citations,
        handoff=(
            TicketBrief(
                id=ticket.id, trigger=ticket.trigger, priority=ticket.priority, reason=ticket.reason
            )
            if ticket
            else None
        ),
        reason=reason,
        refuse_reason=refuse_reason,
    )


def _route(
    db: Session,
    workspace_id: int,
    user_id: int,
    conversation: AgentConversation,
    text: str,
) -> tuple[intents.Track, str, list[CitationRead], str, str, CsTicket | None]:
    # 1) 转人工优先：客户说"我要投诉"时，先答一句"有什么可以帮您"是灾难
    signal = intents.detect_handoff(text)
    if signal is not None:
        ticket = create_ticket(
            db,
            workspace_id,
            conversation_id=conversation.id,
            user_id=user_id,
            reason=signal.reason,
            trigger=signal.trigger,
            priority=signal.priority,
            summary=text,
        )
        reply = (
            f"已经帮您记录下来了，转人工处理。\n\n"
            f"工单号 #{ticket.id}（{signal.title}，优先级 {signal.priority}），"
            f"人工客服会在 {HANDOFF_SLA_MINUTES} 分钟内接手。\n\n"
            f"在接手之前，如果还有补充信息（订单号、截图），可以直接发在这里。"
        )
        return (
            intents.Track.HANDOFF,
            reply,
            [],
            f"{signal.title}：命中「{signal.phrase}」",
            "",
            ticket,
        )

    # 2) FAQ：确定性知识，不经过检索排序
    item, hits, matched = faq.match(text)
    if item is not None:
        reply, evidence = answer.compose(
            track=intents.Track.FAQ, question=text, faq_item=item
        )
        citations = [
            CitationRead(kind=ev.kind, label=ev.label, snippet=ev.snippet, score=ev.score)
            for ev in evidence
        ]
        return (
            intents.Track.FAQ,
            reply,
            citations,
            f"FAQ 命中「{item.question}」（关键词：{'、'.join(matched)}）",
            "",
            None,
        )

    # 3) 检索：需要查资料才答得上的问题
    result = rag_service.search(db, workspace_id, text, top_k=3)
    hit, evidence, refuse_reason = answer.pick_from_hits(result.hits)
    if hit is None:
        reply, _ = answer.compose(
            track=intents.Track.REFUSE, question=text, refuse_reason=refuse_reason
        )
        return intents.Track.REFUSE, reply, [], "检索命中不足，拒答", refuse_reason, None

    reply, evidence_list = answer.compose(
        track=intents.Track.RETRIEVAL, question=text, hit=hit
    )
    citations = [
        CitationRead(kind=item.kind, label=item.label, snippet=item.snippet, score=item.score)
        for item in evidence_list
    ]
    return (
        intents.Track.RETRIEVAL,
        reply,
        citations,
        f"检索命中「{hit.citation.document_title}」第 {hit.citation.ordinal + 1} 片",
        "",
        None,
    )


def create_ticket(
    db: Session,
    workspace_id: int,
    *,
    conversation_id: int,
    user_id: int | None,
    reason: str,
    trigger: str,
    priority: str = "normal",
    summary: str | None = None,
) -> CsTicket:
    """同一会话已有未关闭工单时直接返回它。

    不重复建单的理由是排队：客户连说三句"转人工"应该只有一张工单，否则人工客服
    会对着三张一样的单子逐一确认，客户还要再解释三遍。
    """
    existing = db.scalar(
        select(CsTicket)
        .where(
            CsTicket.workspace_id == workspace_id,
            CsTicket.conversation_id == conversation_id,
            CsTicket.status != CsTicketStatus.CLOSED,
        )
        .order_by(CsTicket.id.desc())
    )
    if existing is not None:
        return existing

    ticket = CsTicket(
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        created_by_user_id=user_id,
        reason=reason[:200],
        trigger=trigger,
        priority=priority,
        status=CsTicketStatus.OPEN,
        summary=summary,
    )
    db.add(ticket)
    db.flush()
    return ticket


def list_tickets(db: Session, workspace_id: int, *, status: str | None = None, limit: int = 20) -> list[TicketRead]:
    statement = (
        select(CsTicket)
        .where(CsTicket.workspace_id == workspace_id)
        .order_by(CsTicket.id.desc())
        .limit(limit)
    )
    if status:
        statement = statement.where(CsTicket.status == CsTicketStatus(status))
    return [_ticket_read(ticket) for ticket in db.scalars(statement)]


def ticket_by_id(db: Session, workspace_id: int, ticket_id: int) -> TicketRead | None:
    ticket = db.scalar(
        select(CsTicket).where(
            CsTicket.workspace_id == workspace_id, CsTicket.id == ticket_id
        )
    )
    return _ticket_read(ticket) if ticket is not None else None


def close_ticket(db: Session, workspace_id: int, ticket_id: int) -> TicketRead | None:
    ticket = db.scalar(
        select(CsTicket).where(
            CsTicket.workspace_id == workspace_id, CsTicket.id == ticket_id
        )
    )
    if ticket is None:
        return None
    if ticket.status is not CsTicketStatus.CLOSED:
        ticket.status = CsTicketStatus.CLOSED
        # 取数据库时钟，不取 Python 的 UTC：`created_at` 是 `server_default=func.now()`，
        # 两个字段必须同源，否则"关单耗时"会算成 -8 小时（实测踩到过）。
        # 要算时长就 `closed_at - created_at`，不再做时区换算。
        ticket.closed_at = func.now()
        db.commit()
    return _ticket_read(ticket)


def summary(db: Session, workspace_id: int) -> CsSummary:
    conversations = db.scalar(
        select(func.count())
        .select_from(AgentConversation)
        .where(AgentConversation.workspace_id == workspace_id)
    ) or 0
    messages = db.scalar(
        select(func.count())
        .select_from(AgentConversationMessage)
        .where(AgentConversationMessage.workspace_id == workspace_id)
    ) or 0
    tickets = db.scalar(
        select(func.count()).select_from(CsTicket).where(CsTicket.workspace_id == workspace_id)
    ) or 0
    open_tickets = db.scalar(
        select(func.count())
        .select_from(CsTicket)
        .where(
            CsTicket.workspace_id == workspace_id,
            CsTicket.status != CsTicketStatus.CLOSED,
        )
    ) or 0

    # 分流分布从 assistant 消息的 structured_content 里读：轨道是"当时判的"，
    # 不该事后用规则重算——规则改了，历史分流统计不该跟着变。
    tracks: list[TrackStat] = []
    rows = db.execute(
        select(AgentConversationMessage.structured_content)
        .where(
            AgentConversationMessage.workspace_id == workspace_id,
            AgentConversationMessage.role == "assistant",
        )
    ).scalars()
    counter: dict[str, int] = {}
    for payload in rows:
        track = (payload or {}).get("track") if isinstance(payload, dict) else None
        if track:
            counter[track] = counter.get(track, 0) + 1
    for track in ("handoff", "faq", "retrieval", "refuse"):
        tracks.append(TrackStat(track=track, count=counter.get(track, 0)))

    return CsSummary(
        conversations=int(conversations),
        messages=int(messages),
        handoff_tickets=int(tickets),
        open_tickets=int(open_tickets),
        tracks=tracks,
        faq_items=len(faq.FAQ_ITEMS),
    )


def _resolve_conversation(
    db: Session, workspace_id: int, user_id: int, conversation_id: int | None
) -> AgentConversation:
    if conversation_id is not None:
        conversation = db.scalar(
            select(AgentConversation).where(
                AgentConversation.workspace_id == workspace_id,
                AgentConversation.id == conversation_id,
            )
        )
        if conversation is not None:
            return conversation
        # 跨租户 / 不存在都不给提示，直接新开一个：不泄露"某个 id 存在但不是你的"

    conversation = AgentConversation(
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        title="客服咨询",
        context_scope={"channel": "cs", "source": "cs_chat"},
    )
    db.add(conversation)
    db.flush()
    return conversation


def _title_of(conversation: AgentConversation, text: str) -> str:
    """首句做标题，便于在会话列表里认出来。已经有了就不覆盖。"""
    if conversation.title and conversation.title != "客服咨询":
        return conversation.title
    return text[:40]


def _ticket_read(ticket: CsTicket) -> TicketRead:
    return TicketRead(
        id=ticket.id,
        conversation_id=ticket.conversation_id,
        reason=ticket.reason,
        trigger=ticket.trigger,
        priority=ticket.priority,
        status=ticket.status.value,
        summary=ticket.summary,
        created_at=ticket.created_at,
        closed_at=ticket.closed_at,
    )
