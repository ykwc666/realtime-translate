"""Convert local Opus-MT models to CTranslate2 int8."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAIRS = [
    (ROOT / "models" / "opus-mt-zh-en", ROOT / "models" / "opus-mt-zh-en-ct2"),
    (ROOT / "models" / "opus-mt-en-zh", ROOT / "models" / "opus-mt-en-zh-ct2"),
]


def convert(src: Path, dst: Path) -> None:
    if not src.exists():
        raise FileNotFoundError(src)
    marker = dst / "model.bin"
    if marker.exists():
        print(f"skip exists: {dst}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "ctranslate2.converters.transformers",
        "--model",
        str(src),
        "--output_dir",
        str(dst),
        "--quantization",
        "int8",
        "--force",
    ]
    # newer ct2 uses ct2-transformers-converter entrypoint
    alt = [
        "ct2-transformers-converter",
        "--model",
        str(src),
        "--output_dir",
        str(dst),
        "--quantization",
        "int8",
        "--force",
    ]
    print("convert", src, "->", dst)
    try:
        subprocess.check_call(alt, cwd=str(ROOT))
    except Exception:
        subprocess.check_call(cmd, cwd=str(ROOT))
    print("ok", dst)


def main() -> int:
    for src, dst in PAIRS:
        convert(src, dst)
    print("CT2_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
