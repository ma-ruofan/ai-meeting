"""Speech inference runs in a spawned process on both macOS and Windows."""

import asyncio
import io
import json
import multiprocessing
import threading
import wave
from pathlib import Path

import av
import numpy as np
from opencc import OpenCC

CACHE = {}
SIMPLIFIED = OpenCC("t2s")


def decode_audio(content, max_seconds=1800):
    """Bound decoded audio, including files with misleading headers. PyAV bundles codecs."""
    parts, count = [], 0
    try:
        with av.open(io.BytesIO(content)) as container:
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            for frame in container.decode(audio=0):
                for converted in resampler.resample(frame):
                    chunk = converted.to_ndarray().flatten().astype(np.float32) / 32768
                    count += len(chunk)
                    if count > max_seconds * 16000:
                        raise ValueError("音频超过时长限制")
                    parts.append(chunk)
            for converted in resampler.resample(None):
                parts.append(converted.to_ndarray().flatten().astype(np.float32) / 32768)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("音频无法解码，请上传有效的 WAV/MP3/M4A 音频") from exc
    if not parts:
        raise ValueError("音频为空")
    data = np.concatenate(parts)
    if len(data) > max_seconds * 16000 or not np.isfinite(data).all():
        raise ValueError("音频数据无效或过长")
    return data


def wav_bytes(samples):
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return target.getvalue()


def embedding(samples, model_path):
    import sherpa_onnx

    if not Path(model_path).is_file():
        raise ValueError("声纹模型尚未下载，请运行 scripts/setup_models.py")
    if len(samples) < 16000 or float(np.sqrt(np.mean(samples**2))) < 0.003:
        raise ValueError("有效语音太短或音量太低，请重新录制")
    if model_path not in CACHE:
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=model_path, num_threads=1, provider="cpu")
        if not cfg.validate():
            raise ValueError("声纹模型配置无效")
        CACHE[model_path] = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
    extractor = CACHE[model_path]
    stream = extractor.create_stream()
    stream.accept_waveform(16000, np.ascontiguousarray(samples, dtype=np.float32))
    stream.input_finished()
    if not extractor.is_ready(stream):
        raise ValueError("有效语音不足，无法提取声纹")
    value = np.asarray(extractor.compute(stream), dtype=np.float32)
    return value / max(float(np.linalg.norm(value)), 1e-9)


def match_embedding(vector, candidates, threshold, margin):
    scored = []
    for row in candidates:
        v = np.asarray(json.loads(row["embedding"]), dtype=np.float32)
        if v.shape != vector.shape:
            continue
        score = float(np.dot(vector, v) / max(float(np.linalg.norm(v)), 1e-9))
        scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored:
        return {"name": "未知说话人", "status": "unknown", "score": None}
    score, row = scored[0]
    gap = score - scored[1][0] if len(scored) > 1 else 1.0
    accepted = score >= threshold and gap >= margin
    return {
        "name": row["name"] if accepted else "未知说话人",
        "member_id": row["member_id"] if accepted else None,
        "status": "matched" if accepted else "uncertain",
        "score": round(score, 3),
        "margin": round(gap, 3),
        "threshold": threshold,
    }


def infer(task, options):
    samples = decode_audio(task["content"], 90 if task["kind"] == "enroll" else options["max_seconds"])
    if task["kind"] == "enroll":
        if not 3 <= len(samples) / 16000 <= 90:
            raise ValueError("登记录音需为3～90秒，建议10～20秒")
        return embedding(samples, options["speaker_path"]).tolist()
    from faster_whisper import WhisperModel

    model_path = options["asr_path"]
    if not Path(model_path).is_dir():
        raise ValueError("转录模型尚未下载，请运行 scripts/setup_models.py")
    if model_path not in CACHE:
        CACHE[model_path] = WhisperModel(
            model_path, device="cpu", compute_type="int8", cpu_threads=2, num_workers=1
        )
    segments, _ = CACHE[model_path].transcribe(
        samples,
        language="zh",
        beam_size=2,
        vad_filter=True,
        word_timestamps=True,
        condition_on_previous_text=False,
    )
    result = []
    for segment in segments:
        if not segment.text.strip() or segment.no_speech_prob > 0.8:
            continue
        # Label short windows separately. Do not assume an ASR sentence is one speaker.
        windows = []
        start = segment.start
        while start < segment.end:
            end = min(start + 3, segment.end)
            sample = samples[int(start * 16000) : int(end * 16000)]
            match = {"name": "未知说话人", "status": "unknown", "score": None}
            if task.get("candidates") and len(sample) >= 16000:
                try:
                    vector = embedding(sample, options["speaker_path"])
                    match = match_embedding(
                        vector, task["candidates"], options["threshold"], options["margin"]
                    )
                except ValueError:
                    match["status"] = "uncertain"
            windows.append((start, end, match))
            start = end
        if not segment.words:
            names = {m["name"] for _, _, m in windows}
            match = windows[0][2] if len(names) == 1 else {"name": "多人/待确认", "status": "uncertain"}
            result.append(
                {"start": segment.start, "end": segment.end, "text": segment.text.strip(), "match": match}
            )
            continue
        for start, end, match in windows:
            words = [
                w.word
                for w in segment.words
                if start <= (w.start + w.end) / 2 < end
                or (end == segment.end and (w.start + w.end) / 2 == end)
            ]
            if words:
                result.append({"start": start, "end": end, "text": "".join(words).strip(), "match": match})
    # Normalize assembled text before either persistence or voice-question handling.
    # Keep word timing and speaker labels independent of Chinese script conversion.
    for row in result:
        row["text"] = SIMPLIFIED.convert(row["text"])
    return {"segments": result, "duration": len(samples) / 16000}


