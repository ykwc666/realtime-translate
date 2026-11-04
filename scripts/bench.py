"""Reproducible latency bench; appends to docs/OPTIMIZATION_LOG.md and bench_history.jsonl."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app.asr_stream import StreamingAsr  # noqa: E402
from app.audio_io import load_wav  # noqa: E402
from app.translate import Translator  # noqa: E402
from app.tts_play import TtsPlayer  # noqa: E402
from app import config  # noqa: E402

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


def percentile(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True, help="短标签，如 ct2-int8")
    parser.add_argument("--note", default="", help="改动说明")
    parser.add_argument("--wav", default=str(DEFAULT_WAV))
    parser.add_argument("--runs", type=int, default=3, help="有效测速次数（不含预热）")
    parser.add_argument("--no-tts", action="store_true")
    parser.add_argument(
        "--asr",
        choices=["sherpa_paraformer_online", "sherpa_sensevoice"],
        default=None,
    )
    parser.add_argument("--mt", choices=["transformers", "ct2"], default=None)
    args = parser.parse_args()

    if args.asr:
        config.ASR_BACKEND = args.asr
    if args.mt:
        config.MT_BACKEND = args.mt

    wav = Path(args.wav)
    if not wav.exists():
        print(f"wav 不存在: {wav}", file=sys.stderr)
        return 2

    audio = load_wav(str(wav))
    audio_sec = len(audio) / float(config.SAMPLE_RATE)
    print(f"音频 {audio_sec:.2f}s | ASR={config.ASR_BACKEND} | MT={config.MT_BACKEND}")

    print("加载…")
    t_load = time.perf_counter()
    asr = StreamingAsr()
    mt = Translator()
    tts = None if args.no_tts else TtsPlayer()
    print(f"加载完成 {(time.perf_counter() - t_load):.1f}s")

    # warmup（含中英 TTS，避免首句冷启动）
    print("预热…")
    _ = asr.transcribe(audio)
    _ = mt.translate("你好，世界")
    if tts:
        costs = tts.warmup()
        print(f"TTS warmup zh={costs.get('zh', 0):.0f}ms en={costs.get('en', 0):.0f}ms")

    asr_ms: list[float] = []
    mt_ms: list[float] = []
    tts_ms: list[float] = []
    e2e_ms: list[float] = []
    last_src = ""
    last_tgt = ""

    for i in range(args.runs):
        t0 = time.perf_counter()
        a = asr.transcribe(audio)
        asr_ms.append(a.ms)
        m = mt.translate(a.text or "你好")
        mt_ms.append(m.ms)
        tm = 0.0
        if tts and m.text:
            syn = tts.synthesize(m.text, m.tgt_lang)
            tm = syn.ms
        tts_ms.append(tm)
        e2e = (time.perf_counter() - t0) * 1000
        e2e_ms.append(e2e)
        last_src, last_tgt = a.text, m.text
        print(
            f"run{i+1}: ASR {a.ms:.0f} | MT {m.ms:.0f} | TTS {tm:.0f} | E2E {e2e:.0f} | {a.text} => {m.text}"
        )

    row = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tag": args.tag,
        "note": args.note,
        "asr_backend": config.ASR_BACKEND,
        "mt_backend": config.MT_BACKEND,
        "audio_sec": round(audio_sec, 2),
        "runs": args.runs,
        "asr_ms_median": round(percentile(asr_ms, 0.5), 1),
        "mt_ms_median": round(percentile(mt_ms, 0.5), 1),
        "tts_ms_median": round(percentile(tts_ms, 0.5), 1),
        "e2e_ms_median": round(percentile(e2e_ms, 0.5), 1),
        "asr_ms": [round(x, 1) for x in asr_ms],
        "mt_ms": [round(x, 1) for x in mt_ms],
        "tts_ms": [round(x, 1) for x in tts_ms],
        "e2e_ms": [round(x, 1) for x in e2e_ms],
        "sample_src": last_src,
        "sample_tgt": last_tgt,
    }

    LOG_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with LOG_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    md_line = (
        f"| {row['time']} | {row['tag']} | {row['asr_ms_median']} | "
        f"{row['mt_ms_median']} | {row['tts_ms_median']} | {row['e2e_ms_median']} | "
        f"{row['note'] or row['asr_backend']+'+'+row['mt_backend']} |"
    )
    if LOG_MD.exists():
        text = LOG_MD.read_text(encoding="utf-8")
        if "## 自动追加" in text:
            text = text.rstrip() + "\n" + md_line + "\n"
        else:
            text = text.rstrip() + "\n\n## 自动追加\n\n" + md_line + "\n"
        LOG_MD.write_text(text, encoding="utf-8")

    print("\n中位值:", {k: row[k] for k in ("asr_ms_median", "mt_ms_median", "tts_ms_median", "e2e_ms_median")})
    print("已写入", LOG_JSONL, "与", LOG_MD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
