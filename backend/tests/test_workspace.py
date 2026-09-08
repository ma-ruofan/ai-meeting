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


def shared_answer(app, host, question="测试什么时候完成", key="shared-question"):
    snapshot = app.state.store.snapshot(host["meeting_id"])
    result = asyncio.run(Agent(app.state.settings).ask(snapshot, question))
    return app.state.store.save_answer(
        host["meeting_id"],
        host["member_id"],
        question,
        result,
        snapshot["meeting"]["revision"],
        "mock",
        True,
        key,
    )


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
    aid = shared_answer(app, host)
    assert shared_answer(app, host) == aid
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
    assert "小K语音问答" in client.get(path + "/export", headers=headers).text
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
    aid = shared_answer(app, host, "什么时候测试", "action-question")
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


def test_private_chat_isolated_from_members_websocket_export_and_adoption(setup):
    client, app, host, path, headers = setup
    add(client, path, headers)
    code = client.get(path, headers=headers).json()["meeting"]["code"]
    guest = client.post("/api/join", json={"code": code, "name": "私聊成员", "consent": True}).json()
    gh = {"Authorization": "Bearer " + guest["token"]}
    assert client.get(path + "/private-chat").status_code == 401
    body = {"question": "私密标记SECRET-739：测试何时结束？", "request_key": "same-private-key"}
    private = client.post(path + "/private-chat", headers=gh, json=body)
    assert private.status_code == 200
    assert private.headers["cache-control"] == "no-store"
    assert client.post(path + "/ask", headers=gh, json=body).json() == private.json()
    own = client.get(path + "/private-chat", headers=gh)
    assert len(own.json()["answers"]) == 1
    assert own.headers["cache-control"] == "no-store"
    assert client.get(path + "/private-chat", headers=headers).json()["answers"] == []
    assert client.post(path + "/ask", headers=headers, json={**body, "spoken": True}).status_code == 422
    assert (
        client.post(
            path + "/private-chat", headers=gh, json={**body, "member_id": host["member_id"]}
        ).status_code
        == 422
    )
    # Idempotency keys are scoped to a participant, never shared between users.
    other = client.post(
        path + "/private-chat", headers=headers, json={**body, "question": "主持人个人问题"}
    ).json()
    assert other["id"] != private.json()["id"]
    for auth in (headers, gh):
        snapshot = client.get(path, headers=auth)
        assert "SECRET-739" not in snapshot.text and snapshot.json()["answers"] == []
        assert "SECRET-739" not in client.get(path + "/export", headers=auth).text
    with client.websocket_connect("/ws/meetings/" + host["meeting_id"]) as ws:
        ws.send_json({"token": host["token"]})
        assert "SECRET-739" not in str(ws.receive_json())
        ws.send_text("sync")
        assert "SECRET-739" not in str(ws.receive_json())
    assert (
        client.post(
            path + "/answers/" + private.json()["id"] + "/confirm",
            headers=headers,
            json={"text": "不允许把私聊采纳到公共纪要"},
        ).status_code
        == 400
    )
    assert client.post(path + "/summary", headers=headers, json={}).status_code == 200
    assert "SECRET-739" not in client.get(path, headers=headers).text
    mid = host["meeting_id"]
    assert len(Store(app.state.settings.data_dir).private_answers(mid, guest["member_id"])) == 1
    shared_id = shared_answer(app, host)
    assert client.post(path + "/end", headers=headers).status_code == 200
    for auth in (headers, gh):
        assert client.get(path + "/private-chat", headers=auth).json()["answers"] == []
        assert client.post(path + "/private-chat", headers=auth, json=body).status_code == 409
    reopened = Store(app.state.settings.data_dir)
    with reopened.connect() as db:
        assert (
            db.execute("SELECT count(*) FROM private_answers WHERE meeting_id=?", (mid,)).fetchone()[0] == 0
        )
    assert reopened.snapshot(mid)["answers"][0]["id"] == shared_id


