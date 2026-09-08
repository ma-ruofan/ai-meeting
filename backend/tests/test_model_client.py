import asyncio
import json

import httpx
import pytest
from meeting_app.agent import ModelClient
from meeting_app.config import Settings


def settings(**extra):
    return Settings(llm_mode="local", llm_url="http://127.0.0.1:11434/v1", llm_model="gemma4:e4b", **extra)


def test_ollama_native_json_thinking_disabled_and_proxy_bypass(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    calls = []
    options = []
    real_client = httpx.AsyncClient

    def factory(**kwargs):
        options.append(kwargs)
        return real_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)

    def respond(request):
        calls.append(request)
        assert str(request.url) == "http://127.0.0.1:11434/api/chat"
        body = json.loads(request.content)
        assert body["model"] == "gemma4:e4b"
        assert body["format"] == "json" and body["think"] is False and body["stream"] is False
        assert body["options"]["num_predict"] == 1800
        return httpx.Response(
            200,
            json={"message": {"content": '{"ok":true}', "thinking": "NOT-FOR-OUTPUT"}, "done_reason": "stop"},
        )

    result = asyncio.run(ModelClient(settings(), httpx.MockTransport(respond)).complete([]))
    assert result == {"ok": True} and len(calls) == 1
    assert options[0]["trust_env"] is False
    assert options[0]["timeout"].read == 120


@pytest.mark.parametrize(
    "base,backend,endpoint",
    [
        ("http://localhost:11434", "auto", "http://localhost:11434/api/chat"),
        ("http://localhost:11434/api", "ollama", "http://localhost:11434/api/chat"),
        ("http://localhost:11434/api/chat", "ollama", "http://localhost:11434/api/chat"),
        ("http://localhost:11434/v1", "chat-completions", "http://localhost:11434/v1/chat/completions"),
        ("http://model.example/ollama/v1/", "ollama", "http://model.example/ollama/api/chat"),
    ],
)
def test_model_endpoint_selection(base, backend, endpoint):
    s = settings(llm_backend=backend)
    s.llm_url = base
    assert ModelClient(s).connection()[0] == endpoint


def test_chat_completions_adapter_preserved_and_fenced_json_accepted():
    def respond(request):
        body = json.loads(request.content)
        assert str(request.url) == "http://127.0.0.1:8080/v1/chat/completions"
        assert "format" not in body and "think" not in body and body["max_tokens"] == 1800
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '```json\n{"ok": true}\n```'}, "finish_reason": "stop"}]
            },
        )

    s = settings()
    s.llm_url = "http://127.0.0.1:8080/v1"
    assert asyncio.run(ModelClient(s, httpx.MockTransport(respond)).complete([])) == {"ok": True}


@pytest.mark.parametrize("code", [400, 401, 403, 404, 429, 500])
def test_http_errors_distinct_redacted_and_never_retried(code):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(code, json={"error": "SECRET-KEY PRIVATE-QUESTION"})

    with pytest.raises(ValueError, match=f"LLM_HTTP_{code}") as exc:
        asyncio.run(ModelClient(settings(llm_key="SECRET-KEY"), httpx.MockTransport(respond)).complete([]))
    assert "SECRET" not in str(exc.value) and "PRIVATE" not in str(exc.value) and len(calls) == 1


@pytest.mark.parametrize(
    "error,expected", [(httpx.ConnectError, "LLM_CONNECT"), (httpx.ReadTimeout, "LLM_TIMEOUT")]
)
def test_transport_errors_distinct_and_redacted(error, expected):
    def respond(request):
        raise error("SECRET upstream details", request=request)

    with pytest.raises(ValueError, match=expected) as exc:
        asyncio.run(ModelClient(settings(), httpx.MockTransport(respond)).complete([]))
    assert "SECRET" not in str(exc.value)


@pytest.mark.parametrize(
    "body,expected",
    [
        ({"message": {"content": "你好，PRIVATE-QUESTION"}}, "LLM_JSON"),
        ({"message": {"content": "", "thinking": "PRIVATE-THOUGHT"}}, "LLM_EMPTY"),
        ({"message": {"content": '{"ok":true}'}, "done_reason": "length"}, "LLM_TRUNCATED"),
        ({"message": {"content": "[]"}}, "LLM_JSON"),
        ({"message": {"content": 'Prefix {"ok":true}'}}, "LLM_JSON"),
        ({"choices": []}, "LLM_RESPONSE"),
        ([], "LLM_RESPONSE"),
    ],
)
def test_output_shape_and_truncation_failures(body, expected):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    with pytest.raises(ValueError, match=expected) as exc:
        asyncio.run(ModelClient(settings(), transport).complete([]))
    assert "PRIVATE" not in str(exc.value)


def test_non_json_http_body_is_distinguished_from_non_json_answer():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="<html>SECRET proxy error</html>")
    )
    with pytest.raises(ValueError, match="LLM_RESPONSE") as exc:
        asyncio.run(ModelClient(settings(), transport).complete([]))
    assert "SECRET" not in str(exc.value)
