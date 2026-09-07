"""Read-only environment report; never prints credentials or sends model requests."""

import platform
import shutil
from importlib.util import find_spec

from meeting_assistant.config import Settings


def main():
    cfg = Settings.load()
    print(f"Python: {platform.python_version()} / {platform.system()} {platform.machine()}")
    for name in ("streamlit", "pydantic", "httpx", "faster_whisper"):
        print(f"{name}: {'installed' if find_spec(name) else 'not installed'}")
    print(f"FFmpeg: {'available' if shutil.which(cfg.ffmpeg) else 'not found'}")
    print(f"ffprobe: {'available' if shutil.which(cfg.ffprobe) else 'not found'}")
    print(f"LLM configuration: {cfg.llm_error() or 'filled (connection not tested)'}")
    print(f"ASR: {cfg.asr_model} / {cfg.asr_device} / {cfg.asr_compute_type}")


if __name__ == "__main__":
    main()
