"""微调平台服务层：收集样本 → 建数据集 → 导出 → 登记任务 → 记评测。

有两条不容妥协：

**一，样本必须可追溯。** 每条 `source_ref` 都指回原始记录（`qa_session:12`）。
训练数据说不清来源，就没法回答"模型为什么会这么说"，也没法在下游投诉时撤样本。

**二，不假装在训练。** 任务的 `status` 只能由人推进或外部服务回调推进，
`metrics` 与 `eval_summary` 都是台账。平台上不会出现假的进度条和假的 loss 曲线。
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.finetune import builders
from app.finetune.schemas import (
    CallbackRequest,
    DatasetDetail,
    DatasetRead,
    EvalRequest,
    JobCreate,
    JobRead,
    JobUpdate,
    SampleRead,
    SummaryRead,
)
from app.models import (
    AgentConversation,
    AgentConversationMessage,
    FinetuneDataset,
    FinetuneDatasetStatus,
    FinetuneJob,
    FinetuneJobStatus,
    FinetuneSample,
    QaMessage,
    QaScore,
    QaSession,
)

JOB_STATUSES = tuple(item.value for item in FinetuneJobStatus)
SOURCE_TITLES = {"qa": "销售考核会话", "cs": "AI 客服会话"}
JOB_STATUS_TITLES = {
    "registered": "已登记",
    "running": "训练中",
    "succeeded": "已完成",
    "failed": "失败",
}


class FinetuneError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def build_dataset(db: Session, workspace_id: int, *, user_id: int | None, name: str, sources: list[str], limit: int) -> DatasetDetail:
    sources = [item for item in sources if item in ("qa", "cs")]
    if not sources:
        raise FinetuneError("sources 只接受 qa / cs", status_code=400)

    drafts: list[builders.Draft] = []
    # 被丢掉的样本要记原因。静默丢弃是数据平台最坏的失败方式：
    # 指标上只看到"这次只有 13 条"，没人知道另 11 条客服会话为什么没进来。
    skipped: Counter[str] = Counter()
    if "qa" in sources:
        got, drop = _from_qa(db, workspace_id, limit)
        drafts.extend(got)
        skipped.update(drop)
    if "cs" in sources:
        got, drop = _from_cs(db, workspace_id, limit)
        drafts.extend(got)
        skipped.update(drop)

    # 去重在这里做，不能指望训练框架：同一句模板话术进三次，模型就只学会那一句。
    #
    # 顺序是先 qa 后 cs，这决定了"重复时留下谁"：P09 的质检会话带分数、等级和红线标记，
    # 标签比 P01 的轨道更硬，所以留 qa 那条。同一条对话在两个模块各存一份是常态
    # （实测本机 11 条客服会话与质检会话逐字相同），不是异常。
    seen: set[str] = set()
    unique: list[builders.Draft] = []
    dropped_by_source: Counter[str] = Counter()
    for draft in drafts:
        fingerprint = draft.fingerprint()
        if fingerprint in seen:
            dropped_by_source[draft.source] += 1
            continue
        seen.add(fingerprint)
        unique.append(draft)

    if not unique:
        raise FinetuneError(
            "没有可用的样本。先去 P09 导入会话、或用 P01 聊几句，再回来建数据集。",
            status_code=422,
        )

    stats = _stats(unique)
    stats["duplicates"] = sum(dropped_by_source.values())
    stats["duplicates_by_source"] = dict(dropped_by_source)
    stats["skipped"] = dict(skipped)

    dataset = FinetuneDataset(
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        name=name[:200],
        task_type="sft_chat",
        status=FinetuneDatasetStatus.READY,
        sample_count=len(unique),
        builder_version=builders.BUILDER_VERSION,
        stats=stats,
    )
    db.add(dataset)
    db.flush()

    db.add_all(
        [
            FinetuneSample(
                workspace_id=workspace_id,
                dataset_id=dataset.id,
                source=draft.source,
                source_ref=draft.source_ref,
                quality=draft.quality,
                messages=draft.messages,
                fingerprint=draft.fingerprint(),
                tags=draft.tags,
            )
            for draft in unique
        ]
    )
    db.commit()
    return get_dataset(db, workspace_id, dataset.id)


def get_dataset(db: Session, workspace_id: int, dataset_id: int) -> DatasetDetail:
    dataset = db.scalar(
        select(FinetuneDataset)
        .where(FinetuneDataset.id == dataset_id, FinetuneDataset.workspace_id == workspace_id)
        .options(selectinload(FinetuneDataset.samples))
    )
    if dataset is None:
        raise FinetuneError("数据集不存在", status_code=404)
    return DatasetDetail(
        **_dataset_dict(dataset),
        samples=[
            SampleRead.model_validate(item)
            for item in sorted(dataset.samples, key=lambda row: row.id)
        ],
    )


def list_datasets(db: Session, workspace_id: int, *, limit: int = 20) -> list[DatasetRead]:
    statement = (
        select(FinetuneDataset)
        .where(FinetuneDataset.workspace_id == workspace_id)
        .order_by(FinetuneDataset.id.desc())
        .limit(limit)
    )
    return [DatasetRead(**_dataset_dict(item)) for item in db.scalars(statement)]


def export_jsonl(db: Session, workspace_id: int, dataset_id: int) -> tuple[str, str]:
    """导出 JSONL 文本。只有正向与边界样本进训练集，待修样本要人工改完才能用。

    这一点必须显式：把待修样本直接拿去训，等于教模型学错误的回答。
    """
    dataset = get_dataset(db, workspace_id, dataset_id)
    usable = [item for item in dataset.samples if item.quality != builders.QUALITY_NEEDS_FIX]
    if not usable:
        raise FinetuneError("没有可用于训练的样本（待修样本需要先人工改）", status_code=422)

    lines = [builders.to_jsonl_line(item.messages) for item in usable]
    payload = "\n".join(lines) + "\n"

    dataset_row = db.scalar(select(FinetuneDataset).where(FinetuneDataset.id == dataset_id))
    if dataset_row is not None:
        dataset_row.status = FinetuneDatasetStatus.EXPORTED
        dataset_row.exported_at = func.now()
        db.commit()

    filename = f"dataset-{dataset_id}-{dataset.sample_count}samples.jsonl"
    return filename, payload


def register_job(db: Session, workspace_id: int, *, payload: JobCreate) -> JobRead:
    dataset = db.scalar(
        select(FinetuneDataset).where(
            FinetuneDataset.id == payload.dataset_id,
            FinetuneDataset.workspace_id == workspace_id,
        )
    )
    if dataset is None:
        raise FinetuneError("数据集不存在", status_code=404)

    job = FinetuneJob(
        workspace_id=workspace_id,
        dataset_id=dataset.id,
        name=payload.name[:200],
        base_model=payload.base_model[:120],
        params=payload.params or {},
        # 登记态。真正训练由外部服务做，这里只记台账
        status=FinetuneJobStatus.REGISTERED,
        note=payload.note,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return JobRead.model_validate(job)


def update_job(db: Session, workspace_id: int, job_id: int, *, payload: JobUpdate) -> JobRead:
    if payload.status not in JOB_STATUSES:
        raise FinetuneError(f"status 只接受 {'/'.join(JOB_STATUSES)}", status_code=400)
    job = _load_job(db, workspace_id, job_id)

    job.status = FinetuneJobStatus(payload.status)
    if payload.metrics:
        job.metrics = payload.metrics
    if payload.note:
        job.note = payload.note
    if payload.status in (FinetuneJobStatus.SUCCEEDED.value, FinetuneJobStatus.FAILED.value):
        job.finished_at = func.now()
    db.commit()
    db.refresh(job)
    return JobRead.model_validate(job)


# 状态机。终态是终态：同一个作业不重复跑，要重跑就再登记一条，旧的留在台账里。
# 允许"直接 registered → failed"是因为启动训练器立刻失败（命令不存在、显存不够）
# 也是一种真实结局，不该逼着先改成 running 再改 failed 去凑一个不存在的中间态。
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "registered": {"running", "failed"},
    "running": {"succeeded", "failed"},
    "succeeded": set(),
    "failed": set(),
}


def get_job(db: Session, workspace_id: int, job_id: int) -> JobRead:
    return JobRead.model_validate(_load_job(db, workspace_id, job_id))


def apply_callback(db: Session, job_id: int, *, payload: CallbackRequest) -> JobRead:
    """外部训练服务的回写。

    三件事值得单独说：

    **幂等。** 训练框架的重试、网络超时重发都会让同一个状态到达两次。第二次什么都不改——
    尤其是 `metrics`，不能被后来的空 payload 覆盖成 `{}`。

    **状态机。** 已完成的任务被回写成 running，说明两边对同一件事的认知不一致，
    宁可 409 报出来，也不要让台账悄悄改口。

    **不认租户。** 这条路靠 HMAC 认人，作业 id 就是它知道的一切；
    跨租户的读取仍然走用户身份那套。
    """
    job = db.scalar(select(FinetuneJob).where(FinetuneJob.id == job_id))
    if job is None:
        raise FinetuneError("任务不存在", status_code=404)
    if payload.status not in JOB_STATUSES:
        raise FinetuneError(f"status 只接受 {'/'.join(JOB_STATUSES)}", status_code=400)

    current = job.status.value
    if payload.status == current:
        # 重复回写：不改任何字段，包括 metrics 与 finished_at
        return JobRead.model_validate(job)
    if payload.status not in ALLOWED_TRANSITIONS[current]:
        raise FinetuneError(
            f"任务已经是 {current}，不能再变成 {payload.status}；要重跑请新登记一条",
            status_code=409,
        )

    job.status = FinetuneJobStatus(payload.status)
    if payload.metrics:
        job.metrics = payload.metrics
    if payload.note:
        job.note = payload.note
    if payload.status in (FinetuneJobStatus.SUCCEEDED.value, FinetuneJobStatus.FAILED.value):
        job.finished_at = func.now()
    db.commit()
    db.refresh(job)
    return JobRead.model_validate(job)


def record_eval(db: Session, workspace_id: int, job_id: int, *, payload: EvalRequest) -> JobRead:
    """记一批对比结果。

    算的是**同一批题**上的平均分差。没有同一批题，"微调后变好了"就是一句空话：
    换一批题，分数涨跌可以归因到任何东西。
    """
    if not payload.cases:
        raise FinetuneError("至少要有一条评测用例", status_code=422)
    job = _load_job(db, workspace_id, job_id)

    base = sum(item.base_score for item in payload.cases) / len(payload.cases)
    tuned = sum(item.tuned_score for item in payload.cases) / len(payload.cases)
    wins = sum(1 for item in payload.cases if item.tuned_score > item.base_score)
    losses = sum(1 for item in payload.cases if item.tuned_score < item.base_score)

    job.eval_summary = {
        "cases": len(payload.cases),
        "base_average": round(base, 2),
        "tuned_average": round(tuned, 2),
        "delta": round(tuned - base, 2),
        "wins": wins,
        "losses": losses,
        "ties": len(payload.cases) - wins - losses,
        "note": payload.note,
        "detail": [
            {
                "question": item.question,
                "base_score": item.base_score,
                "tuned_score": item.tuned_score,
                "delta": round(item.tuned_score - item.base_score, 2),
                "note": item.note,
            }
            for item in payload.cases
        ],
    }
    db.commit()
    db.refresh(job)
    return JobRead.model_validate(job)


def list_jobs(db: Session, workspace_id: int, *, limit: int = 20) -> list[JobRead]:
    statement = (
        select(FinetuneJob)
        .where(FinetuneJob.workspace_id == workspace_id)
        .order_by(FinetuneJob.id.desc())
        .limit(limit)
    )
    return [JobRead.model_validate(item) for item in db.scalars(statement)]


def summary(db: Session, workspace_id: int) -> SummaryRead:
    datasets = list(
        db.scalars(
            select(FinetuneDataset)
            .where(FinetuneDataset.workspace_id == workspace_id)
            .options(selectinload(FinetuneDataset.samples))
        ).unique()
    )
    samples = [item for dataset in datasets for item in dataset.samples]
    jobs = list(
        db.scalars(select(FinetuneJob).where(FinetuneJob.workspace_id == workspace_id))
    )

    return SummaryRead(
        datasets=len(datasets),
        samples=len(samples),
        jobs=len(jobs),
        by_source=_counter([item.source for item in samples], SOURCE_TITLES),
        by_quality=_counter(
            [item.quality for item in samples],
            {key: value for key, value in builders.QUALITY_TITLES.items()},
        ),
        by_status=_counter([item.status.value for item in jobs], JOB_STATUS_TITLES),
    )


def source_titles() -> dict[str, str]:
    return dict(SOURCE_TITLES)


def _from_qa(
    db: Session, workspace_id: int, limit: int
) -> tuple[list[builders.Draft], Counter[str]]:
    rows = db.scalars(
        select(QaSession)
        .where(QaSession.workspace_id == workspace_id)
        .options(selectinload(QaSession.messages), selectinload(QaSession.score))
        .order_by(QaSession.id.desc())
        .limit(limit)
    ).unique()

    drafts: list[builders.Draft] = []
    skipped: Counter[str] = Counter()
    for session in rows:
        score = session.score
        if score is None:
            # 还没评分就不知道这次服务好不好，不能当正向样本也不能当边界样本
            skipped["qa_unscored"] += 1
            continue
        utterances = [
            (item.role.value, item.content)
            for item in sorted(session.messages, key=lambda row: row.seq)
        ]
        draft = builders.from_qa_session(
            session_id=session.id,
            utterances=utterances,
            grade=score.grade,
            redline=score.redline,
        )
        if draft is None:
            skipped["qa_too_short_or_unfinished"] += 1
            continue
        drafts.append(draft)
    return drafts, skipped


def _from_cs(
    db: Session, workspace_id: int, limit: int
) -> tuple[list[builders.Draft], Counter[str]]:
    """客服会话没有 ORM 关系，按 conversation_id 分批取消息。

    两次查询而不是 join：会话数量本来就是 limit 量级，join 会把消息行放大成
    笛卡尔积，再在内存里裁——为了少一次查询换来 N 倍行数是亏的。
    """
    conversations = list(
        db.scalars(
            select(AgentConversation)
            .where(AgentConversation.workspace_id == workspace_id)
            .order_by(AgentConversation.id.desc())
            .limit(limit)
        )
    )
    if not conversations:
        return [], Counter()

    message_rows = db.scalars(
        select(AgentConversationMessage)
        .where(AgentConversationMessage.conversation_id.in_([item.id for item in conversations]))
        .order_by(AgentConversationMessage.id)
    )
    grouped: dict[int, list] = {}
    for row in message_rows:
        grouped.setdefault(row.conversation_id, []).append(row)

    drafts: list[builders.Draft] = []
    skipped: Counter[str] = Counter()
    for conversation in conversations:
        messages = grouped.get(conversation.id, [])
        utterances = [
            ("customer" if item.role == "user" else "agent", item.content) for item in messages
        ]
        # 轨道记在最后一条 assistant 的结构化内容里
        track = ""
        for item in reversed(messages):
            if item.role == "assistant" and isinstance(item.structured_content, dict):
                track = str(item.structured_content.get("track") or "")
                break
        if track not in ("faq", "retrieval", "refuse", "handoff"):
            # 没有轨道就分不清这条是"答对了"还是"该停没停"，质量分层无从下手。
            # 早期会话或直接写库的数据会落到这里，必须计数，否则看起来像"客服侧没数据"。
            skipped["cs_missing_track"] += 1
            continue
        draft = builders.from_cs_conversation(
            conversation_id=conversation.id, utterances=utterances, track=track
        )
        if draft is None:
            skipped["cs_too_short_or_unfinished"] += 1
            continue
        drafts.append(draft)
    return drafts, skipped


def _stats(drafts: list[builders.Draft]) -> dict:
    sources = Counter(item.source for item in drafts)
    qualities = Counter(item.quality for item in drafts)
    lengths = [item.chars() for item in drafts]
    return {
        "by_source": dict(sources),
        "by_quality": dict(qualities),
        "average_chars": round(sum(lengths) / len(lengths), 1) if lengths else 0,
        "max_chars": max(lengths) if lengths else 0,
        "min_chars": min(lengths) if lengths else 0,
        "builder_version": builders.BUILDER_VERSION,
    }


def _dataset_dict(dataset: FinetuneDataset) -> dict:
    return {
        "id": dataset.id,
        "name": dataset.name,
        "task_type": dataset.task_type,
        "status": dataset.status.value,
        "sample_count": dataset.sample_count,
        "stats": dataset.stats or {},
        "builder_version": dataset.builder_version,
        "exported_at": dataset.exported_at,
        "created_at": dataset.created_at,
    }


def _load_job(db: Session, workspace_id: int, job_id: int) -> FinetuneJob:
    job = db.scalar(
        select(FinetuneJob).where(
            FinetuneJob.id == job_id, FinetuneJob.workspace_id == workspace_id
        )
    )
    if job is None:
        raise FinetuneError("训练任务不存在", status_code=404)
    return job


def _counter(values: list[str], titles: dict[str, str]) -> list[dict]:
    counts = Counter(values)
    return [
        {"key": key, "title": titles.get(key, key), "count": count}
        for key, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def fingerprint_of(messages: list[dict]) -> str:
    payload = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
