import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    data_dir: Path = ROOT / "data"
    provider: str = "cloud"
    base_url: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False)
    json_mode: str = "schema"
    agent_protocol: str = "structured"
    timeout: float = 90
    context_tokens: int = 16384
    max_output_tokens: int = 1800
    asr_model: str = "small"
    asr_device: str = "cpu"
    asr_compute_type: str = "int8"
    asr_language: str = "zh"
    asr_timeout: int = 900
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    max_audio_seconds: int = 600
    max_upload_bytes: int = 100 * 1024 * 1024
    live_asr_model: str = "small"
    live_rms_threshold: float = 0.012
    live_silence_seconds: float = 0.8
    tts_voice: str = ""

    @classmethod
    def load(cls):
        # Read afresh on rerun; do not retain deleted credentials in os.environ.
        env = {**dotenv_values(ROOT / ".env"), **os.environ}

        def get(key, default=""):
            return env.get(key) or default

        directory = Path(get("MEETING_DATA_DIR", "data")).expanduser()
        provider = get("LLM_PROVIDER", "cloud")
        return cls(
            data_dir=(directory if directory.is_absolute() else ROOT / directory).resolve(),
            provider=provider,
            base_url=get("LLM_BASE_URL").rstrip("/"),
            model=get("LLM_MODEL"),
            api_key=get("LLAMACPP_API_KEY" if provider == "llamacpp" else "LLM_API_KEY"),
            json_mode=get("LLM_JSON_MODE", "schema"),
            agent_protocol=get("AGENT_PROTOCOL", "structured"),
            timeout=float(get("LLM_TIMEOUT_SECONDS", "90")),
            context_tokens=int(get("LLM_CONTEXT_TOKENS", "16384")),
            max_output_tokens=int(get("LLM_MAX_OUTPUT_TOKENS", "1800")),
            asr_model=get("ASR_MODEL", "small"),
            asr_device=get("ASR_DEVICE", "cpu"),
            asr_compute_type=get("ASR_COMPUTE_TYPE", "int8"),
            asr_language=get("ASR_LANGUAGE", "zh"),
            asr_timeout=int(get("ASR_TIMEOUT_SECONDS", "900")),
            ffmpeg=get("FFMPEG_PATH", "ffmpeg"),
            ffprobe=get("FFPROBE_PATH", "ffprobe"),
            live_asr_model=get("LIVE_ASR_MODEL", "small"),
            live_rms_threshold=float(get("LIVE_RMS_THRESHOLD", "0.012")),
            live_silence_seconds=float(get("LIVE_SILENCE_SECONDS", "0.8")),
            tts_voice=get("TTS_VOICE"),
        )

    def llm_error(self):
        if self.provider not in {"cloud", "llamacpp"}:
            return "LLM_PROVIDER 只能是 cloud 或 llamacpp。"
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return "请在 .env 填写 LLM_BASE_URL（包含服务要求的版本前缀）。"
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            return "服务地址不能包含账号、密钥、查询参数或片段。"
        if self.provider == "llamacpp" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            return "本地模式仅连接本机 llama.cpp；请使用 localhost 或 127.0.0.1。"
        if self.provider == "cloud" and parsed.scheme != "https":
            return "云端 API 请使用 HTTPS 地址。"
        if not self.model:
            return "请在 .env 填写 LLM_MODEL。"
        if self.provider == "cloud" and not self.api_key:
            return "请在本机 .env 填写 LLM_API_KEY，不要提交密钥。"
        if self.json_mode not in {"schema", "json", "prompt"}:
            return "LLM_JSON_MODE 必须为 schema、json 或 prompt。"
        if self.agent_protocol not in {"structured", "native"}:
            return "AGENT_PROTOCOL 必须为 structured 或 native。"
        if self.timeout <= 0 or self.max_output_tokens < 128:
            return "模型超时或输出预算配置无效。"
        if self.context_tokens <= self.max_output_tokens + 1024:
            return "上下文需为输入和输出预留足够空间。"
        return None

    def public_model(self):
        return {"provider": self.provider, "model": self.model, "json_mode": self.json_mode}
