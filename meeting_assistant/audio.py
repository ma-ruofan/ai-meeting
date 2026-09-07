import json
import os
import shutil
import subprocess
import sys
import uuid
import wave
from importlib.util import find_spec
from pathlib import Path

from .config import ROOT
from .models import AppError, validate_segments


def run_media(command, timeout=90):
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise AppError("未找到 FFmpeg/ffprobe，请安装并配置可执行文件路径。") from exc
    except subprocess.TimeoutExpired as exc:
        raise AppError("音频处理超时，请换较短的文件后重试。") from exc
    if result.returncode:
        raise AppError("音频解码失败，文件可能损坏、格式不支持或没有音轨。")
    return result.stdout


def wav_info(path):
    try:
        with wave.open(str(path), "rb") as audio:
            expected = audio.getnframes() * audio.getnchannels() * audio.getsampwidth()
            received = 0
            while block := audio.readframes(65536):
                received += len(block)
            if received != expected:
                return None, False
            return audio.getnframes() / audio.getframerate(), (
                audio.getnchannels() == 1
                and audio.getframerate() == 16000
                and audio.getsampwidth() == 2
                and audio.getcomptype() == "NONE"
            )
    except (wave.Error, EOFError, ZeroDivisionError):
        return None, False


def import_audio(repo, settings, title, filename, content):
    ext = Path(filename).suffix.lower()
    if ext not in {".wav", ".mp3"}:
        raise AppError("首版只接收 WAV 或 MP3。")
    if not content or len(content) > settings.max_upload_bytes:
        raise AppError("音频为空或超过 100 MB 限制。")
    with repo.processing():
        ident = uuid.uuid4().hex
        folder = repo.root / "meetings" / ident
        folder.mkdir(parents=True)
        source = folder / ("source" + ext)
        processed = folder / "processed.wav"
        temp = folder / "processed.tmp.wav"
        try:
            source.write_bytes(content)
            duration, standard = wav_info(source) if ext == ".wav" else (None, False)
            if duration is None:
                output = run_media(
                    [
                        settings.ffprobe,
                        "-v",
                        "error",
                        "-select_streams",
                        "a:0",
                        "-show_entries",
                        "stream=codec_type:format=duration",
                        "-of",
                        "json",
                        str(source),
                    ]
                )
                try:
                    probe = json.loads(output)
                    if not probe.get("streams"):
                        raise ValueError("no audio")
                    duration = float(probe["format"]["duration"])
                except (ValueError, KeyError, TypeError) as exc:
                    raise AppError("无法读取有效音频时长或音轨。") from exc
            if not 0 < duration <= settings.max_audio_seconds:
                raise AppError("首版音频时长需在 0～10 分钟之间。")
            if standard:
                shutil.copyfile(source, temp)
            else:
                run_media(
                    [
                        settings.ffmpeg,
                        "-nostdin",
                        "-v",
                        "error",
                        "-y",
                        "-i",
                        str(source),
                        "-vn",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        "-c:a",
                        "pcm_s16le",
                        str(temp),
                    ]
                )
            converted_duration, standard = wav_info(temp)
            if (
                not standard
                or not converted_duration
                or converted_duration > settings.max_audio_seconds
            ):
                raise AppError("转换后的音频未通过校验。")
            os.replace(temp, processed)
            return repo.create_meeting(
                title or Path(filename).stem,
                converted_duration,
                "audio",
                ident,
                str(source.relative_to(repo.root)),
                str(processed.relative_to(repo.root)),
            )
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise


def transcribe(repo, settings, meeting_id, progress=lambda _: None):
    if find_spec("faster_whisper") is None:
        raise AppError("尚未安装语音依赖，请运行 uv sync --extra asr。")
    with repo.processing():
        meeting = repo.meeting(meeting_id)
        audio = repo.path(meeting["audio_path"])
        if not audio or not audio.is_file():
            raise AppError("会议没有可转录音频；开发样例只提供文字。")
        run = repo.begin_stage(
            meeting_id,
            "transcription",
            {
                "model": settings.asr_model,
                "device": settings.asr_device,
                "compute_type": settings.asr_compute_type,
            },
        )
        result_path = audio.parent / (f"asr-{uuid.uuid4().hex}.json")
        progress("加载语音模型并转录；首次运行可能下载模型")
        env = os.environ.copy()
        # ASR has no reason to receive language-model credentials.
        for key in ("LLM_API_KEY", "LLAMACPP_API_KEY"):
            env.pop(key, None)
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "meeting_assistant.asr_worker",
                    "--audio",
                    str(audio),
                    "--output",
                    str(result_path),
                    "--model",
                    settings.asr_model,
                    "--device",
                    settings.asr_device,
                    "--compute-type",
                    settings.asr_compute_type,
                    "--language",
                    settings.asr_language,
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                timeout=settings.asr_timeout,
                check=False,
            )
            if result.returncode or not result_path.exists():
                raise AppError("语音转录失败，请检查模型缓存、网络及 CPU/CUDA 配置；已有转录保留。")
            data = json.loads(result_path.read_text(encoding="utf-8"))
            segments = validate_segments(data["segments"], meeting["duration"])
            progress("保存转录片段")
            repo.replace_transcript(meeting_id, segments)
            repo.finish_stage(run, metadata=data["metrics"])
        except Exception as exc:
            message = (
                "转录超时，子进程已终止；可以重试。"
                if isinstance(exc, subprocess.TimeoutExpired)
                else str(exc)
                if isinstance(exc, AppError)
                else "转录结果校验失败，已有结果保留。"
            )
            repo.finish_stage(run, error=message)
            raise AppError(message) from exc
        finally:
            result_path.unlink(missing_ok=True)
