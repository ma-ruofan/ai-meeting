"""Pin the tested wake phrase phonemes for the Chinese/English 3M KWS model."""

from pathlib import Path


def configure(folder=None):
    folder = folder or Path(__file__).resolve().parents[1] / "models" / "kws"
    tokens = {
        line.split()[0]
        for line in (folder / "tokens.txt").read_text(encoding="utf-8").splitlines()
    }
    # English letter K: /k eɪ/. Include the common Chinese '开' pronunciation as a separate path.
    variants = ["x iǎo K EY1 x iǎo K EY1", "x iǎo k āi x iǎo k āi"]
    for variant in variants:
        if not set(variant.split()) <= tokens:
            raise ValueError("关键词不兼容当前模型词表，请核对下载的模型版本")
    (folder / "xiaok.txt").write_text(
        "\n".join(v + " @小K小K" for v in variants) + "\n", encoding="utf-8"
    )
    print("Wake phrase configured: 小K小K")


if __name__ == "__main__":
    configure()
