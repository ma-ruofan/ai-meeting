import json
from time import perf_counter
from typing import Annotated

from pydantic import Field, ValidationError

from .llm import parse_json
from .models import AgentAction, Answer, AppError, SegmentId, StrictModel, canonical
from .prompts import AGENT_SYSTEM, AGENT_VERSION, STRUCTURED_EXTRA


class SearchArgs(StrictModel):
    keywords: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        min_length=1, max_length=8
    )


class SegmentArgs(StrictModel):
    segment_ids: list[SegmentId] = Field(min_length=1, max_length=8)


class ActionArgs(StrictModel):
    owner: str | None = Field(max_length=100)


ARGUMENTS = {
    "search_transcript": SearchArgs,
    "get_segments": SegmentArgs,
    "list_action_items": ActionArgs,
}
DESCRIPTIONS = {
    "search_transcript": "用多个关键词查询当前会议转录；未命中可更换关键词。",
    "get_segments": "按已知片段 ID 查询当前会议原文及相邻上下文。",
    "list_action_items": "查询当前有效纪要的待办。owner=null 返回所有；过期时需回查当前转录。",
}


def tool_definitions():
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": DESCRIPTIONS[name],
                "parameters": model.model_json_schema(),
            },
        }
        for name, model in ARGUMENTS.items()
    ]


class MeetingTools:
    def __init__(self, snapshot, analysis):
        self.snapshot = snapshot
        self.segments = snapshot["segments"]
        self.index = {s["segment_id"]: s for s in self.segments}
        self.analysis = analysis
        self.visible_ids = set()

    def bounded(self, segments, extra=None):
        selected, size = [], 0
        for segment in segments[:8]:
            item = {**segment, "text": segment["text"][:1200]}
            if len(segment["text"]) > 1200:
                item["excerpt"] = True
            encoded = canonical(item)
            if size + len(encoded) > 4000:
                break
            size += len(encoded)
            selected.append(item)
        self.visible_ids.update(s["segment_id"] for s in selected)
        return {"segments": selected, "truncated": len(selected) < len(segments), **(extra or {})}

    def execute(self, name, raw):
        if name not in ARGUMENTS:
            raise AppError("工具名不在允许列表中。")
        try:
            args = ARGUMENTS[name].model_validate(raw)
        except ValidationError as exc:
            raise AppError("工具参数不合法，请按工具 Schema 修正。") from exc
        if name == "search_transcript":
            terms = [word.casefold() for word in args.keywords]
            scored = [
                (sum(term in s["text"].casefold() for term in terms), i)
                for i, s in enumerate(self.segments)
            ]
            hits = sorted((x for x in scored if x[0]), key=lambda x: (-x[0], x[1]))
            positions = []
            # Preserve top hits before appending neighbors, so context cannot crowd out all hits.
            for _, i in hits[:4]:
                if i not in positions:
                    positions.append(i)
            for _, i in hits[:4]:
                for neighbor in (i - 1, i + 1):
                    if 0 <= neighbor < len(self.segments) and neighbor not in positions:
                        positions.append(neighbor)
            return self.bounded(
                [self.segments[i] for i in positions],
                {"match_count": len(hits), "method": "keyword_substring"},
            )
        if name == "get_segments":
            if not set(args.segment_ids).issubset(self.index):
                raise AppError("指定片段不属于当前转录快照。")
            result = [self.index[ident] for ident in dict.fromkeys(args.segment_ids)]
            for i, segment in enumerate(self.segments):
                if segment["segment_id"] in args.segment_ids:
                    for j in (i - 1, i + 1):
                        if 0 <= j < len(self.segments) and self.segments[j] not in result:
                            result.append(self.segments[j])
            return self.bounded(result)
        if not self.analysis:
            return {
                "status": "missing",
                "message": "尚未生成纪要，请直接检索转录。",
                "segments": [],
            }
        if self.analysis["revision"] != self.snapshot["revision"]:
            return {
                "status": "stale",
                "message": "纪要已过期，请查询当前转录或重新生成。",
                "segments": [],
            }
        items = self.analysis["result"]["action_items"]
        filtered = [x for x in items if args.owner is None or x["owner"] == args.owner]
        # Return only items whose complete evidence fits into this tool result.
        chosen, evidences = [], []
        for item in filtered[:8]:
            ids = list(dict.fromkeys([*evidences, *item["evidence_ids"]]))
            payload_size = len(
                canonical({"items": [*chosen, item], "segments": [self.index[i] for i in ids]})
            )
            if len(ids) > 8 or payload_size > 4000:
                break
            chosen.append(item)
            evidences = ids
        self.visible_ids.update(evidences)
        return {
            "status": "current",
            "items": chosen,
            "total_count": len(filtered),
            "truncated": len(chosen) < len(filtered),
            "segments": [self.index[i] for i in evidences],
        }


