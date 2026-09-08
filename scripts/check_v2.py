"""Run meaningful backend checks and build the browser app. Does not access an LLM."""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for command in [
    [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "backend",
        "scripts",
        "--config",
        "backend/pyproject.toml",
    ],
    [sys.executable, "-m", "pytest", "backend/tests", "-q"],
    [shutil.which("npm") or "npm", "--prefix", "frontend", "run", "build"],
]:
    subprocess.run(command, cwd=ROOT, check=True)
