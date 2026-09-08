import asyncio

import numpy as np
import pytest
from fastapi.testclient import TestClient
from meeting_app.agent import Agent, Tools
from meeting_app.config import Settings
from meeting_app.main import create_app
from meeting_app.speech import decode_audio, match_embedding, wav_bytes
from meeting_app.store import Store


class FakeSpeech:
    def available(self):
        return {"asr": True, "speaker": True, "kws": False}

    async def run(self, kind, content, candidates=None):
        if kind == "enroll":
            return [1.0, 0.0, 0.0]
        return {
            "segments": [
                {
                    "text": "周五完成测试。",
                    "start": 0,
                    "end": 2,
                    "match": {"name": "未知说话人", "status": "unknown"},
                }
            ],
            "duration": 2,
        }

    def close(self):
        pass


@pytest.fixture
def setup(tmp_path):
    settings = Settings(data_dir=tmp_path, model_dir=tmp_path / "models")
    app = create_app(settings, speech=FakeSpeech())
    with TestClient(app) as client:
        host = client.post("/api/meetings", json={"title": "项目讨论", "name": "林"}).json()
        path = "/api/meetings/" + host["meeting_id"]
        headers = {"Authorization": "Bearer " + host["token"]}
        yield client, app, host, path, headers


def add(client, path, headers, text="周五完成测试", key="request-first"):
    response = client.post(
        path + "/utterances",
        headers=headers,
        json={"text": text, "speaker": "林", "start": 0, "end": 3, "request_key": key},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_membership_isolation_and_host_permissions(setup):
    client, app, host, path, headers = setup
    assert client.get(path).status_code == 401
    snapshot = client.get(path, headers=headers).json()
    assert "token_hash" not in snapshot["members"][0]
    guest = client.post(
        "/api/join", json={"code": snapshot["meeting"]["code"], "name": "陈", "consent": True}
    ).json()
    guest_headers = {"Authorization": "Bearer " + guest["token"]}
    assert client.get(path, headers=guest_headers).status_code == 200
    assert client.delete(path, headers=guest_headers).status_code == 403
    assert (
        client.post(
            path + "/utterances", headers=guest_headers, json={"text": "hack", "request_key": "abcd1234"}
        ).status_code
        == 403
    )
    other = client.post("/api/meetings", json={"title": "隔离", "name": "另一个人"}).json()
    assert client.get("/api/meetings/" + other["meeting_id"], headers=headers).status_code == 401
    assert (
        client.post(
            "/api/join", json={"code": snapshot["meeting"]["code"], "name": "无授权", "consent": False}
        ).status_code
        == 422
    )


def test_idempotency_agent_confirmation_revision_and_export(setup):
    client, app, host, path, headers = setup
    ident = add(client, path, headers)
    assert ident == add(client, path, headers)
    question = {"question": "测试什么时候完成", "request_key": "question-first"}
    answer = client.post(path + "/ask", headers=headers, json=question)
    assert answer.status_code == 200, answer.text
    assert client.post(path + "/ask", headers=headers, json=question).json() == answer.json()
    snapshot = client.get(path, headers=headers).json()
    assert len(snapshot["utterances"]) == len(snapshot["answers"]) == 1
    assert snapshot["decisions"] == []
    a = snapshot["answers"][0]
    assert a["mode"] == "mock"
    assert a["trace"][0]["tool"] == "search_meeting"
    assert a["citations"] == [{"id": ident, "version": 1}]
    client.post(path + "/summary", headers=headers, json={})
    client.post(
        path + f"/answers/{a['id']}/confirm", headers=headers, json={"text": "周五完成测试", "kind": "action"}
    )
    client.post(
        path + f"/answers/{a['id']}/confirm", headers=headers, json={"text": "周五完成测试", "kind": "action"}
    )
    snapshot = client.get(path, headers=headers).json()
    assert len(snapshot["decisions"]) == 1
    assert "AI回答与建议" in client.get(path + "/export", headers=headers).text
    change = {"text": "改为周六完成测试", "speaker": "林", "version": 1}
    assert client.patch(path + "/utterances/" + ident, headers=headers, json=change).status_code == 200
    assert client.patch(path + "/utterances/" + ident, headers=headers, json=change).status_code == 400
    s = client.get(path, headers=headers).json()
    assert s["answers"][0]["stale"] and s["summaries"][0]["stale"]
    assert (
        client.post(
            path + f"/answers/{a['id']}/confirm", headers=headers, json={"text": "错误再次采纳"}
        ).status_code
        == 400
    )
    assert client.delete(path, headers=headers).status_code == 200
    assert client.get(path, headers=headers).status_code == 401
    assert client.get(path + "/export", headers=headers).status_code == 401


def test_websocket_auth_and_reconnect_snapshot(setup):
    client, app, host, path, headers = setup
    mid = host["meeting_id"]
    with client.websocket_connect("/ws/meetings/" + mid) as ws:
        ws.send_json({"token": host["token"]})
        first = ws.receive_json()["data"]
        ident = add(client, path, headers)
        next = ws.receive_json()["data"]
        assert next["meeting"]["seq"] > first["meeting"]["seq"]
        assert next["utterances"][0]["id"] == ident
    with client.websocket_connect("/ws/meetings/" + mid) as ws:
        ws.send_json({"token": host["token"]})
        assert len(ws.receive_json()["data"]["utterances"]) == 1
    with client.websocket_connect("/ws/meetings/" + mid) as ws:
        ws.send_json({"token": "invalid"})
        assert ws.receive()["code"] == 4401


def test_voiceprint_ownership_revoke_delete_and_no_vector_disclosure(setup):
    client, app, host, path, headers = setup
    blob = wav_bytes(np.ones(16000 * 4, dtype=np.float32) * 0.1)
    assert (
        client.post(
            path + "/voiceprint", headers=headers, data={"consent": "false"}, files={"file": ("a.wav", blob)}
        ).status_code
        == 400
    )
    assert (
        client.post(
            path + "/voiceprint", headers=headers, data={"consent": "true"}, files={"file": ("a.wav", blob)}
        ).status_code
        == 200
    )
    s = client.get(path, headers=headers).json()
    assert len(s["voiceprints"]) == 1
    assert "embedding" not in s["voiceprints"][0]
    client.patch(path + "/voiceprint", headers=headers, json={"enabled": False})
    assert app.state.store.voice_candidates(host["meeting_id"]) == []
    client.delete(path + "/voiceprint", headers=headers)
    assert client.get(path, headers=headers).json()["voiceprints"] == []


def test_audio_session_and_pcm_persistence(setup):
    client, app, host, path, headers = setup
    mid = host["meeting_id"]
    with client.websocket_connect("/ws/audio/" + mid) as ws:
        ws.send_json({"token": host["token"]})
        assert ws.receive_json()["type"] == "ready"
        # Second owner is rejected, even with the host credential.
        with client.websocket_connect("/ws/audio/" + mid) as ws2:
            ws2.send_json({"token": host["token"]})
            assert ws2.receive_json()["type"] == "error"
        pcm = (np.sin(np.arange(1600) * 0.1) * 12000).astype("<i2").tobytes()
        for _ in range(20):
            ws.send_bytes(pcm)
        ws.send_json({"type": "stop"})
        assert ws.receive()["type"] == "websocket.close"
    s = client.get(path, headers=headers).json()
    assert len(s["utterances"]) == 1
    assert s["utterances"][0]["source"] == "asr"
    assert list((app.state.settings.data_dir / "audio" / mid).glob("*.wav"))
    assert client.delete(path, headers=headers).status_code == 200
    assert not (app.state.settings.data_dir / "audio" / mid).exists()


def test_agent_cannot_cite_unseen_or_cross_meeting_records(tmp_path):
    store = Store(tmp_path)
    a = store.create("A", "a")
    b = store.create("B", "b")
    aid = store.add_utterance(a["meeting_id"], "周五上线", "a", 0, 5, "manual", "a")
    bid = store.add_utterance(b["meeting_id"], "不能读到的秘密", "b", 0, 5, "manual", "b")
    snapshot = store.snapshot(a["meeting_id"])
    tools = Tools(snapshot)
    with pytest.raises(ValueError):
        tools.execute("get_utterances", {"ids": [bid]})
    with pytest.raises(ValueError):
        tools.execute("run_shell", {"command": "echo secret"})

    class Client:
        async def complete(self, messages):
            return {"kind": "final", "answer": "没有读取也敢引用", "citations": [aid]}

    result = asyncio.run(Agent(Settings(llm_mode="local"), Client()).ask(snapshot, "什么时候上线"))
    assert result["status"] == "failed"
    assert result["citations"] == []


def test_agent_bounded_tool_loop_recovers_from_bad_tool_arguments(tmp_path):
    store = Store(tmp_path)
    a = store.create("A", "a")
    aid = store.add_utterance(a["meeting_id"], "周五上线", "a", 0, 5, "manual", "a")

    class Client:
        count = 0

        async def complete(self, messages):
            self.count += 1
            if self.count == 1:
                return {"kind": "tool", "tool": "search_meeting", "arguments": {"keywords": []}}
            if self.count == 2:
                return {"kind": "tool", "tool": "search_meeting", "arguments": {"keywords": ["上线"]}}
            return {"kind": "final", "answer": "周五上线", "citations": [aid]}

    result = asyncio.run(
        Agent(Settings(llm_mode="local"), Client()).ask(store.snapshot(a["meeting_id"]), "什么时候")
    )
    assert result["status"] == "succeeded"
    assert len(result["trace"]) == 2 and "error" in result["trace"][0]["result"]


def test_speaker_threshold_margin_and_bounded_decode():
    vec = np.array([1.0, 0.0], dtype=np.float32)
    candidates = [{"name": "a", "member_id": "a", "embedding": "[1,0]"}]
    assert match_embedding(vec, candidates, 0.65, 0.1)["status"] == "matched"
    assert match_embedding(-vec, candidates, 0.65, 0.1)["status"] == "uncertain"
    candidates.append({"name": "b", "member_id": "b", "embedding": "[1,0]"})
    assert match_embedding(vec, candidates, 0.65, 0.1)["status"] == "uncertain"
    samples = np.sin(np.arange(16000) * 0.1).astype(np.float32) * 0.1
    assert len(decode_audio(wav_bytes(samples))) == 16000
    with pytest.raises(ValueError):
        decode_audio(wav_bytes(samples), max_seconds=0.5)
    with pytest.raises(ValueError):
        decode_audio(b"not audio")


def test_native_audio_worker_timeout_can_recover(tmp_path):
    from meeting_app.speech import Speech

    speech = Speech(Settings(model_dir=tmp_path / "missing-models"))
    speech.timeout = 0.001
    content = wav_bytes(np.ones(64000, dtype=np.float32) * 0.1)
    try:
        with pytest.raises(ValueError, match="工作进程"):
            asyncio.run(speech.run("enroll", content))
        assert speech.process is None
        speech.timeout = 10
        # A fresh child should return a controlled missing-model/dependency error, not hang.
        with pytest.raises(ValueError):
            asyncio.run(speech.run("enroll", content))
        assert speech.process is not None and speech.process.is_alive()
    finally:
        speech.close()


def test_confirmed_actions_include_current_source_evidence(setup):
    client, app, host, path, headers = setup
    ident = add(client, path, headers)
    aid = client.post(
        path + "/ask", headers=headers, json={"question": "什么时候测试", "request_key": "action-question"}
    ).json()["id"]
    client.post(
        path + "/answers/" + aid + "/confirm",
        headers=headers,
        json={"text": "周五完成测试", "kind": "action"},
    )
    snapshot = client.get(path, headers=headers).json()
    tools = Tools(snapshot)
    result = tools.execute("list_confirmed_actions", {})
    assert result["confirmed_actions"][0]["text"] == "周五完成测试"
    assert result["utterances"][0]["id"] == ident
    assert ident in tools.visible


def test_host_manages_device_free_attendees_with_consent_and_isolation(setup):
    client, app, host, path, headers = setup
    code = client.get(path, headers=headers).json()["meeting"]["code"]
    guest = client.post("/api/join", json={"code": code, "name": "手机用户", "consent": True}).json()
    gh = {"Authorization": "Bearer " + guest["token"]}
    payload = {"name": "现场张三", "consent": True}
    assert client.post(path + "/members", headers=gh, json=payload).status_code == 403
    assert (
        client.post(path + "/members", headers=headers, json={**payload, "consent": False}).status_code == 422
    )
    with client.websocket_connect("/ws/meetings/" + host["meeting_id"]) as ws:
        ws.send_json({"token": guest["token"]})
        ws.receive_json()
        response = client.post(path + "/members", headers=headers, json=payload)
        assert response.status_code == 200
        attendee = response.json()
        assert attendee["role"] == "attendee" and "token" not in attendee
        assert ws.receive_json()["data"]["members"][-1]["id"] == attendee["id"]
    assert client.post(path + "/members", headers=headers, json=payload).status_code == 400
    vp = path + "/members/" + attendee["id"] + "/voiceprint"
    files = {"file": ("enroll.wav", wav_bytes(np.ones(64000, dtype=np.float32) * 0.1))}
    for auth, consent, expected in [(gh, "true", 403), (headers, "false", 400), (headers, "true", 200)]:
        assert client.post(vp, headers=auth, data={"consent": consent}, files=files).status_code == expected
    assert (
        client.post(
            path + "/members/" + guest["member_id"] + "/voiceprint",
            headers=headers,
            data={"consent": "true"},
            files=files,
        ).status_code
        == 404
    )
    other = app.state.store.create("另一场", "另一个主持人")
    foreign = app.state.store.add_attendee(other["meeting_id"], "他人")
    assert (
        client.post(
            path + "/members/" + foreign["id"] + "/voiceprint",
            headers=headers,
            data={"consent": "true"},
            files=files,
        ).status_code
        == 404
    )
    snap = client.get(path, headers=gh).json()
    assert snap["voiceprints"][0]["member_id"] == attendee["id"]
    assert "embedding" not in snap["voiceprints"][0]
    assert "token_hash" not in snap["members"][-1]
    # The participant and their voiceprint survive a store reopen, with no raw enrollment recording.
    reopened = Store(app.state.settings.data_dir)
    assert reopened.attendee(host["meeting_id"], attendee["id"])["name"] == "现场张三"
    assert reopened.voice_candidates(host["meeting_id"])[0]["name"] == "现场张三"
    assert not list(app.state.settings.data_dir.rglob("*.wav"))
    assert client.patch(vp, headers=gh, json={"enabled": False}).status_code == 403
    assert client.delete(vp, headers=gh).status_code == 403
    assert client.patch(vp, headers=headers, json={"enabled": False}).status_code == 200
    assert reopened.voice_candidates(host["meeting_id"]) == []
    assert client.patch(vp, headers=headers, json={"enabled": True}).status_code == 400
    assert client.delete(vp, headers=headers).status_code == 200
    assert client.get(path, headers=headers).json()["voiceprints"] == []
    for i in range(9):
        assert (
            client.post(
                path + "/members", headers=headers, json={"name": f"现场{i}", "consent": True}
            ).status_code
            == 200
        )
    assert (
        client.post(path + "/members", headers=headers, json={"name": "超额", "consent": True}).status_code
        == 400
    )
    assert client.post("/api/join", json={"code": code, "name": "超额", "consent": True}).status_code == 400
    assert client.post(path + "/end", headers=headers).status_code == 200
    assert client.post(vp, headers=headers, data={"consent": "true"}, files=files).status_code == 409
    assert (
        client.post(path + "/members", headers=headers, json={"name": "会后", "consent": True}).status_code
        == 400
    )
