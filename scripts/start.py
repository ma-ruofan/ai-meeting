"""Cross-platform entry point. Uses the Python interpreter running this script."""

import argparse
import importlib.util
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument(
        "--dev", action="store_true", help="Backend reload; run Vite separately"
    )
    args = parser.parse_args()
    if importlib.util.find_spec("meeting_app") is None:
        raise SystemExit('Install first: python -m pip install -e "backend[voice,dev]"')
    if not args.dev and not (ROOT / "frontend" / "dist" / "index.html").exists():
        raise SystemExit("Build frontend first: cd frontend && npm ci && npm run build")
    print(f"Host recording page: http://localhost:{args.port}", flush=True)
    addresses = {
        x[4][0] for x in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    }
    for address in sorted(addresses):
        if not address.startswith("127."):
            print(f"Other devices on Wi-Fi: http://{address}:{args.port}", flush=True)
    print("Text LLM defaults to mock. The old .env is not read.", flush=True)
    env = dict(os.environ)
    env.setdefault("XIAOK_DATA_DIR", str(ROOT / "data" / "v2"))
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "meeting_app.main:app",
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--no-access-log",
    ]
    if args.dev:
        command += ["--reload", "--reload-dir", str(ROOT / "backend")]
    try:
        raise SystemExit(subprocess.call(command, cwd=ROOT, env=env))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
