import asyncio
import io
import json
import re
import shutil
import time
import wave
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .agent import Agent
from .config import ROOT, Settings
from .speech import Speech, WakeDetector
from .store import Store, uid


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Create(Strict):
    title: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=30)


class Join(Strict):
    code: str = Field(min_length=8, max_length=8)
    name: str = Field(min_length=1, max_length=30)
    consent: Literal[True]


class Attendee(Strict):
    name: str = Field(min_length=1, max_length=30)
    consent: Literal[True]


class Utterance(Strict):
    text: str = Field(min_length=1, max_length=4000)
    speaker: str = Field(default="未标注", min_length=1, max_length=40)
    start: float = Field(default=0, ge=0, le=100000, allow_inf_nan=False)
    end: float = Field(default=0, ge=0, le=100000, allow_inf_nan=False)
    request_key: str = Field(min_length=8, max_length=100)


class Edit(Strict):
    text: str = Field(min_length=1, max_length=4000)
    speaker: str = Field(min_length=1, max_length=40)
    version: int = Field(ge=1)


class Ask(Strict):
    question: str = Field(min_length=1, max_length=1000)
    request_key: str = Field(min_length=8, max_length=100)
    spoken: bool = False


class Confirm(Strict):
    text: str = Field(min_length=1, max_length=4000)
    kind: Literal["decision", "action"] = "decision"


class VoiceChange(Strict):
    enabled: bool


@dataclass
class RoomRuntime:
    connections: set = field(default_factory=set)
    audio_socket: object = None
    voice_state: str = "idle"
    answer_id: str | None = None
    busy: bool = False
    tasks: set = field(default_factory=set)
    reset_audio: int = 0
    speaking_until: float = 0


