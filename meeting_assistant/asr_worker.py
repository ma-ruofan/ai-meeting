"""One-shot ASR process: the process exit releases model memory on both platforms."""

import argparse
import importlib.metadata
import json
from pathlib import Path
from time import perf_counter


def main():
    parser = argparse.ArgumentParser()
    for name in ("audio", "output", "model", "device", "compute-type", "language"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    from faster_whisper import WhisperModel

    begin = perf_counter()
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    loaded = perf_counter()
    raw, info = model.transcribe(
        args.audio,
        language=args.language or None,
        vad_filter=True,
        word_timestamps=False,
        beam_size=5,
    )
    segments = []
    for segment in raw:
        if segment.text.strip() and segment.end > segment.start:
            segments.append(
                {
                    "segment_id": f"S{len(segments) + 1:03d}",
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text.strip(),
                }
            )
    done = perf_counter()
    metrics = {
        "model": args.model,
        "device": args.device,
        "compute_type": args.compute_type,
        "faster_whisper_version": importlib.metadata.version("faster-whisper"),
        "load_seconds": round(loaded - begin, 3),
        "inference_seconds": round(done - loaded, 3),
        "duration": info.duration,
        "rtf": round((done - loaded) / max(info.duration, 0.001), 4),
    }
    Path(args.output).write_text(
        json.dumps({"segments": segments, "metrics": metrics}, ensure_ascii=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
