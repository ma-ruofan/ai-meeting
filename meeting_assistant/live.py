"""Single-room voice demo. Microphone access starts only on explicit UI action."""

import queue
import re
import threading
import uuid
import wave
from collections import deque

from filelock import FileLock, Timeout

from .agent import ask_meeting
from .llm import ChatClient
from .models import AppError
from .tts import speak

STATUS = {
    "thinking": "正在查询",
    "speaking": "正在播报",
    "spoken": "播报完成",
    "failed": "问答失败",
    "tts_failed": "回答已生成，播报失败",
    "cancelled": "已停止，未完成播报",
    "interrupted": "异常中断，未确认播报完成",
}


class WakeGate:
    """Text trigger after ASR, not an acoustic keyword detector."""

    pattern = re.compile(r"小[kK凯开][，,。.!！?？\s]*小[kK凯开]")

    def __init__(self):
        self.pending = None
        self.previous = ("", 0, [])

    def feed(self, text, start, end, ident, final=True):
        previous, previous_end, previous_ids = self.previous
        joined = previous + text if start - previous_end < 2 else text
        ids = previous_ids + [ident] if start - previous_end < 2 else [ident]
        self.previous = (text[-12:], end, [ident])
        if self.pending and start > self.pending[1] + 8:
            self.pending = None
        match = self.pattern.search(joined) if not self.pending else None
        if match:
            question = joined[match.end() :].strip(" ，,。.!！?？")
            self.pending = [question, end, ids, start]
        elif self.pending:
            self.pending[0] += text
            self.pending[2].append(ident)
        if self.pending and self.pending[0] and final:
            question, _, ids, question_time = self.pending
            self.pending = None
            self.previous = ("", 0, [])
            return question[:2000], question_time, list(dict.fromkeys(ids))
        return None


class Utterances:
    """RMS endpointing on 20 ms PCM frames, with pre-roll and bounded chunks."""

    def __init__(self, threshold, silence):
        self.threshold, self.silence = threshold, silence
        self.pre = deque(maxlen=15)
        self.frames = []
        self.quiet = 0

    def reset(self):
        self.frames = []
        self.pre.clear()
        self.quiet = 0

    def feed(self, pcm, start):
        import numpy as np

        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        loud = float(np.sqrt(np.mean(samples * samples))) >= self.threshold
        if not self.frames:
            self.pre.append((pcm, start))
            if not loud:
                return None
            self.frames = list(self.pre)
            self.pre.clear()
        else:
            self.frames.append((pcm, start))
        self.quiet = 0 if loud else self.quiet + 0.02
        final = self.quiet >= self.silence
        if final or len(self.frames) >= 400:
            result = b"".join(x[0] for x in self.frames), self.frames[0][1], start + 0.02, final
            self.reset()
            return result
        return None


def answer_public(repo, client, meeting_id, question, when, ids, stop, clock, speaker=speak):
    ident = repo.begin_dialogue(meeting_id, question, when, ids)
    try:
        if stop.is_set():
            repo.update_dialogue(ident, status="cancelled")
            return
        run_id = ask_meeting(repo, client, meeting_id, question, spoken=True)
        run = next(r for r in repo.agent_runs(meeting_id) if r["id"] == run_id)
        answer = run["result"]["answer"]
        repo.update_dialogue(
            ident,
            status="thinking",
            answer=answer,
            evidence_ids=run["result"]["evidence_ids"],
            agent_run_id=run_id,
        )
    except Exception as exc:
        repo.update_dialogue(
            ident,
            status="failed",
            error=str(exc) if isinstance(exc, AppError) else "模型调用失败，请检查配置。",
        )
        return
    if stop.is_set():
        repo.update_dialogue(ident, status="cancelled")
        return
    repo.update_dialogue(ident, status="speaking", answer_start=clock())
    try:
        completed = speaker(answer, client.settings.tts_voice, stop)
        repo.update_dialogue(
            ident, status="spoken" if completed else "cancelled", answer_end=clock()
        )
    except Exception as exc:
        repo.update_dialogue(
            ident,
            status="tts_failed",
            answer_end=clock(),
            error=str(exc) if isinstance(exc, AppError) else "本机语音播报失败。",
        )


