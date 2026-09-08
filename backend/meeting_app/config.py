import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    # Deliberately do not load the previous prototype's .env or LLM_* variables.
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("XIAOK_DATA_DIR", str(ROOT / "data" / "v2")))
    )
    model_dir: Path = field(default_factory=lambda: Path(os.getenv("XIAOK_MODEL_DIR", str(ROOT / "models"))))
    llm_mode: str = field(default_factory=lambda: os.getenv("XIAOK_LLM_MODE", "mock"))
    llm_url: str = field(default_factory=lambda: os.getenv("XIAOK_LLM_URL", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("XIAOK_LLM_MODEL", ""))
    llm_key: str = field(default_factory=lambda: os.getenv("XIAOK_LLM_KEY", ""))
    llm_backend: str = field(default_factory=lambda: os.getenv("XIAOK_LLM_BACKEND", "auto"))

    asr_model: str = field(default_factory=lambda: os.getenv("XIAOK_ASR_MODEL", "base"))
    speaker_threshold: float = 0.65
    speaker_margin: float = 0.10
    max_upload: int = 50 * 1024 * 1024
    max_seconds: int = 30 * 60

    @property
    def llm_timeout(self):
        return 120 if self.llm_mode == "local" else 45

    @property
    def agent_timeout(self):
        return 300 if self.llm_mode == "local" else 100

    @property
    def speaker_path(self):
        return self.model_dir / "wespeaker_zh_cnceleb_resnet34.onnx"

    @property
    def kws_dir(self):
        return self.model_dir / "kws"