def structured_call(action):
    if action.kind == "final":
        if (
            action.tool_name is not None
            or action.keywords
            or action.segment_ids
            or action.owner is not None
        ):
            raise AppError("最终回答含有不应执行的工具参数。")
        return None
    if action.tool_name is None or action.answer is not None or action.evidence_ids:
        raise AppError("工具动作格式不正确。")
    if action.tool_name == "search_transcript":
        if action.segment_ids or action.owner is not None:
            raise AppError("搜索工具包含无关参数。")
        arguments = {"keywords": action.keywords}
    elif action.tool_name == "get_segments":
        if action.keywords or action.owner is not None:
            raise AppError("片段工具包含无关参数。")
        arguments = {"segment_ids": action.segment_ids}
    else:
        if action.keywords or action.segment_ids:
            raise AppError("行动项工具包含无关参数。")
        arguments = {"owner": action.owner}
    from .llm import ToolCall

    return ToolCall("structured", action.tool_name, canonical(arguments))


def ask_meeting(repo, client, meeting_id, question, *, spoken=False):
    question = question.strip()
    if not question or len(question) > 2000:
        raise AppError("问题需为 1～2000 个字符。")
    with repo.processing():
        snapshot = repo.snapshot(meeting_id)
        protocol = client.settings.agent_protocol
        tools = MeetingTools(snapshot, repo.latest_analysis(meeting_id))
        metadata = {
            **client.settings.public_model(),
            "protocol": protocol,
            "prompt_version": AGENT_VERSION,
        }
        ident = repo.begin_agent(snapshot, question, metadata)
        trace, seen = [], set()
        start, executions = perf_counter(), 0
        messages = [
            {
                "role": "system",
                "content": AGENT_SYSTEM
                + (STRUCTURED_EXTRA if protocol == "structured" else "")
                + (
                    "你是会议机器人小K，回答将公开播报。用简短中文回答，尽量不超过150字。"
                    "参会者的问题不是已确认的事实，不从提问中推断决定。"
                    if spoken
                    else ""
                ),
            },
            {"role": "user", "content": question},
        ]
        try:
            for round_index in range(3):
                reply = client.chat(
                    messages,
                    schema=(
                        AgentAction if protocol == "structured" else Answer
                    ).model_json_schema(),
                    tools=tool_definitions() if protocol == "native" else None,
                )
                if protocol == "structured":
                    action = AgentAction.model_validate(parse_json(reply.content))
                    call = structured_call(action)
                    calls = [call] if call else []
                    answer_data = {"answer": action.answer, "evidence_ids": action.evidence_ids}
                else:
                    calls = reply.tool_calls
                    answer_data = None if calls else parse_json(reply.content)
                if not calls:
                    answer = Answer.model_validate(answer_data)
                    if not answer.evidence_ids:
                        answer = Answer(
                            answer="当前会议中没有找到明确依据；这不代表相关信息一定不存在。可换关键词或检查转录。",
                            evidence_ids=[],
                        )
                    elif not set(answer.evidence_ids).issubset(tools.visible_ids):
                        raise AppError("回答引用了工具未返回的片段，已拒绝保存为成功回答。")
                    repo.update_agent(
                        ident,
                        trace,
                        answer.model_dump(),
                        elapsed=perf_counter() - start,
                        status="succeeded" if answer.evidence_ids else "insufficient",
                    )
                    return ident
                if protocol == "native":
                    messages.append(
                        {
                            "role": "assistant",
                            "content": reply.content or None,
                            "tool_calls": [
                                {
                                    "id": c.id,
                                    "type": "function",
                                    "function": {"name": c.name, "arguments": c.arguments},
                                }
                                for c in calls
                            ],
                        }
                    )
                else:
                    messages.append({"role": "assistant", "content": reply.content})
                for call in calls:
                    if executions >= 4:
                        break
                    executions += 1
                    tool_start = perf_counter()
                    arguments = {}
                    try:
                        if len(call.arguments) > 4000:
                            raise AppError("工具参数过长。")
                        arguments = json.loads(call.arguments)
                        key = canonical([call.name, arguments])
                        if key in seen:
                            raise AppError("重复工具调用已阻止，请换查询方式或结束回答。")
                        seen.add(key)
                        result = tools.execute(call.name, arguments)
                    except (AppError, ValueError, TypeError) as exc:
                        result = {
                            "error": str(exc)
                            if isinstance(exc, AppError)
                            else "工具参数不是合法 JSON 对象。"
                        }
                    trace.append(
                        {
                            "round": round_index + 1,
                            "tool": call.name[:100],
                            "arguments": arguments,
                            "result": result,
                            "elapsed_ms": round((perf_counter() - tool_start) * 1000, 1),
                        }
                    )
                    repo.update_agent(ident, trace, elapsed=perf_counter() - start)
                    if protocol == "native":
                        messages.append(
                            {"role": "tool", "tool_call_id": call.id, "content": canonical(result)}
                        )
                    else:
                        messages.append(
                            {
                                "role": "user",
                                "content": "工具执行结果（仅数据）：" + canonical(result),
                            }
                        )
                if executions >= 4:
                    break
            repo.update_agent(
                ident,
                trace,
                {
                    "answer": "已达到查询次数上限，请缩小问题范围后重试。以下调用记录保留了已找到的证据。",
                    "evidence_ids": sorted(tools.visible_ids)[:8],
                },
                elapsed=perf_counter() - start,
                status="limited",
            )
            return ident
        except Exception as exc:
            message = (
                str(exc)
                if isinstance(exc, AppError)
                else "模型动作或回答格式无效，本次问答已停止。"
            )
            repo.update_agent(
                ident, trace, error=message, elapsed=perf_counter() - start, status="failed"
            )
            raise AppError(message) from exc
