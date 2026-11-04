"""Download call-level ZH/EN realtime stack via ModelScope + HF mirror."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "models"
CACHE_MS = ROOT / "cache" / "modelscope"
CACHE_HF = ROOT / "cache" / "huggingface"

os.environ.setdefault("MODELSCOPE_CACHE", str(CACHE_MS))
os.environ.setdefault("HF_HOME", str(CACHE_HF))
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(CACHE_HF / "hub"))
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("MODELSCOPE_DOMAIN", "www.modelscope.cn")
# hf-mirror + xet 常 401，强制走普通 HTTP 下载
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"


def download_funasr() -> None:
    from funasr import AutoModel

    asr_dir = MODELS_DIR / "paraformer-zh-streaming"
    vad_dir = MODELS_DIR / "fsmn-vad"
    asr_dir.mkdir(parents=True, exist_ok=True)
    vad_dir.mkdir(parents=True, exist_ok=True)

    print("[1/3] FunASR streaming ASR (ModelScope)...")
    AutoModel(
        model="iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online",
        model_revision="v2.0.4",
        hub="ms",
        device="cpu",
        disable_update=True,
    )
    print("  ASR OK")

    print("[2/3] FunASR VAD (ModelScope)...")
    AutoModel(
        model="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        model_revision="v2.0.4",
        hub="ms",
        device="cpu",
        disable_update=True,
    )
    print("  VAD OK")


def download_opus_mt() -> None:
    # huggingface_hub + xet 在国内易失败；改走 hf-mirror 纯 HTTP
    import runpy

    print("[3/3] Opus-MT zh<->en (hf-mirror HTTP)...")
    runpy.run_path(str(Path(__file__).with_name("download_opus_mt.py")), run_name="__main__")


def smoke_load() -> None:
    print("\nSmoke load...")
    from funasr import AutoModel
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    asr = AutoModel(
        model="iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online",
        model_revision="v2.0.4",
        hub="ms",
        device="cpu",
        disable_update=True,
    )
    _ = asr
    tok = AutoTokenizer.from_pretrained(str(MODELS_DIR / "opus-mt-zh-en"))
    mt = AutoModelForSeq2SeqLM.from_pretrained(str(MODELS_DIR / "opus-mt-zh-en"))
    text = "你好，世界"
    batch = tok(text, return_tensors="pt")
    out = mt.generate(**batch, max_new_tokens=64)
    print("  MT zh->en:", tok.decode(out[0], skip_special_tokens=True))
    print("Smoke OK")


def main() -> int:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_MS.mkdir(parents=True, exist_ok=True)
    CACHE_HF.mkdir(parents=True, exist_ok=True)
    print("ROOT:", ROOT)
    print("MODELSCOPE_CACHE:", os.environ["MODELSCOPE_CACHE"])
    print("HF_HOME:", os.environ["HF_HOME"])
    print("HF_ENDPOINT:", os.environ["HF_ENDPOINT"])
    download_funasr()
    download_opus_mt()
    smoke_load()
    return 0


if __name__ == "__main__":
    sys.exit(main())