def speech_worker(connection):
    """Private pipe only; no network-facing deserialization. The child owns model caches."""
    try:
        while True:
            message = connection.recv()
            if message is None:
                return
            task, options = message
            try:
                connection.send({"result": infer(task, options)})
            except Exception as exc:
                message = str(exc) if isinstance(exc, ValueError) else "音频模型执行失败，请检查模型文件"
                connection.send({"error": message})
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        connection.close()


class Speech:
    def __init__(self, settings):
        self.settings = settings
        self.process = None
        self.connection = None
        self.lock = asyncio.Semaphore(1)
        self.timeout = 600

    def available(self):
        s = self.settings
        return {
            "asr": (s.model_dir / f"asr-{s.asr_model}" / "model.bin").is_file(),
            "speaker": s.speaker_path.is_file(),
            "kws": (s.kws_dir / "tokens.txt").is_file() and (s.kws_dir / "xiaok.txt").is_file(),
        }

    def start(self):
        if self.process and self.process.is_alive():
            return
        self.close()
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(target=speech_worker, args=(child,), daemon=True)
        self.process.start()
        child.close()

    @staticmethod
    def exchange(connection, task, options, timeout):
        connection.send((task, options))
        if not connection.poll(timeout):
            raise TimeoutError("语音处理超时")
        return connection.recv()

    async def run(self, kind, content, candidates=None):
        s = self.settings
        options = {
            "speaker_path": str(s.speaker_path),
            "asr_path": str(s.model_dir / f"asr-{s.asr_model}"),
            "threshold": s.speaker_threshold,
            "margin": s.speaker_margin,
            "max_seconds": s.max_seconds,
        }
        async with self.lock:
            self.start()
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.exchange,
                        self.connection,
                        {"kind": kind, "content": content, "candidates": candidates or []},
                        options,
                        self.timeout,
                    ),
                    self.timeout + 5,
                )
                if "error" in response:
                    raise ValueError(response["error"])
                return response["result"]
            except asyncio.CancelledError:
                # Stop native inference, not just its awaiting Future; the next request starts a new child.
                await asyncio.to_thread(self.close)
                raise
            except (TimeoutError, EOFError, BrokenPipeError, OSError) as exc:
                await asyncio.to_thread(self.close)
                raise ValueError("音频工作进程超时或退出，已重置；录音保留，可重试") from exc

    def close(self):
        if self.process:
            if self.process.is_alive():
                try:
                    self.connection.send(None)
                except (BrokenPipeError, OSError):
                    pass
                self.process.join(timeout=1)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=1)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=1)
            self.process.close()
            self.process = None
        if self.connection:
            self.connection.close()
            self.connection = None


class WakeDetector:
    def __init__(self, folder):
        self.folder = folder
        self.engine = None
        self.lock = threading.Lock()

    def load(self):
        import sherpa_onnx

        def model(prefix):
            paths = sorted(self.folder.glob(prefix + "*.onnx"), key=lambda p: ("int8" not in p.name, p.name))
            if not paths:
                raise ValueError("唤醒模型未就绪，请先下载语音模型")
            return str(paths[0])

        tokens = self.folder / "tokens.txt"
        if not tokens.exists():
            raise ValueError("唤醒词词表未就绪")
        # The bilingual model uses Chinese pinyin + English BPE; generated keyword file can override.
        keywords = self.folder / "xiaok.txt"
        if not keywords.exists():
            raise ValueError("小K唤醒词未配置，请运行 scripts/configure_kws.py")
        self.engine = sherpa_onnx.KeywordSpotter(
            tokens=str(tokens),
            encoder=model("encoder"),
            decoder=model("decoder"),
            joiner=model("joiner"),
            keywords_file=str(keywords),
            num_threads=1,
            provider="cpu",
            keywords_threshold=0.25,
        )

    def create(self):
        with self.lock:
            if self.engine is None:
                self.load()
            return self.engine.create_stream()

    def feed(self, stream, pcm):
        with self.lock:
            stream.accept_waveform(16000, np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768)
            found = False
            while self.engine.is_ready(stream):
                self.engine.decode_stream(stream)
                if self.engine.get_result(stream):
                    found = True
                    self.engine.reset_stream(stream)
            return found
