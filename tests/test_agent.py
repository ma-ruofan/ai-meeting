import json

import pytest
from conftest import FakeClient, final_action, tool_action

from meeting_assistant.agent import MeetingTools, ask_meeting
from meeting_assistant.llm import Reply, ToolCall
from meeting_assistant.models import AppError
from meeting_assistant.services import generate_minutes


def test_real_tool_execution_then_evidence_answer(repo, meeting):
    client = FakeClient([tool_action(keywords=["文档", "负责人"]), final_action(ids=["S005"])])
    ask_meeting(repo, client, meeting, "文档谁负责？")
    run = repo.agent_runs(meeting)[0]
    assert run["status"] == "succeeded"
    assert len(run["trace"]) == 1
    assert run["trace"][0]["result"]["segments"]
    assert run["result"]["evidence_ids"] == ["S005"]


def test_unobserved_evidence_is_rejected(repo, meeting):
    client = FakeClient(
        [tool_action(name="get_segments", ids=["S001"]), final_action(ids=["S008"])]
    )
    with pytest.raises(AppError, match="未返回"):
        ask_meeting(repo, client, meeting, "谁负责？")
    assert repo.agent_runs(meeting)[0]["status"] == "failed"
    assert repo.agent_runs(meeting)[0]["result"] is None


def test_unsupported_answer_replaced_by_insufficient(repo, meeting):
    ask_meeting(repo, FakeClient([final_action("预算已经是十万元。")]), meeting, "预算多少？")
    result = repo.agent_runs(meeting)[0]
    assert result["status"] == "insufficient"
    assert "十万元" not in result["result"]["answer"]


def test_three_round_limit_and_duplicate_guard(repo, meeting):
    action = tool_action(keywords=["预算"])
    client = FakeClient([action, action, action])
    ask_meeting(repo, client, meeting, "预算？")
    run = repo.agent_runs(meeting)[0]
    assert client.calls == 3
    assert run["status"] == "limited"
    assert "重复" in run["trace"][1]["result"]["error"]


def test_four_tool_budget_native(repo, meeting):
    calls = [
        ToolCall(str(i), "get_segments", json.dumps({"segment_ids": [f"S00{i + 1}"]}))
        for i in range(6)
    ]
    client = FakeClient([Reply("", calls)], protocol="native")
    ask_meeting(repo, client, meeting, "全面查询")
    run = repo.agent_runs(meeting)[0]
    assert len(run["trace"]) == 4
    assert run["status"] == "limited"


def test_unknown_tool_is_not_executed(repo, meeting):
    client = FakeClient(
        [
            Reply("", [ToolCall("1", "run_shell", '{"command":"rm -rf /"}')]),
            {"answer": "没有依据", "evidence_ids": []},
        ],
        protocol="native",
    )
    ask_meeting(repo, client, meeting, "检查会议")
    run = repo.agent_runs(meeting)[0]
    assert "允许列表" in run["trace"][0]["result"]["error"]
    assert run["status"] == "insufficient"


def test_native_call_and_final(repo, meeting):
    client = FakeClient(
        [
            Reply("", [ToolCall("1", "get_segments", '{"segment_ids":["S006"]}')]),
            {"answer": "预算还没有确认。", "evidence_ids": ["S006"]},
        ],
        protocol="native",
    )
    ask_meeting(repo, client, meeting, "预算确认了吗？")
    assert repo.agent_runs(meeting)[0]["status"] == "succeeded"


def test_stale_actions_do_not_leak_old_facts(repo, meeting, minutes):
    generate_minutes(repo, FakeClient([minutes]), meeting)
    texts = {s["segment_id"]: s["text"] for s in repo.segments(meeting)}
    texts["S005"] = "文档现在由小李负责。"
    repo.edit_transcript(meeting, texts, 1)
    tools = MeetingTools(repo.snapshot(meeting), repo.latest_analysis(meeting))
    result = tools.execute("list_action_items", {"owner": None})
    assert result["status"] == "stale"
    assert result["segments"] == [] and tools.visible_ids == set()


@pytest.mark.parametrize(
    "name,args",
    [
        ("search_transcript", {"keywords": []}),
        ("search_transcript", {"keywords": ["预算"], "meeting_id": "other"}),
        ("get_segments", {"segment_ids": ["S999"]}),
        ("list_action_items", {"owner": ["小李"]}),
    ],
)
def test_tool_parameter_validation(repo, meeting, name, args):
    tools = MeetingTools(repo.snapshot(meeting), None)
    with pytest.raises(AppError):
        tools.execute(name, args)


def test_keyword_no_hit_not_claimed_absent(repo, meeting):
    tools = MeetingTools(repo.snapshot(meeting), None)
    result = tools.execute("search_transcript", {"keywords": ["完全不存在的字符"]})
    assert result["segments"] == []
    assert result["match_count"] == 0
