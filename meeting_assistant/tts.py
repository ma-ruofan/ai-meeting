"""Speak on the service computer using its installed OS voice; no cloud TTS."""

import platform
import subprocess
import tempfile
from pathlib import Path

from .config import ROOT
from .models import AppError


def speak(text, voice, stop):
    if stop.is_set():
        return False
    with tempfile.TemporaryDirectory(prefix="meeting-tts-") as directory:
        path = Path(directory) / "speech.txt"
        path.write_text(text, encoding="utf-8")
        system = platform.system()
        if system == "Darwin":
            args = ["say", "-v", voice or "Tingting", "-f", str(path)]
        elif system == "Windows":
            args = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(ROOT / "scripts/speak.ps1"),
                "-TextPath",
                str(path),
                "-Voice",
                voice,
            ]
        else:
            raise AppError("首版本机播报仅支持 macOS 和 Windows。")
        try:
            with subprocess.Popen(
                args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            ) as proc:
                waited = 0
                while proc.poll() is None:
                    if stop.wait(0.1) or waited >= 120:
                        proc.terminate()
                        try:
                            proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                        if waited >= 120:
                            raise AppError("语音播报超过两分钟，已停止。")
                        return False
                    waited += 0.1
                if proc.returncode:
                    raise AppError("本机播报失败，请安装中文系统语音并检查 TTS_VOICE。")
        except OSError as exc:
            raise AppError("未找到本机语音程序，请检查系统语音配置。") from exc
    return True
