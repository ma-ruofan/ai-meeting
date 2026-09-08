"""Download only speech models, never a text LLM. Run with the project's Python."""

import argparse
import hashlib
import json
import shutil
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
ASSETS = {
    "speaker": (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_zh_cnceleb_resnet34.onnx",
        "wespeaker_zh_cnceleb_resnet34.onnx",
    ),
    "kws": (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2",
        "kws.tar.bz2",
    ),
}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--only", choices=["speaker", "kws", "asr", "all"], default="all")
    args = p.parse_args()
    MODELS.mkdir(exist_ok=True)
    manifest = {}
    for key, (url, name) in ASSETS.items():
        if args.only not in ("all", key):
            continue
        dest = MODELS / name
        if not dest.exists():
            print(f"Downloading {key} from official sherpa-onnx release", flush=True)
            temp = dest.with_suffix(".part")
            urllib.request.urlretrieve(url, temp)
            temp.replace(dest)
        manifest[key] = {
            "url": url,
            "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
        }
        if key == "kws":
            folder = MODELS / "kws"
            folder.mkdir(exist_ok=True)
            with tarfile.open(dest) as archive:
                for member in archive.getmembers():
                    if not member.isfile():
                        continue
                    name = Path(member.name).name
                    if name.endswith((".onnx", ".txt")):
                        with (
                            archive.extractfile(member) as source,
                            (folder / name).open("wb") as out,
                        ):
                            shutil.copyfileobj(source, out)
            from configure_kws import configure

            configure(folder)
    if args.only in ("all", "asr"):
        from huggingface_hub import snapshot_download

        print(
            "Downloading faster-whisper base (speech recognition, not a text LLM)",
            flush=True,
        )
        snapshot_download(
            "Systran/faster-whisper-base",
            local_dir=str(MODELS / "asr-base"),
            allow_patterns=["*.json", "*.bin", "*.txt"],
        )
        manifest["asr"] = {"repository": "Systran/faster-whisper-base"}
    target = MODELS / "downloads.json"
    previous = json.loads(target.read_text()) if target.exists() else {}
    target.write_text(
        json.dumps(previous | manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("Speech models ready.", flush=True)


if __name__ == "__main__":
    main()
