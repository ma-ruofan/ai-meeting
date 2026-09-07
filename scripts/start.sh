#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  echo "请先运行 uv sync --frozen（需要语音转录则加 --extra asr）。"
  exit 1
fi
exec .venv/bin/python -m streamlit run app.py "$@"
