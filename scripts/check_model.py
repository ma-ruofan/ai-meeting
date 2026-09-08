"""Diagnose the configured model with one synthetic request; never read meeting data."""

import argparse
import asyncio
from time import perf_counter

from meeting_app.agent import ModelClient
from meeting_app.config import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-api", action="store_true", help="Explicitly allow one cloud API request")
    args = parser.parse_args()
    settings = Settings()
    if settings.llm_mode == "api" and not args.allow_api:
        raise SystemExit("Cloud API mode: use --allow-api to explicitly allow one synthetic request.")
    client = ModelClient(settings)
    try:
        _, ollama, _ = client.connection()
        print("Model backend:", "Ollama native /api/chat" if ollama else "Chat Completions", flush=True)
        print("One synthetic JSON request; no meeting data or automatic retries.", flush=True)
        started = perf_counter()
        result = asyncio.run(
            client.complete(
                [
                    {
                        "role": "system",
                        "content": 'Reply with exactly one JSON object: {"ok":true}. No explanation.',
                    },
                    {"role": "user", "content": "Run the JSON connectivity test."},
                ]
            )
        )
        if result != {"ok": True}:
            raise ValueError("[LLM_SCHEMA] JSON decoded, but the model did not follow the test schema.")
        print(f"PASS: JSON model response verified in {perf_counter() - started:.1f}s.")
        print("This checks connectivity and JSON output, not meeting answer quality.")
    except ValueError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
