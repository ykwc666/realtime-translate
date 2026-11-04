"""A/B Piper TTS: CPU vs DirectML (run inside the matching venv)."""
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
import onnxruntime as ort  # noqa: E402
from piper import PiperVoice  # noqa: E402
from piper.config import PiperConfig  # noqa: E402

ZH = ROOT / "models" / "piper" / "zh_CN-huayan-medium.onnx"
EN = ROOT / "models" / "piper" / "en_US-lessac-medium.onnx"
LOG_MD = ROOT / "docs" / "OPTIMIZATION_LOG.md"
LOG_JSONL = ROOT / "docs" / "bench_history.jsonl"

TEXTS = {
    "zh_short": "测试",
    "zh_mid": "欢迎大家来体验达摩院推出的语音识别模型。",
    "en_short": "test",
    "en_mid": "Welcome to the speech recognition model.",
}


def load_voice(model: Path, provider: str) -> PiperVoice:
    cfg_path = Path(str(model) + ".json")
    with cfg_path.open(encoding="utf-8") as f:
        cfg = PiperConfig.from_dict(json.load(f))
    if provider == "dml":
        providers: list = ["DmlExecutionProvider", "CPUExecutionProvider"]
    elif provider == "cpu":
        providers = ["CPUExecutionProvider"]
    else:
        raise ValueError(provider)
    avail = ort.get_available_providers()
    for p in providers:
        name = p if isinstance(p, str) else p[0]
        if name not in avail and name != "CPUExecutionProvider":
            raise RuntimeError(f"provider {name} not available: {avail}")
    sess = ort.InferenceSession(str(model), providers=providers)
    return PiperVoice(config=cfg, session=sess)


def synth_ms(voice: PiperVoice, text: str, repeats: int) -> list[float]:
    # warmup
    for _ in voice.synthesize(text):
        pass
    times: list[float] = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        chunks = []
        for chunk in voice.synthesize(text):
            chunks.append(np.asarray(chunk.audio_float_array))
        times.append((time.perf_counter() - t0) * 1000)
        if not chunks:
            raise RuntimeError("empty synth")
    return times


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("cpu", "dml"), required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--tag", default="")
    args = parser.parse_args()

    for p in (ZH, EN):
        if not p.exists():
            print("missing", p)
            return 2

    used = ort.get_available_providers()
    print("ort", ort.__version__, "providers", used)

    rows = []
    voices = {
        "zh": load_voice(ZH, args.provider),
        "en": load_voice(EN, args.provider),
    }
    # report actual session providers
    for lang, v in voices.items():
        print(lang, "session_providers", v.session.get_providers())

    for key, text in TEXTS.items():
        lang = "zh" if key.startswith("zh") else "en"
        ms = synth_ms(voices[lang], text, args.repeats)
        ms_sorted = sorted(ms)
        row = {
            "key": key,
            "text_len": len(text),
            "ms_median": round(ms_sorted[len(ms_sorted) // 2], 1),
            "ms_mean": round(sum(ms) / len(ms), 1),
            "ms_min": round(ms_sorted[0], 1),
            "ms_all": [round(x, 1) for x in ms],
        }
        rows.append(row)
        print(key, row)

    out = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tag": args.tag or f"piper-{args.provider}",
        "note": f"Piper TTS provider={args.provider}",
        "mode": "piper_provider_ab",
        "provider": args.provider,
        "ort_version": ort.__version__,
        "ort_available": used,
        "results": rows,
    }
    LOG_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with LOG_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False) + "\n")

    summary = " ".join(f"{r['key']}={r['ms_median']}ms" for r in rows)
    md = f"| {out['time']} | {out['tag']} | {summary} | provider={args.provider} ort={ort.__version__} |"
    if LOG_MD.exists():
        text = LOG_MD.read_text(encoding="utf-8")
        LOG_MD.write_text(text.rstrip() + "\n" + md + "\n", encoding="utf-8")

    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("PIPER_PROVIDER_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
