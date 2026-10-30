"""微调平台的出入参。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BuildRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sources: list[str] = Field(default_factory=lambda: ["qa", "cs"])
    limit: int = Field(default=200, ge=1, le=1000)


class SampleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    source_ref: str
    quality: str
    fingerprint: str
    tags: list[str] = Field(default_factory=list)
    messages: list[dict] = Field(default_factory=list)


class DatasetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    task_type: str
    status: str
    sample_count: int
    stats: dict = Field(default_factory=dict)
    builder_version: str
    exported_at: datetime | None = None
    created_at: datetime


class DatasetDetail(DatasetRead):
    samples: list[SampleRead] = Field(default_factory=list)


class JobCreate(BaseModel):
    dataset_id: int
    name: str = Field(min_length=1, max_length=200)
    base_model: str = Field(min_length=1, max_length=120)
    params: dict = Field(default_factory=dict)
    note: str = ""


class JobUpdate(BaseModel):
    status: str
    metrics: dict = Field(default_factory=dict)
    note: str = ""


class EvalCase(BaseModel):
    question: str
    base_score: float
    tuned_score: float
    note: str = ""


class EvalRequest(BaseModel):
    cases: list[EvalCase] = Field(default_factory=list)
    note: str = ""


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dataset_id: int
    name: str
    base_model: str
    params: dict = Field(default_factory=dict)
    status: str
    metrics: dict = Field(default_factory=dict)
    eval_summary: dict = Field(default_factory=dict)
    note: str
    finished_at: datetime | None = None
    created_at: datetime


class SummaryRead(BaseModel):
    datasets: int
    samples: int
    jobs: int
    by_source: list[dict]
    by_quality: list[dict]
    by_status: list[dict]
