import json
from types import SimpleNamespace

import pytest

from meeting_assistant.config import ROOT
from meeting_assistant.llm import Reply
from meeting_assistant.services import import_sample
from meeting_assistant.storage import Repository


@pytest.fixture
def repo(tmp_path):
    return Repository(tmp_path / "data")


@pytest.fixture
def meeting(repo):
    return import_sample(repo, ROOT / "samples/product-review.json")


@pytest.fixture
def minutes():
    return {
        "summary": "确认了首版范围，预算待定。",
        "decisions": [{"content": "首版只支持 WAV 和 MP3 上传", "evidence_ids": ["S002"]}],
        "action_items": [
            {"task": "整理文档", "owner": None, "deadline_text": None, "evidence_ids": ["S005"]}
        ],
    }


class FakeClient:
    """Test-only scripted transport, never exposed as a production model provider."""

    def __init__(self, replies, protocol="structured", hook=None):
        self.replies = iter(replies)
        self.calls = 0
        self.hook = hook
        self.settings = SimpleNamespace(
            agent_protocol=protocol, public_model=lambda: {"provider": "test", "model": "scripted"}
        )

    def chat(self, messages, schema=None, tools=None):
        self.calls += 1
        if self.hook:
            self.hook()
        value = next(self.replies)
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, Reply) else Reply(json.dumps(value, ensure_ascii=False))


def tool_action(name="search_transcript", keywords=None, ids=None, owner=None):
    return {
        "kind": "tool",
        "tool_name": name,
        "keywords": keywords or [],
        "segment_ids": ids or [],
        "owner": owner,
        "answer": None,
        "evidence_ids": [],
    }


def final_action(answer="文档负责人未定。", ids=None):
    return {
        "kind": "final",
        "tool_name": None,
        "keywords": [],
        "segment_ids": [],
        "owner": None,
        "answer": answer,
        "evidence_ids": ids or [],
    }
