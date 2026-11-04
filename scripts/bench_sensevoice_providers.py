"""A/B sherpa SenseVoice: CPU vs DirectML (use matching venv + ORT DLL)."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import sherpa_onnx  # noqa: E402
import soundfile as sf  # noqa: E402

MODEL = ROOT / "models" / "sherpa-sensevoice" / "model.int8.onnx"
TOKENS = ROOT / "models" / "sherpa-sensevoice" / "tokens.txt"
DEFAULT_WAV = (
    ROOT
    / "cache"
    / "modelscope"
    / "models"
    / "iic--speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
    / "snapshots"
    / "v2.0.4"
    / "example"
    / "asr_example.wav"
)
LOG_MD = ROOT / "docs" / "OPTIMIZATION_LOG.md"
LOG_JSONL = ROOT / "docs" / "bench_history.jsonl"


def load_audio(path: Path) -> tuple[np.ndarray, int]:
    audio, sr = sf.read(str(path), dtype="float32")
    if getattr(audio, "ndim", 1) > 1:
        audio = audio.mean(axis=1)
    return np.asarray(audio, dtype=np.float32), int(sr)


def bench(provider: str, audio: np.ndarray, sr: int, repeats: int) -> dict:
    rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(MODEL),
        tokens=str(TOKENS),
        num_threads=6,
        use_itn=True,
        language="auto",
        provider=provider,
        debug=False,
    )
    # warmup
    st = rec.create_stream()
    st.accept_waveform(sr, audio)
    rec.decode_stream(st)
    text0 = (st.result.text or "").strip()

    times: list[float] = []
    text = text0
    for _ in range(repeats):
        t0 = time.perf_counter()
        st = rec.create_stream()
        st.accept_waveform(sr, audio)
        rec.decode_stream(st)
        times.append((time.perf_counter() - t0) * 1000)
        text = (st.result.text or "").strip()
    times_s = sorted(times)
    return {
        "provider": provider,
        "text": text,
        "ms_median": round(times_s[len(times_s) // 2], 1),
        "ms_mean": round(sum(times) / len(times), 1),
        "ms_min": round(times_s[0], 1),
        "ms_all": [round(x, 1) for x in times],
        "audio_sec": round(len(audio) / float(sr), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("cpu", "directml"), required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--wav", default=str(DEFAULT_WAV))
    parser.add_argument("--tag", default="")
    args = parser.parse_args()

    if not MODEL.exists():
        print("missing model", MODEL)
        return 2
    wav = Path(args.wav)
    if not wav.exists():
        print("missing wav", wav)
        return 2

    audio, sr = load_audio(wav)
    print("sherpa", getattr(sherpa_onnx, "__version__", "?"), "provider", args.provider)
    print("audio", audio.shape, sr)

    try:
        row = bench(args.provider, audio, sr, args.repeats)
    except Exception as e:
        print("BENCH_FAIL", type(e).__name__, e)
        return 1

    out = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tag": args.tag or f"sensevoice-{args.provider}",
        "note": f"SenseVoice sherpa provider={args.provider}",
        "mode": "sensevoice_provider_ab",
        "provider": args.provider,
        **row,
    }
    LOG_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with LOG_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False) + "\n")

    md = (
        f"| {out['time']} | {out['tag']} | asr_median={out['ms_median']} "
        f"min={out['ms_min']} | provider={args.provider} text={out['text'][:24]} |"
    )
    if LOG_MD.exists():
        text = LOG_MD.read_text(encoding="utf-8")
        LOG_MD.write_text(text.rstrip() + "\n" + md + "\n", encoding="utf-8")

    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("SENSEVOICE_PROVIDER_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
