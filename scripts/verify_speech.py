"""Optional real-model smoke test. Supply your own authorized audio files; never records a mic."""

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from meeting_app.config import Settings
from meeting_app.speech import Speech, WakeDetector, decode_audio


async def verify(args):
    settings = Settings()
    speech = Speech(settings)
    audio = Path(args.audio).read_bytes()
    try:
        started = perf_counter()
        transcript = await speech.run("transcribe", audio)
        elapsed = round(perf_counter() - started, 3)
        vector = await speech.run("enroll", audio)
        report = {
            "kind": "pipeline_smoke_not_accuracy_evaluation",
            "duration_seconds": transcript["duration"],
            "transcription_seconds": elapsed,
            "segments": transcript["segments"],
            "embedding_dimension": len(vector),
        }
        if args.wake_audio:
            detector = WakeDetector(settings.kws_dir)
            stream = detector.create()
            signal = decode_audio(Path(args.wake_audio).read_bytes())
            pcm = (
                (np.concatenate([signal, np.zeros(16000)]) * 32767)
                .astype("<i2")
                .tobytes()
            )
            report["wake_hits_seconds"] = [
                i / 32000
                for i in range(0, len(pcm), 3200)
                if detector.feed(stream, pcm[i : i + 3200])
            ]
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        speech.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("--wake-audio")
    asyncio.run(verify(parser.parse_args()))
