import sys
import threading
import time
import wave
from dataclasses import replace
from types import SimpleNamespace

import pytest
from conftest import FakeClient, final_action, tool_action
from filelock import FileLock

from meeting_assistant.config import Settings
from meeting_assistant.export import markdown_minutes
from meeting_assistant.live import LiveManager, Utterances, WakeGate, answer_public
from meeting_assistant.models import AppError
from meeting_assistant.storage import Repository


@pytest.mark.parametrize("wake", ["小K小K", "小k，小k", "小凯小凯", "小开小开"])
def test_wake_and_same_utterance_question(wake):
    assert WakeGate().feed(wake + "，预算确定了吗？", 3, 5, "S003") == ("预算确定了吗", 3, ["S003"])


def test_wake_split_and_timeout():
    gate = WakeGate()
    assert gate.feed("小K", 1, 2, "S001") is None
    assert gate.feed("小K", 2, 3, "S002") is None
    assert gate.feed("预算是多少？", 4, 5, "S003") == ("预算是多少？", 2, ["S001", "S002", "S003"])
    assert gate.feed("小K小K", 6, 7, "S004") is None
    assert gate.feed("普通发言", 18, 19, "S005") is None


def test_no_wake_and_long_question_chunk():
    gate = WakeGate()
    assert gate.feed("小K的方案还没决定", 1, 2, "S001") is None
    assert gate.feed("小K小K预算", 4, 12, "S002", final=False) is None
    assert gate.feed("确认了吗", 12, 14, "S003") == ("预算确认了吗", 4, ["S002", "S003"])
    assert gate.feed("下一段普通发言", 14, 15, "S004") is None


def test_endpointing_silence_preroll_and_max_chunk():
    np = pytest.importorskip("numpy")
    loud = (np.ones(320) * 10000).astype("<i2").tobytes()
    quiet = bytes(640)
    detector = Utterances(0.012, 0.2)
    for i in range(15):
        assert detector.feed(quiet, i * 0.02) is None
    assert detector.feed(loud, 0.3) is None
    result = None
    for i in range(11):
        result = detector.feed(quiet, 0.32 + i * 0.02) or result
    assert result and result[3] is True and result[1] < 0.3
    detector.reset()
    for i in range(399):
        assert detector.feed(loud, i * 0.02) is None
    result = detector.feed(loud, 399 * 0.02)
    assert result[3] is False and len(result[0]) == 400 * 640


def scripted_client():
    client = FakeClient([tool_action(keywords=["文档"]), final_action(ids=["S005"])])
    client.settings.tts_voice = ""
    return client


def test_public_answer_and_export_not_participant_evidence(repo, meeting):
    before = repo.snapshot(meeting)
    with FileLock(str(repo.root / "live.lock")):
        answer_public(
            repo,
            scripted_client(),
            meeting,
            "文档谁负责？",
            190,
            ["S008"],
            threading.Event(),
            lambda: 191,
            speaker=lambda *a: True,
        )
    snapshot = repo.snapshot(meeting)
    event = snapshot["public_dialogue"][0]
    assert event["status"] == "spoken"
    assert event["answer"] == "文档负责人未定。"
    assert event["evidence_ids"] == ["S005"]
    assert snapshot["segments"] == before["segments"]
    assert snapshot["revision"] > before["revision"]
    assert before["public_dialogue"] == []
    md = markdown_minutes(repo.meeting(meeting), snapshot)
    assert "## 小K公开问答" in md and "播报完成" in md and "文档负责人未定。" in md
    assert "小K公开问答" not in markdown_minutes(repo.meeting(meeting), before)


@pytest.mark.parametrize(
    "outcome,expected", [(False, "cancelled"), (AppError("没有中文声音"), "tts_failed")]
)
def test_speech_failure_keeps_generated_answer(repo, meeting, outcome, expected):
    def speaker(*args):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    answer_public(
        repo,
        scripted_client(),
        meeting,
        "谁整理文档",
        190,
        ["S008"],
        threading.Event(),
        lambda: 191,
        speaker=speaker,
    )
    event = repo.public_dialogue(meeting)[0]
    assert event["status"] == expected and event["answer"]


def test_missing_model_stores_public_question_no_fake_answer(repo, meeting):
    from meeting_assistant.llm import ChatClient

    answer_public(
        repo,
        ChatClient(Settings()),
        meeting,
        "谁整理文档",
        190,
        ["S008"],
        threading.Event(),
        lambda: 191,
        speaker=lambda *a: pytest.fail("must not speak"),
    )
    event = repo.public_dialogue(meeting)[0]
    assert event["status"] == "failed" and event["answer"] is None
    assert "LLM_BASE_URL" in event["error"]


def test_stop_during_model_prevents_speech(repo, meeting):
    stop = threading.Event()
    client = scripted_client()
    client.hook = stop.set
    answer_public(
        repo,
        client,
        meeting,
        "文档谁负责",
        190,
        ["S008"],
        stop,
        lambda: 191,
        speaker=lambda *a: pytest.fail("must not speak"),
    )
    assert repo.public_dialogue(meeting)[0]["status"] == "cancelled"