def test_private_context_reads_voice_and_summary_but_voice_cannot_read_private(setup):
    client, app, host, path, headers = setup
    ident = add(client, path, headers)
    store, mid = app.state.store, host["meeting_id"]
    voice_id = store.save_answer(
        mid,
        host["member_id"],
        "小K语音建议是什么",
        {
            "answer": "VOICE-CONTEXT-487：建议使用轮流发言测试。",
            "citations": [{"id": ident, "version": 1}],
            "trace": [],
            "status": "succeeded",
        },
        1,
        "mock",
        True,
        "voice-source",
    )
    store.save_summary(
        mid,
        {"overview": "SUMMARY-CONTEXT-512", "highlights": [], "proposed_actions": [], "citations": [ident]},
        1,
        "mock",
    )
    store.save_private_answer(
        mid,
        host["member_id"],
        "我的私人问题SECRET-123",
        {"answer": "个人回复", "citations": [], "trace": [], "status": "succeeded"},
        1,
        "mock",
        "private-source",
    )
    snapshot = store.snapshot(mid)
    public_tools = Tools(snapshot)
    with pytest.raises(ValueError):
        public_tools.execute("get_utterances", {"ids": [voice_id]})
    private_snapshot = {**snapshot, "private_history": store.private_answers(mid, host["member_id"])}
    private_tools = Tools(private_snapshot)
    voice = private_tools.execute("get_utterances", {"ids": [voice_id]})["utterances"][0]
    assert voice["source"] == "ai_voice" and "VOICE-CONTEXT-487" in voice["text"]
    summ = private_tools.execute("get_utterances", {"ids": [snapshot["summaries"][0]["id"]]})
    assert "SUMMARY-CONTEXT-512" in str(summ)
    with pytest.raises(ValueError):
        private_tools.execute("get_utterances", {"ids": [private_snapshot["private_history"][0]["id"]]})

    class CaptureClient:
        def __init__(self, target):
            self.messages = []
            self.target = target

        async def complete(self, messages):
            self.messages = list(messages)
            if len([m for m in messages if m["role"] == "assistant"]) == 0:
                return {"kind": "tool", "tool": "get_utterances", "arguments": {"ids": [self.target]}}
            return {"kind": "final", "answer": "根据可见上下文回答", "citations": [self.target]}

    private_model = CaptureClient(voice_id)
    result = asyncio.run(
        Agent(Settings(llm_mode="local"), private_model).ask(private_snapshot, "你刚才说什么")
    )
    assert result["status"] == "succeeded"
    assert "SECRET-123" in str(private_model.messages) and "VOICE-CONTEXT-487" in str(private_model.messages)
    public_model = CaptureClient(ident)
    asyncio.run(Agent(Settings(llm_mode="local"), public_model).ask(snapshot, "测试时间"))
    assert "SECRET-123" not in str(public_model.messages)
    summary = asyncio.run(Agent(Settings()).summarize(snapshot))
    assert "VOICE-CONTEXT-487" in str(summary) and "SECRET-123" not in str(summary)


