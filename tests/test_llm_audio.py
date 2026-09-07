import io
import json
import wave
from dataclasses import replace

import httpx
import pytest

from meeting_assistant.audio import import_audio
from meeting_assistant.config import Settings
from meeting_assistant.llm import ChatClient
from meeting_assistant.models import AppError, Minutes, validate_segments


def test_cloud_payload_and_local_payload():
    requests = []

    def respond(request):
        requests.append((str(request.url), json.loads(request.content), dict(request.headers)))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}
        )

    cfg = Settings(base_url="https://provider.test/v1", model="example", api_key="test-placeholder")
    ChatClient(cfg, httpx.MockTransport(respond)).chat([], schema=Minutes.model_json_schema())
    assert requests[0][0] == "https://provider.test/v1/chat/completions"
    assert "json_schema" in requests[0][1]["response_format"]
    local = replace(cfg, provider="llamacpp", base_url="http://127.0.0.1:8080/v1", api_key="")
    ChatClient(local, httpx.MockTransport(respond)).chat([], schema=Minutes.model_json_schema())
    assert "schema" in requests[1][1]["response_format"]
    assert "authorization" not in requests[1][2]


def test_budget_stops_before_http():
    cfg = Settings(
        base_url="https://provider.test/v1",
        model="example",
        api_key="placeholder",
        context_tokens=4096,
        max_output_tokens=1024,
    )

    def unexpected(_):
        pytest.fail("must not call provider")

    with pytest.raises(AppError, match="预算"):
        ChatClient(cfg, httpx.MockTransport(unexpected)).chat(
            [{"role": "user", "content": "中" * 2000}]
        )


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 302])
def test_errors_never_echo_provider_body(status):
    cfg = Settings(base_url="https://provider.test/v1", model="example", api_key="placeholder")
    transport = httpx.MockTransport(
        lambda _: httpx.Response(status, text="SECRET-ECHO personal transcript")
    )
    with pytest.raises(AppError) as exc:
        ChatClient(cfg, transport).chat([])
    assert "SECRET" not in str(exc.value)


def test_truncated_response_rejected():
    cfg = Settings(base_url="https://provider.test/v1", model="example", api_key="placeholder")
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "length"}]}
        )
    )
    with pytest.raises(AppError, match="截断"):
        ChatClient(cfg, transport).chat([])


def test_local_config_does_not_inherit_cloud_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "llamacpp")
    monkeypatch.setenv("LLM_API_KEY", "cloud-secret-test")
    monkeypatch.delenv("LLAMACPP_API_KEY", raising=False)
    assert Settings.load().api_key == ""


def test_local_mode_rejects_remote_host():
    assert Settings(provider="llamacpp", base_url="https://provider.test/v1", model="x").llm_error()


def wav_bytes(seconds=1):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * int(16000 * seconds))
    return stream.getvalue()


def test_standard_wav_import_without_ffmpeg(repo):
    ident = import_audio(repo, Settings(), "测试", "../../unsafe.wav", wav_bytes())
    meeting = repo.meeting(ident)
    assert meeting["duration"] == 1
    assert repo.path(meeting["audio_path"]).is_file()
    assert "unsafe" not in meeting["audio_path"]


def test_rejected_audio_leaves_no_partial_meeting(repo):
    with pytest.raises(AppError):
        import_audio(repo, Settings(max_audio_seconds=1), "超时音频", "long.wav", wav_bytes(2))
    assert repo.list_meetings() == []
    assert not list((repo.root / "meetings").iterdir())


@pytest.mark.parametrize(
    "segments",
    [
        [],
        [{"segment_id": "S001", "start": 0, "end": 100, "text": "too long"}],
        [
            {"segment_id": "S001", "start": 0, "end": 1, "text": "a"},
            {"segment_id": "S001", "start": 2, "end": 3, "text": "b"},
        ],
    ],
)
def test_bad_transcript_rejected(segments):
    with pytest.raises(AppError):
        validate_segments(segments, 10)
