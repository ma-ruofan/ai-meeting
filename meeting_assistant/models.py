import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AppError(Exception):
    """A safe, user-readable error; do not wrap raw credential-bearing responses."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


ShortText = Annotated[str, Field(min_length=1, max_length=2000)]
SegmentId = Annotated[str, Field(pattern=r"^S[0-9]{3,6}$")]


class Segment(StrictModel):
    segment_id: SegmentId
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def check_range(self):
        if self.end <= self.start:
            raise ValueError("片段结束时间必须晚于开始时间")
        return self


class Decision(StrictModel):
    content: ShortText
    evidence_ids: list[SegmentId] = Field(min_length=1, max_length=8)


class ActionItem(StrictModel):
    task: ShortText
    owner: str | None = Field(max_length=100)
    deadline_text: str | None = Field(max_length=100)
    evidence_ids: list[SegmentId] = Field(min_length=1, max_length=8)


class Minutes(StrictModel):
    summary: str = Field(min_length=1, max_length=5000)
    decisions: list[Decision] = Field(max_length=30)
    action_items: list[ActionItem] = Field(max_length=50)


class Answer(StrictModel):
    answer: ShortText
    evidence_ids: list[SegmentId] = Field(max_length=8)


class AgentAction(StrictModel):
    kind: Literal["tool", "final"]
    tool_name: Literal["search_transcript", "get_segments", "list_action_items"] | None
    keywords: list[str] = Field(max_length=8)
    segment_ids: list[SegmentId] = Field(max_length=8)
    owner: str | None
    answer: str | None
    evidence_ids: list[SegmentId] = Field(max_length=8)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def validate_segments(raw, duration):
    if not raw or len(raw) > 2000:
        raise AppError("转录必须包含 1～2000 个有效片段。")
    segments = [Segment.model_validate(x) for x in raw]
    if len({s.segment_id for s in segments}) != len(segments):
        raise AppError("转录片段 ID 不能重复。")
    if any(s.end > duration + 0.5 for s in segments):
        raise AppError("片段时间超过音频时长。")
    if any(b.start < a.start for a, b in zip(segments, segments[1:])):
        raise AppError("转录片段必须按开始时间排序。")
    return [s.model_dump() for s in segments]


def validate_evidence(minutes, segments):
    valid = {s["segment_id"] for s in segments}
    for item in [*minutes.decisions, *minutes.action_items]:
        if not set(item.evidence_ids).issubset(valid):
            raise AppError("模型引用了输入快照中不存在的片段。")


def timestamp(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    return f"{minutes:02d}:{seconds:02d}"