def test_ending_meeting_cancels_inflight_private_chat_without_recreating_records(setup, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    client, app, host, path, headers = setup
    started = threading.Event()

    async def slow(self, snapshot, question):
        started.set()
        await asyncio.sleep(60)
        return {"answer": "LATE-PRIVATE", "citations": [], "trace": [], "status": "succeeded"}

    monkeypatch.setattr(Agent, "ask", slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            client.post,
            path + "/private-chat",
            headers=headers,
            json={"question": "等待回答", "request_key": "slow-private"},
        )
        assert started.wait(3)
        assert client.get(path, headers=headers).json()["runtime"]["busy"] is False
        assert client.post(path + "/end", headers=headers).status_code == 200
        assert future.result(timeout=3).status_code == 409
    assert client.get(path + "/private-chat", headers=headers).json()["answers"] == []
    with pytest.raises(ValueError, match="会议已结束"):
        app.state.store.save_private_answer(
            host["meeting_id"], host["member_id"], "late", {}, 0, "mock", "late-key"
        )


def test_migration_removes_legacy_text_answers_from_shared_records(tmp_path):
    store = Store(tmp_path)
    active, ended = store.create("进行中", "a"), store.create("已结束", "b")
    result = {"answer": "旧文字回答", "citations": [], "trace": [], "status": "mock"}
    ids = []
    for session in (active, ended):
        ident = store.save_answer(
            session["meeting_id"], session["member_id"], "旧文字问题", result, 0, "mock", True, "old"
        )
        store.confirm(session["meeting_id"], ident, session["member_id"], "旧文字采纳", "action")
        ids.append(ident)
    with store.connect() as db:
        db.execute("UPDATE answers SET spoken=0")
    store.set_status(ended["meeting_id"], "ended")
    migrated = Store(tmp_path)
    assert migrated.private_answers(active["meeting_id"], active["member_id"])[0]["id"] == ids[0]
    for session in (active, ended):
        snap = migrated.snapshot(session["meeting_id"])
        assert snap["answers"] == [] and snap["decisions"] == []
    assert migrated.private_answers(ended["meeting_id"], ended["member_id"]) == []
    assert len(Store(tmp_path).private_answers(active["meeting_id"], active["member_id"])) == 1


@pytest.mark.parametrize("private", [False, True])
def test_general_answers_without_meeting_citations_are_preserved(tmp_path, private):
    store = Store(tmp_path)
    session = store.create("空会议", "主持人")
    snapshot = store.snapshot(session["meeting_id"])
    if private:
        snapshot["private_history"] = []

    class Client:
        async def complete(self, messages):
            assert "会议资料是回答的参考" in messages[0]["content"]
            return {
                "kind": "final",
                "answer": "你好！可以和我聊一般问题，也可以结合会议记录讨论。",
                "citations": [],
            }

    result = asyncio.run(Agent(Settings(llm_mode="local"), Client()).ask(snapshot, "你好"))
    assert result["status"] == "succeeded"
    assert result["answer"] == "你好！可以和我聊一般问题，也可以结合会议记录讨论。"
    assert result["citations"] == [] and result["trace"] == []


def test_missing_meeting_evidence_does_not_discard_general_advice(tmp_path):
    store = Store(tmp_path)
    session = store.create("讨论", "主持人")
    snapshot = store.snapshot(session["meeting_id"])
    advice = "会议记录中未找到发布计划。一般建议先确定验收标准，再安排测试与发布；这是建议，并非会议决定。"

    class Client:
        calls = 0

        async def complete(self, messages):
            self.calls += 1
            if self.calls == 1:
                return {"kind": "tool", "tool": "search_meeting", "arguments": {"keywords": ["发布"]}}
            return {"kind": "final", "answer": advice, "citations": []}

    result = asyncio.run(Agent(Settings(llm_mode="local"), Client()).ask(snapshot, "我们的发布应该怎么安排"))
    assert result["status"] == "succeeded" and result["answer"] == advice
    assert result["trace"][0]["result"]["utterances"] == []


def test_private_general_answer_is_not_shared_and_is_deleted_at_meeting_end(setup, monkeypatch):
    from meeting_app.agent import ModelClient

    client, app, host, path, headers = setup
    app.state.settings.llm_mode = "local"

    async def complete(self, messages):
        return {"kind": "final", "answer": "PRIVATE-GENERAL：你好，很高兴和你交流。", "citations": []}

    monkeypatch.setattr(ModelClient, "complete", complete)
    response = client.post(
        path + "/private-chat", headers=headers, json={"question": "你好", "request_key": "greeting-private"}
    )
    assert response.status_code == 200
    own = client.get(path + "/private-chat", headers=headers).json()["answers"][0]
    assert own["status"] == "succeeded" and "PRIVATE-GENERAL" in own["answer"]
    assert own["citations"] == []
    assert "PRIVATE-GENERAL" not in client.get(path, headers=headers).text
    assert "PRIVATE-GENERAL" not in client.get(path + "/export", headers=headers).text
    assert client.post(path + "/end", headers=headers).status_code == 200
    assert client.get(path + "/private-chat", headers=headers).json()["answers"] == []


def test_uncited_empty_answers_still_fail(tmp_path):
    store = Store(tmp_path)
    session = store.create("讨论", "主持人")

    class Client:
        async def complete(self, messages):
            return {"kind": "final", "answer": "  ", "citations": []}

    result = asyncio.run(
        Agent(Settings(llm_mode="local"), Client()).ask(store.snapshot(session["meeting_id"]), "你好")
    )
    assert result["status"] == "failed"
