"""Merge LoRA into Opus-MT base and convert to CTranslate2 int8."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from peft import PeftModel  # noqa: E402
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=str(ROOT / "models" / "opus-mt-zh-en"))
    parser.add_argument(
        "--adapter", default=str(ROOT / "models" / "opus-mt-zh-en-lora-prefix")
    )
    parser.add_argument(
        "--merged", default=str(ROOT / "models" / "opus-mt-zh-en-lora-merged")
    )
    parser.add_argument(
        "--ct2", default=str(ROOT / "models" / "opus-mt-zh-en-lora-ct2")
    )
    args = parser.parse_args()

    adapter = Path(args.adapter)
    if not adapter.exists():
        print("missing adapter", adapter)
        return 2

    print("load base + adapter …")
    tok = AutoTokenizer.from_pretrained(args.base)
    base = AutoModelForSeq2SeqLM.from_pretrained(args.base)
    model = PeftModel.from_pretrained(base, str(adapter))
    merged = model.merge_and_unload()

    merged_dir = Path(args.merged)
    if merged_dir.exists():
        # overwrite safely for reconvert
        pass
    merged_dir.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(str(merged_dir))
    tok.save_pretrained(str(merged_dir))
    print("merged ->", merged_dir)

    ct2_dir = Path(args.ct2)
    if ct2_dir.exists():
        import shutil

        shutil.rmtree(ct2_dir)
    converter = Path(sys.executable).with_name("ct2-transformers-converter.exe")
    if not converter.exists():
        converter = Path(sys.executable).with_name("ct2-transformers-converter")
    cmd = [
        str(converter),
        "--model",
        str(merged_dir),
        "--output_dir",
        str(ct2_dir),
        "--quantization",
        "int8",
        "--force",
    ]
    print("convert", cmd)
    try:
        subprocess.check_call(cmd, cwd=str(ROOT))
    except Exception:
        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "ctranslate2.converters.transformers",
                "--model",
                str(merged_dir),
                "--output_dir",
                str(ct2_dir),
                "--quantization",
                "int8",
                "--force",
            ],
            cwd=str(ROOT),
        )
    print("CT2_LORA_OK", ct2_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