def create_app(settings=None, agent=None, speech=None):
    settings = settings or Settings()
    store = Store(settings.data_dir)
    agent = agent or Agent(settings)
    speech = speech or Speech(settings)
    wake = WakeDetector(settings.kws_dir)
    rooms = {}

    def room(mid):
        return rooms.setdefault(mid, RoomRuntime())

    @asynccontextmanager
    async def lifespan(app):
        yield
        tasks = [task for r in rooms.values() for task in r.tasks]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        speech.close()

    app = FastAPI(title="小K · 会议工作台", version="0.2.0", lifespan=lifespan)
    app.state.store, app.state.rooms, app.state.settings = store, rooms, settings

    @app.exception_handler(ValueError)
    async def validation_error(request, exc):
        return Response(
            json.dumps({"detail": str(exc)}, ensure_ascii=False),
            status_code=400,
            media_type="application/json",
        )

    def member(mid: str, authorization: str = Header(default="")):
        token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        result = store.auth(mid, token)
        if result is None:
            raise HTTPException(401, "请重新加入会议")
        return result

    def host(mid: str, current=Depends(member)):
        if current["role"] != "host":
            raise HTTPException(403, "仅主持人可以执行此操作")
        return current

    def public(mid):
        snapshot = store.snapshot(mid)
        r = room(mid)
        snapshot["runtime"] = {
            "voice_state": r.voice_state,
            "answer_id": r.answer_id,
            "busy": r.busy,
            "recording": r.audio_socket is not None,
        }
        snapshot["model_mode"] = settings.llm_mode
        return snapshot

    async def publish(mid):
        r = room(mid)
        try:
            payload = {"type": "snapshot", "data": public(mid)}
        except ValueError:
            payload = {"type": "deleted"}

        async def send(ws):
            try:
                await asyncio.wait_for(ws.send_json(payload), 2)
            except (TimeoutError, Exception):
                r.connections.discard(ws)
                try:
                    await ws.close()
                except Exception:
                    pass

        await asyncio.gather(*(send(ws) for ws in list(r.connections)))

    def spawn(mid, coroutine):
        r = room(mid)
        task = asyncio.create_task(coroutine)
        r.tasks.add(task)
        task.add_done_callback(r.tasks.discard)
        return task

    def require_ready(mid):
        if store.snapshot(mid)["meeting"]["status"] == "ended":
            raise HTTPException(409, "会议已结束；仍可查看、问答和导出")

    async def run_ask(mid, current, body):
        r = room(mid)
        if r.busy:
            raise HTTPException(409, "小K正在处理上一项请求")
        prior = next(
            (a for a in store.snapshot(mid)["answers"] if a["request_key"] == body.request_key), None
        )
        if prior:
            return {"id": prior["id"]}
        r.busy = True
        if body.spoken:
            r.voice_state = "thinking"
            r.reset_audio += 1
        await publish(mid)
        try:
            snapshot = store.snapshot(mid)
            result = await agent.ask(snapshot, body.question)
            ident = store.save_answer(
                mid,
                current["id"],
                body.question,
                result,
                snapshot["meeting"]["revision"],
                settings.llm_mode,
                body.spoken,
                body.request_key,
            )
            if body.spoken and result["status"] != "failed":
                r.voice_state, r.answer_id = "speaking", ident
                r.speaking_until = time.monotonic() + 90
            return {"id": ident}
        finally:
            r.busy = False
            if r.voice_state == "thinking":
                r.voice_state = "listening" if r.audio_socket else "idle"
            await publish(mid)

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "model_mode": settings.llm_mode,
            "speech": speech.available(),
            "version": "0.2.0",
            "wake_phrase": "小K小K",
            "speaker_threshold_calibrated": False,
        }

    @app.post("/api/meetings")
    async def create(body: Create):
        return store.create(body.title, body.name)

    @app.post("/api/join")
    async def join(body: Join):
        result = store.join(body.code, body.name)
        await publish(result["meeting_id"])
        return result

    @app.post("/api/meetings/{mid}/members")
    async def add_attendee(mid: str, body: Attendee, current=Depends(host)):
        result = store.add_attendee(mid, body.name)
        await publish(mid)
        return result

    def managed_attendee(mid, ident):
        result = store.attendee(mid, ident)
        if result is None:
            raise HTTPException(404, "本场未找到由主持人添加的参会者")
        return result

    @app.get("/api/meetings/{mid}")
    def snapshot(mid: str, current=Depends(member)):
        return public(mid)

    @app.post("/api/meetings/{mid}/utterances")
    async def add(mid: str, body: Utterance, current=Depends(host)):
        require_ready(mid)
        if body.end < body.start:
            raise ValueError("结束时间不能早于开始时间")
        ident = store.add_utterance(
            mid, body.text, body.speaker, body.start, body.end, "manual", body.request_key
        )
        await publish(mid)
        return {"id": ident}

    @app.patch("/api/meetings/{mid}/utterances/{ident}")
    async def edit(mid: str, ident: str, body: Edit, current=Depends(host)):
        store.edit(mid, ident, body.text, body.speaker, body.version)
        await publish(mid)
        return {"ok": True}

    @app.post("/api/meetings/{mid}/ask")
    async def ask(mid: str, body: Ask, current=Depends(member)):
        if body.spoken and current["role"] != "host":
            raise HTTPException(403, "语音播报由主设备发起")
        return await run_ask(mid, current, body)

    @app.post("/api/meetings/{mid}/answers/{ident}/confirm")
    async def confirm(mid: str, ident: str, body: Confirm, current=Depends(host)):
        store.confirm(mid, ident, current["id"], body.text, body.kind)
        await publish(mid)
        return {"ok": True}

    @app.post("/api/meetings/{mid}/summary")
    async def summary(mid: str, current=Depends(host)):
        r = room(mid)
        if r.busy:
            raise HTTPException(409, "小K正在处理请求")
        r.busy = True
        await publish(mid)
        try:
            snapshot = store.snapshot(mid)
            data = await agent.summarize(snapshot)
            store.save_summary(mid, data, snapshot["meeting"]["revision"], settings.llm_mode)
        finally:
            r.busy = False
            await publish(mid)
        return {"ok": True}

    @app.post("/api/meetings/{mid}/end")
    async def end(mid: str, current=Depends(host)):
        r = room(mid)
        if r.audio_socket or r.tasks or r.busy:
            raise HTTPException(409, "请先停止录音并等待正在处理的任务完成")
        store.set_status(mid, "ended")
        await publish(mid)
        return {"ok": True}

    @app.delete("/api/meetings/{mid}")
    async def delete(mid: str, current=Depends(host)):
        r = room(mid)
        if r.audio_socket or r.tasks or r.busy:
            raise HTTPException(409, "请先停止录音并等待任务结束，再删除")
        folder = settings.data_dir / "audio" / mid
        # Make failed filesystem deletion visible rather than claiming complete deletion.
        if folder.exists():
            shutil.rmtree(folder)
        store.delete(mid)
        await publish(mid)
        for ws in list(r.connections):
            await ws.close(code=1000)
        rooms.pop(mid, None)
        return {"ok": True}

    async def upload_bytes(file):
        content = await file.read(settings.max_upload + 1)
        await file.close()
        if not content or len(content) > settings.max_upload:
            raise HTTPException(413, "音频为空或超过50MB")
        return content

    @app.post("/api/meetings/{mid}/voiceprint")
    async def enroll(
        mid: str, consent: bool = Form(...), file: list[UploadFile] = File(...), current=Depends(member)
    ):
        return await enroll_for(mid, current, consent, file)

    @app.post("/api/meetings/{mid}/members/{ident}/voiceprint")
    async def enroll_attendee(
        mid: str,
        ident: str,
        consent: bool = Form(...),
        file: list[UploadFile] = File(...),
        current=Depends(host),
    ):
        return await enroll_for(mid, managed_attendee(mid, ident), consent, file)

    async def enroll_for(mid, target, consent, file):
        require_ready(mid)
        if not consent:
            raise ValueError("登记声纹需要本人确认")
        if not speech.available()["speaker"]:
            raise ValueError("声纹模型未下载，请在主电脑运行模型安装脚本")
        if not 1 <= len(file) <= 3:
            raise ValueError("每次登记请提供1～3段本人的语音")
        contents = [await upload_bytes(item) for item in file]
        r = room(mid)
        if r.tasks or r.audio_socket or r.busy:
            raise HTTPException(409, "请在录音和处理任务停止后登记声纹")
        r.busy = True
        try:
            vectors = [await speech.run("enroll", content) for content in contents]
            vector = np.mean(vectors, axis=0)
            vector = vector / max(float(np.linalg.norm(vector)), 1e-9)
            store.enroll(mid, target["id"], target["name"], vector.tolist(), settings.speaker_path.name)
        finally:
            r.busy = False
            await publish(mid)
        return {"ok": True}

    @app.patch("/api/meetings/{mid}/voiceprint")
    async def voice_toggle(mid: str, body: VoiceChange, current=Depends(member)):
        store.voice_change(mid, current["id"], body.enabled)
        await publish(mid)
        return {"ok": True}

    @app.delete("/api/meetings/{mid}/voiceprint")
    async def voice_delete(mid: str, current=Depends(member)):
        store.voice_change(mid, current["id"])
        await publish(mid)
        return {"ok": True}

    @app.patch("/api/meetings/{mid}/members/{ident}/voiceprint")
    async def revoke_attendee_voice(mid: str, ident: str, body: VoiceChange, current=Depends(host)):
        managed_attendee(mid, ident)
        if room(mid).busy:
            raise HTTPException(409, "请等待正在处理的任务完成后撤回声纹")
        if body.enabled:
            raise ValueError("重新启用需要本人同意后重新登记")
        store.voice_change(mid, ident, False)
        await publish(mid)
        return {"ok": True}

    @app.delete("/api/meetings/{mid}/members/{ident}/voiceprint")
    async def delete_attendee_voice(mid: str, ident: str, current=Depends(host)):
        managed_attendee(mid, ident)
        if room(mid).busy:
            raise HTTPException(409, "请等待正在处理的任务完成后删除声纹")
        store.voice_change(mid, ident)
        await publish(mid)
        return {"ok": True}

    def permitted_match(mid, match):
        allowed = {c["member_id"] for c in store.voice_candidates(mid)}
        if match.get("member_id") and match["member_id"] not in allowed:
            return {"name": "未知说话人", "status": "revoked"}
        return match

    async def process_job(mid, ident):
        try:
            job = store.job(mid, ident)
            store.update_job(mid, ident, "running")
            await publish(mid)
            content = (settings.data_dir / "audio" / mid / job["filename"]).read_bytes()
            result = await speech.run("transcribe", content, store.voice_candidates(mid))
            for i, seg in enumerate(result["segments"]):
                match = permitted_match(mid, seg["match"])
                store.add_utterance(
                    mid, seg["text"], match["name"], seg["start"], seg["end"], "asr", f"{ident}:{i}", match
                )
            store.update_job(mid, ident, "succeeded")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = (
                str(exc) if isinstance(exc, ValueError) else "语音处理失败，音频已保留；检查模型或稍后重试"
            )
            store.update_job(mid, ident, "failed", error)
        finally:
            await publish(mid)

    @app.post("/api/meetings/{mid}/audio")
    async def upload(
        mid: str,
        request_key: str = Form(..., min_length=8, max_length=100),
        file: UploadFile = File(...),
        current=Depends(host),
    ):
        require_ready(mid)
        r = room(mid)
        if r.audio_socket or r.tasks or r.busy:
            raise HTTPException(409, "请等待当前音频处理结束")
        if not speech.available()["asr"]:
            raise ValueError("转录模型未就绪，请运行模型安装脚本")
        content = await upload_bytes(file)
        folder = settings.data_dir / "audio" / mid
        folder.mkdir(parents=True, exist_ok=True)
        name = uid() + ".audio"
        ident, created = store.add_job(mid, name, request_key)
        if created:
            try:
                (folder / name).write_bytes(content)
            except OSError:
                store.update_job(mid, ident, "failed", "音频保存失败，请检查磁盘空间")
                raise ValueError("音频保存失败")
            spawn(mid, process_job(mid, ident))
        return {"id": ident}

    @app.post("/api/meetings/{mid}/jobs/{ident}/retry")
    async def retry(mid: str, ident: str, current=Depends(host)):
        r = room(mid)
        job = store.job(mid, ident)
        if not job or job["status"] != "failed" or r.tasks or r.audio_socket or r.busy:
            raise HTTPException(409, "任务不可重试或有其他任务正在运行")
        store.update_job(mid, ident, "queued")
        spawn(mid, process_job(mid, ident))
        return {"ok": True}

    @app.get("/api/meetings/{mid}/export")
    def export(mid: str, current=Depends(member)):
        s = store.snapshot(mid)
        lines = [
            "# " + s["meeting"]["title"],
            "",
            "模型模式：" + settings.llm_mode,
            "",
            "## 人类会议记录",
            "",
        ]
        for u in s["utterances"]:
            lines.extend(
                [f"- [{u['start']:.1f}s] {u['speaker']}：{u['text']}（发言 {u['id']} / v{u['version']}）", ""]
            )
        lines += ["## 会议纪要草稿", ""]
        if s["summaries"]:
            summ = s["summaries"][-1]
            lines += ["依据已变化，请复核" if summ["stale"] else "待人工复核", summ["data"]["overview"], ""]
            lines += ["- " + x for x in summ["data"]["highlights"]]
        lines += ["", "## AI回答与建议（不自动构成决定）", ""]
        confirmed = {d["answer_id"] for d in s["decisions"]}
        for a in s["answers"]:
            lines += [
                f"### 问：{a['question']}",
                f"模式：{a['mode']}；状态：{a['status']}；"
                + ("已采纳" if a["id"] in confirmed else "未采纳"),
                "依据已变化" if a["stale"] else "",
                a["answer"],
                "引用：" + ", ".join(f"{c['id']}@v{c['version']}" for c in a["citations"]),
                "",
            ]
        lines += ["## 主持人确认的决定与待办", ""]
        lines += [
            f"- [{'待办' if d['kind'] == 'action' else '决定'}] {d['text']}（来自AI回答 {d['answer_id']}）"
            for d in s["decisions"]
        ]
        return Response(
            "\n".join(lines),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="meeting.md"'},
        )

    async def ws_auth(ws, mid, only_host=False):
        await ws.accept()
        try:
            msg = await asyncio.wait_for(ws.receive_json(), 5)
            token = msg.get("token", "") if isinstance(msg, dict) else ""
            current = store.auth(mid, token) if isinstance(token, str) and len(token) <= 200 else None
            if not current or (only_host and current["role"] != "host"):
                await ws.close(code=4401)
                return None
            return current
        except Exception:
            await ws.close(code=4401)
            return None

    @app.websocket("/ws/meetings/{mid}")
    async def events(ws: WebSocket, mid: str):
        if not await ws_auth(ws, mid):
            return
        r = room(mid)
        r.connections.add(ws)
        try:
            await ws.send_json({"type": "snapshot", "data": public(mid)})
            while True:
                message = await ws.receive_text()
                if message == "ping":
                    await ws.send_json({"type": "pong"})
                elif message == "sync":
                    await ws.send_json({"type": "snapshot", "data": public(mid)})
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            r.connections.discard(ws)

    @app.websocket("/ws/audio/{mid}")
    async def audio(ws: WebSocket, mid: str):
        current = await ws_auth(ws, mid, True)
        if not current:
            return
        r = room(mid)
        if r.audio_socket or r.tasks or r.busy or store.snapshot(mid)["meeting"]["status"] == "ended":
            await ws.send_json({"type": "error", "message": "会议已有主录音设备、任务未结束或会议已结束"})
            await ws.close(code=4409)
            return
        if not speech.available()["asr"]:
            await ws.send_json({"type": "error", "message": "请先下载转录模型"})
            await ws.close(code=4409)
            return
        try:
            kws_stream = await asyncio.to_thread(wake.create)
        except Exception:
            kws_stream = None
        r.audio_socket, r.voice_state = ws, "listening"
        store.set_status(mid, "recording")
        await ws.send_json({"type": "ready", "kws": kws_stream is not None})
        await publish(mid)
        buffer, pre = bytearray(), bytearray()
        position = max((u["end"] for u in store.snapshot(mid)["utterances"]), default=0.0)
        session_start = position
        start = position
        silent, reset = 0.0, r.reset_audio
        question_mode = False
        question_started = 0.0
        queue = asyncio.Queue(maxsize=3)
        session = uid()
        counter = 0
        folder = settings.data_dir / "audio" / mid
        folder.mkdir(parents=True, exist_ok=True)

        async def consume():
            while True:
                item = await queue.get()
                if item is None:
                    queue.task_done()
                    break
                content, offset, key, is_question = item
                try:
                    result = await speech.run("transcribe", content, store.voice_candidates(mid))
                    if is_question:
                        question = "".join(s["text"] for s in result["segments"])
                        question = re.sub(r"^(.*?小[ Kk开凯克]+){1,2}[，,。\s]*", "", question).strip()
                        if question:
                            await run_ask(
                                mid, current, Ask(question=question[:1000], request_key=key, spoken=True)
                            )
                        else:
                            r.voice_state = "listening"
                            await publish(mid)
                    else:
                        for i, seg in enumerate(result["segments"]):
                            match = permitted_match(mid, seg["match"])
                            store.add_utterance(
                                mid,
                                seg["text"],
                                match["name"],
                                offset + seg["start"],
                                offset + seg["end"],
                                "asr",
                                f"{key}:{i}",
                                match,
                            )
                        await publish(mid)
                except Exception as exc:
                    if is_question:
                        r.voice_state = "listening"
                    try:
                        await ws.send_json(
                            {
                                "type": "error",
                                "message": str(exc)
                                if isinstance(exc, ValueError)
                                else "音频处理失败；已保存录音分片，可上传重试",
                            }
                        )
                    except Exception:
                        pass
                    await publish(mid)
                finally:
                    queue.task_done()

        consumer = spawn(mid, consume())

        async def flush():
            nonlocal buffer, start, counter, question_mode, silent
            if not buffer:
                return
            content = io.BytesIO()
            with wave.open(content, "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(16000)
                f.writeframes(buffer)
            key = f"{session}:{counter}"
            counter += 1
            (folder / f"{session}-{counter}.wav").write_bytes(content.getvalue())
            # Backpressure is explicit. Never silently drop chunks while displaying 'recording'.
            if queue.full():
                raise ValueError("转录处理跟不上录音，已停止；分片已保留，请缩短会议或调整模型")
            queue.put_nowait((content.getvalue(), start, key, question_mode))
            if question_mode:
                r.voice_state = "thinking"
                await publish(mid)
            buffer, question_mode, silent = bytearray(), False, 0.0
            start = position

        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("text"):
                    control = json.loads(msg["text"])
                    if control.get("type") == "stop":
                        break
                    if control.get("type") == "playback_done":
                        r.voice_state = "listening"
                        r.answer_id = None
                        r.reset_audio += 1
                    if control.get("type") == "wake" and r.voice_state == "listening":
                        await flush()
                        question_mode, question_started = True, position
                        r.voice_state = "question"
                    await publish(mid)
                    continue
                pcm = msg.get("bytes", b"")
                if not pcm or len(pcm) > 32000 or len(pcm) % 2:
                    raise ValueError("音频帧格式错误")
                seconds = len(pcm) / 32000
                position += seconds
                if position - session_start > settings.max_seconds:
                    raise ValueError("本次录音已达到30分钟限制，已停止并保留接收的音频")
                if r.speaking_until and time.monotonic() > r.speaking_until and r.voice_state == "speaking":
                    r.voice_state, r.answer_id = "listening", None
                    r.reset_audio += 1
                    await publish(mid)
                if reset != r.reset_audio:
                    buffer, pre, silent, start = bytearray(), bytearray(), 0.0, position
                    question_mode, reset = False, r.reset_audio
                    if kws_stream:
                        kws_stream = await asyncio.to_thread(wake.create)
                if r.voice_state in ("speaking", "thinking"):
                    start = position
                    continue
                if not question_mode and kws_stream and await asyncio.to_thread(wake.feed, kws_stream, pcm):
                    await flush()
                    buffer = bytearray(pre)
                    start = position - seconds - len(pre) / 32000
                    question_mode, question_started = True, position
                    r.voice_state = "question"
                    await publish(mid)
                energy = float(
                    np.sqrt(np.mean((np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768) ** 2))
                )
                if energy > 0.008 or buffer or question_mode:
                    if not buffer:
                        start = position - seconds
                    buffer.extend(pcm)
                    silent = silent + seconds if energy < 0.008 else 0
                pre.extend(pcm)
                pre = pre[-32000:]
                duration = len(buffer) / 32000
                if duration and (
                    (silent >= 0.9 and duration >= 1.5) or duration >= (20 if question_mode else 8)
                ):
                    await flush()
                if question_mode and position - question_started > 20:
                    await flush()
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception as exc:
            try:
                await ws.send_json(
                    {
                        "type": "error",
                        "message": str(exc) if isinstance(exc, ValueError) else "录音连接异常，已有记录保留",
                    }
                )
            except Exception:
                pass
        finally:
            # Preserve and finish already captured speech; deleting is blocked until worker completes.
            try:
                if buffer:
                    await flush()
            except Exception:
                pass
            await queue.put(None)
            await consumer
            r.audio_socket, r.voice_state, r.answer_id = None, "idle", None
            store.set_status(mid, "ready")
            await publish(mid)
            try:
                await ws.close()
            except Exception:
                pass

    dist = ROOT / "frontend" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/pcm-worklet.js")
        def worklet():
            return FileResponse(dist / "pcm-worklet.js", media_type="application/javascript")

        @app.get("/")
        def index():
            return FileResponse(dist / "index.html")

    return app


app = create_app()
