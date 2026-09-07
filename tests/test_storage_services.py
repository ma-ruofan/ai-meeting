import pytest
from conftest import FakeClient

from meeting_assistant.export import markdown_minutes
from meeting_assistant.models import AppError
from meeting_assistant.services import generate_minutes
from meeting_assistant.storage import Repository


def test_snapshot_survives_edit_and_restart(repo, meeting, minutes):
    generate_minutes(repo, FakeClient([minutes]), meeting)
    saved = repo.latest_analysis(meeting)
    original = saved["snapshot"]["segments"][0]["text"]
    changes = {s["segment_id"]: s["text"] for s in repo.segments(meeting)}
    changes["S001"] = "修订后的会议开场。"
    repo.edit_transcript(meeting, changes, expected_revision=1)
    restarted = Repository(repo.root)
    assert restarted.meeting(meeting)["transcript_revision"] == 2
    assert restarted.latest_analysis(meeting)["snapshot"]["segments"][0]["text"] == original
    text = markdown_minutes(restarted.meeting(meeting), saved["snapshot"], saved)
    assert "基于旧转录" in text
    assert original in text and "修订后的会议开场" not in text
    assert str(repo.root) not in text


def test_analysis_during_edit_uses_start_snapshot(repo, meeting, minutes):
    def edit():
        changes = {s["segment_id"]: s["text"] for s in repo.segments(meeting)}
        changes["S005"] = "文档已交给小李。"
        repo.edit_transcript(meeting, changes, 1)

    generate_minutes(repo, FakeClient([minutes], hook=edit), meeting)
    saved = repo.latest_analysis(meeting)
    assert saved["revision"] == 1
    assert repo.meeting(meeting)["transcript_revision"] == 2
    assert "暂时没有确定负责人" in saved["snapshot"]["segments"][4]["text"]


def test_invalid_reference_retries_once_never_saved_as_success(repo, meeting, minutes):
    bad = {**minutes, "decisions": [{"content": "错误", "evidence_ids": ["S999"]}]}
    client = FakeClient([bad, bad])
    with pytest.raises(AppError, match="纠错"):
        generate_minutes(repo, client, meeting)
    assert client.calls == 2
    assert repo.latest_analysis(meeting) is None
    assert repo.analyses(meeting)[0]["status"] == "failed"
    assert repo.analyses(meeting)[0]["result"] is None


def test_correction_success_is_counted(repo, meeting, minutes):
    generate_minutes(repo, FakeClient([{}, minutes]), meeting)
    assert repo.latest_analysis(meeting)["attempts"] == 2


def test_failed_provider_preserves_success(repo, meeting, minutes):
    generate_minutes(repo, FakeClient([minutes]), meeting)
    first = repo.latest_analysis(meeting)["id"]
    with pytest.raises(AppError):
        generate_minutes(repo, FakeClient([AppError("服务不可用")]), meeting)
    assert repo.latest_analysis(meeting)["id"] == first
    assert len(repo.segments(meeting)) == 8


def test_edit_conflict_and_noop(repo, meeting):
    texts = {s["segment_id"]: s["text"] for s in repo.segments(meeting)}
    assert repo.edit_transcript(meeting, texts, 1) is False
    with pytest.raises(AppError, match="其他页面"):
        repo.edit_transcript(meeting, texts, 0)
    texts["S005"] = ""
    with pytest.raises(AppError):
        repo.edit_transcript(meeting, texts, 1)
    assert repo.meeting(meeting)["transcript_revision"] == 1


def test_lock_blocks_concurrent_processing_and_recovery(repo, meeting):
    with repo.processing():
        run = repo.begin_stage(meeting, "transcription", {})
        other = Repository(repo.root)
        assert other.stages(meeting)[0]["status"] == "running"
        with pytest.raises(AppError, match="正在运行"):
            with other.processing():
                pass
    other.recover()
    assert other.stages(meeting)[0]["id"] == run
    assert other.stages(meeting)[0]["status"] == "interrupted"


def test_retranscription_preserves_snapshots(repo, meeting, minutes):
    generate_minutes(repo, FakeClient([minutes]), meeting)
    old = repo.latest_analysis(meeting)
    repo.replace_transcript(
        meeting, [{"segment_id": "S001", "start": 0, "end": 10, "text": "新转录"}]
    )
    assert repo.latest_analysis(meeting)["snapshot"] == old["snapshot"]
    assert repo.meeting(meeting)["transcript_revision"] == 2


def test_path_escape_rejected(repo):
    with pytest.raises(AppError):
        repo.path("../../outside.wav")