class LiveManager:
    def __init__(self, repo):
        self.repo = repo
        self.thread = None
        self.stop = threading.Event()
        self.muted = threading.Event()
        self.guard = threading.Lock()
        self.meeting_id = None
        self.state = "尚未开始"
        self.error = None
        self.seconds = 0.0

    @property
    def active(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, settings, title, device=None):
        with self.guard:
            if self.active:
                raise AppError("已有现场会议正在运行，请先结束。")
            if (
                not 0 < settings.live_rms_threshold < 1
                or not 0.2 <= settings.live_silence_seconds <= 3
            ):
                raise AppError("收音阈值应在 0～1 之间，静音分句时间应在 0.2～3 秒之间。")
            lock = FileLock(str(self.repo.root / "live.lock"), thread_local=False)
            try:
                lock.acquire(timeout=0)
            except Timeout as exc:
                raise AppError("另一个进程正在收音，请先停止。") from exc
            try:
                ident = uuid.uuid4().hex
                relative = f"audio/{ident}/live.wav"
                self.repo.path(relative).parent.mkdir(parents=True, exist_ok=True)
                self.meeting_id = self.repo.create_meeting(
                    title or "小K现场会议", 0, "live", meeting_id=ident, audio_path=relative
                )
                session = self.repo.begin_live(
                    ident,
                    {
                        "asr_model": settings.live_asr_model,
                        "device": "cpu",
                        "wake_method": "asr_text",
                    },
                )
                self.stop.clear()
                self.muted.clear()
                self.error, self.seconds, self.state = None, 0.0, "加载语音模型（首次可能下载）"
                self.thread = threading.Thread(
                    target=self._run, args=(settings, device, session, lock), daemon=True
                )
                self.thread.start()
                return ident
            except Exception:
                lock.release()
                raise

    def request_stop(self):
        self.stop.set()
        self.state = "正在停止并保存录音"

    def _run(self, settings, device, session, lock):
        capture = None
        frames = queue.Queue(maxsize=1500)
        try:
            import numpy as np
            import sounddevice as sd
            from faster_whisper import WhisperModel

            model = WhisperModel(settings.live_asr_model, device="cpu", compute_type="int8")
            if self.stop.is_set():
                return
            path = self.repo.path(self.repo.meeting(self.meeting_id)["audio_path"])

            def record():
                try:
                    with wave.open(str(path), "wb") as wav:
                        wav.setnchannels(1)
                        wav.setsampwidth(2)
                        wav.setframerate(16000)
                        with sd.RawInputStream(
                            samplerate=16000,
                            blocksize=320,
                            channels=1,
                            dtype="int16",
                            device=device,
                        ) as stream:
                            self.state = "正在听会 · 说“小K小K”后提问"
                            self.repo.finish_live(session, "active")
                            was_muted = False
                            while not self.stop.is_set() and self.seconds < min(
                                settings.max_audio_seconds, 600
                            ):
                                raw, overflow = stream.read(320)
                                if overflow:
                                    raise AppError("麦克风音频溢出，已停止，录音可能不完整。")
                                pcm = bytes(raw)
                                wav.writeframesraw(pcm)
                                start = self.seconds
                                self.seconds += 0.02
                                muted = self.muted.is_set()
                                # A slow LLM/TTS turn must not fill the ASR queue with silence markers.
                                if not muted or not was_muted:
                                    frames.put_nowait((None if muted else pcm, start))
                                was_muted = muted
                except Exception as exc:
                    self.error = (
                        str(exc)
                        if isinstance(exc, AppError)
                        else ("收音失败或处理积压：请检查麦克风权限、设备和 CPU 负载。")
                    )
                finally:
                    self.stop.set()

            capture = threading.Thread(target=record, daemon=True)
            capture.start()
            detector = Utterances(settings.live_rms_threshold, settings.live_silence_seconds)
            gate = WakeGate()

            def process(chunk):
                pcm, start, end, final = chunk
                if end - start < 0.3:
                    return
                self.state = "识别会议发言"
                with self.repo.processing():
                    result, _ = model.transcribe(
                        np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768,
                        language="zh",
                        beam_size=1,
                        vad_filter=True,
                        condition_on_previous_text=False,
                    )
                    text = "".join(s.text for s in result).strip()
                if not text:
                    return
                sid = self.repo.append_live_text(self.meeting_id, text, start, end)
                question = gate.feed(text, start, end, sid, final)
                if question and not self.stop.is_set():
                    self.state = "小K正在查询与回答（期间暂停转录）"
                    self.muted.set()
                    try:
                        answer_public(
                            self.repo,
                            ChatClient(settings),
                            self.meeting_id,
                            *question,
                            self.stop,
                            lambda: self.seconds,
                        )
                    finally:
                        # Frames captured during the turn stay in WAV but are not fed back to ASR.
                        self.stop.wait(0.4)
                        self.muted.clear()
                        detector.reset()
                self.state = "正在听会 · 说“小K小K”后提问"

            while not self.stop.is_set() or not frames.empty():
                try:
                    pcm, start = frames.get(timeout=0.1)
                except queue.Empty:
                    continue
                if pcm is None:
                    detector.reset()
                    continue
                chunk = detector.feed(pcm, start)
                if chunk:
                    process(chunk)
            if detector.frames:
                process(
                    (
                        b"".join(x[0] for x in detector.frames),
                        detector.frames[0][1],
                        self.seconds,
                        True,
                    )
                )
        except Exception as exc:
            self.error = (
                str(exc)
                if isinstance(exc, AppError)
                else ("现场会议启动或识别失败，请检查 voice 依赖、模型下载和麦克风权限。")
            )
        finally:
            self.stop.set()
            if capture:
                capture.join(timeout=3)
            self.repo.update_duration(self.meeting_id, self.seconds)
            self.repo.finish_live(session, "failed" if self.error else "completed", self.error)
            self.state = "会议已结束" if not self.error else "会议已停止"
            lock.release()