def test_live_empty_snapshot_and_recovery_lock(repo):
    meeting = repo.create_meeting("现场测试", 0, "live")
    assert repo.snapshot(meeting)["segments"] == []
    with FileLock(str(repo.root / "live.lock")):
        session = repo.begin_live(meeting, {})
        sid = repo.append_live_text(meeting, "小K小K", 0, 1)
        repo.begin_dialogue(meeting, "问题", 1, [sid])
        other = Repository(repo.root)
        assert other.live_session(meeting)["status"] == "starting"
        assert other.public_dialogue(meeting)[0]["status"] == "thinking"
    other = Repository(repo.root)
    assert other.live_session(meeting)["id"] == session
    assert other.live_session(meeting)["status"] == "interrupted"
    assert other.public_dialogue(meeting)[0]["status"] == "interrupted"


def test_full_capture_pipeline_with_injected_devices(repo, monkeypatch):
    """No physical microphone/model/API/speaker access. Tests the real worker lifecycle."""
    np = pytest.importorskip("numpy")
    pcm = (np.ones(320) * 10000).astype("<i2").tobytes()

    class Input:
        def __init__(self, **kwargs):
            self.n = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, count):
            time.sleep(0.003)
            self.n += 1
            return (pcm if self.n < 30 else bytes(640)), False

    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            return [SimpleNamespace(text="小K小K文档谁负责")], None

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(RawInputStream=Input))
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    manager = LiveManager(repo)
    ident = manager.start(
        replace(Settings(), max_audio_seconds=3, live_silence_seconds=0.2), "测试"
    )
    with pytest.raises(AppError, match="已有现场会议"):
        manager.start(Settings(), "重复")
    manager.thread.join(timeout=8)
    assert not manager.active and manager.error is None
    assert repo.live_session(ident)["status"] == "completed"
    assert "小K小K" in repo.segments(ident)[0]["text"]
    assert repo.public_dialogue(ident)[0]["status"] == "failed"  # API intentionally absent
    with wave.open(str(repo.path(repo.meeting(ident)["audio_path"]))) as wav:
        assert wav.getnframes() >= 3 * 16000
        assert wav.getframerate() == 16000


def test_public_evidence_survives_transcript_edit(repo, meeting):
    answer_public(
        repo,
        scripted_client(),
        meeting,
        "文档谁负责",
        190,
        ["S008"],
        threading.Event(),
        lambda: 191,
        speaker=lambda *a: True,
    )
    before = repo.snapshot(meeting)
    texts = {s["segment_id"]: s["text"] for s in before["segments"]}
    texts["S005"] = "现在负责人变成小李"
    repo.edit_transcript(meeting, texts, before["revision"])
    after = repo.snapshot(meeting)
    original = after["public_dialogue"][0]["evidence_snapshot"][0]
    assert original["segment_id"] == "S005" and "暂时没有确定负责人" in original["text"]
    assert "原始引用 S005" in markdown_minutes(repo.meeting(meeting), after)


def test_ui_empty_live_record_does_not_start_microphone(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from meeting_assistant.config import ROOT

    monkeypatch.setenv("MEETING_DATA_DIR", str(tmp_path))
    repo = Repository(tmp_path)
    ident = repo.create_meeting("尚无发言的现场会议", 0, "live")
    monkeypatch.setattr(
        LiveManager, "start", lambda *a, **k: pytest.fail("unexpected microphone start")
    )
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
    assert next(b for b in app.button if b.label == "开始收音 · 唤醒小K").disabled
    app.selectbox[0].set_value(ident).run()
    assert not app.exception
    assert any(x.value == "尚无公开问答。" for x in app.caption)


@pytest.mark.parametrize("system,program", [("Darwin", "say"), ("Windows", "powershell.exe")])
def test_tts_uses_literal_file_and_expected_platform(repo, monkeypatch, system, program):
    from pathlib import Path

    from meeting_assistant import tts

    text = "小K回答：$(echo secret) `anything` ; 预算未定。"
    calls = []

    class Process:
        returncode = 0

        def __init__(self, args, **kwargs):
            calls.append(args)
            flag = "-f" if system == "Darwin" else "-TextPath"
            assert Path(args[args.index(flag) + 1]).read_text(encoding="utf-8") == text
            assert text not in args and "shell" not in kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def poll(self):
            return self.returncode

    monkeypatch.setattr(tts.platform, "system", lambda: system)
    monkeypatch.setattr(tts.subprocess, "Popen", Process)
    assert tts.speak(text, "", threading.Event()) is True
    assert calls[0][0] == program


def test_long_answer_does_not_overflow_muted_capture_queue(repo, monkeypatch):
    from meeting_assistant import live

    np = pytest.importorskip("numpy")
    loud = (np.ones(320) * 10000).astype("<i2").tobytes()

    class Input:
        def __init__(self, **kwargs):
            self.n = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, count):
            time.sleep(0.0005)
            self.n += 1
            return (loud if self.n < 30 else bytes(640)), False

    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            return [SimpleNamespace(text="小K小K谁负责页面")], None

    answered = []

    def wait_during_answer(repo, client, mid, question, when, ids, stop, clock):
        answered.append(question)
        assert stop.wait(5)

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(RawInputStream=Input))
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(live, "answer_public", wait_during_answer)
    manager = LiveManager(repo)
    ident = manager.start(
        replace(Settings(), max_audio_seconds=35, live_silence_seconds=0.2), "测试"
    )
    manager.thread.join(timeout=8)
    assert answered and not manager.active and manager.error is None
    assert repo.meeting(ident)["duration"] >= 35
