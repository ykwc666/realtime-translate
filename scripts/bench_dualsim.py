"""Simulate dual-track on a wav and append metrics to optimization log."""
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

from app import config  # noqa: E402
from app.pipeline import TranslatePipeline  # noqa: E402

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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="dualsim-warmup")
    parser.add_argument("--note", default="双轨仿真 + TTS预热")
    parser.add_argument("--wav", default=str(DEFAULT_WAV))
    parser.add_argument("--play", action="store_true")
    parser.add_argument(
        "--asr-backend",
        default="",
        help="override config.ASR_BACKEND (e.g. sherpa_sensevoice / sherpa_paraformer_online)",
    )
    args = parser.parse_args()

    if args.asr_backend:
        config.ASR_BACKEND = args.asr_backend

    wav = Path(args.wav)
    if not wav.exists():
        print("wav missing", wav)
        return 2

    pipe = TranslatePipeline(enable_tts=True)
    t0 = time.perf_counter()
    results = pipe.run_dualsim(str(wav), play=args.play)
    wall = (time.perf_counter() - t0) * 1000
    if not results:
        print("no utterance produced")
        return 1
    r = results[0]
    row = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tag": args.tag,
        "note": args.note,
        "mode": "dualsim",
        "asr_backend": config.ASR_BACKEND,
        "partial_count": r.partial_count,
        "first_partial_ms": None if r.first_partial_ms is None else round(r.first_partial_ms, 1),
        "first_stable_mt_ms": None
        if r.first_stable_mt_ms is None
        else round(r.first_stable_mt_ms, 1),
        "utt_wall_ms": None if r.utt_wall_ms is None else round(r.utt_wall_ms, 1),
        "asr_ms_median": round(sorted(r.asr_ms)[len(r.asr_ms) // 2], 1) if r.asr_ms else 0,
        "asr_ms_sum": round(sum(r.asr_ms), 1),
        "asr_model_samples": getattr(r, "asr_model_samples", []),
        "mt_ms_sum": round(sum(r.mt_ms), 1),
        "mt_triggers": getattr(r, "mt_triggers", 0),
        "tts_ms_sum": round(sum(r.tts_ms), 1),
        "sim_wall_ms": round(wall, 1),
        "sample_src": r.source,
        "sample_tgt": r.translated,
        "asr_incremental": config.ASR_INCREMENTAL,
        "asr_overlap_sec": config.ASR_OVERLAP_SEC,
        "mt_policy": getattr(config, "MT_POLICY", "chars"),
        "mt_waits": getattr(r, "mt_waits", 0),
        "mt_reasons": getattr(r, "mt_reasons", []),
        "partial_adaptive": getattr(config, "PARTIAL_ADAPTIVE", False),
        "partial_intervals": getattr(r, "partial_interval_used", []),
        "mt_pending_max_wait_sec": getattr(config, "MT_PENDING_MAX_WAIT_SEC", None),
    }
    LOG_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with LOG_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    md = (
        f"| {row['time']} | {row['tag']} | first_partial={row['first_partial_ms']} "
        f"first_stable_mt={row['first_stable_mt_ms']} wall={row['utt_wall_ms']} "
        f"partials={row['partial_count']} | {row['note']} |"
    )
    if LOG_MD.exists():
        text = LOG_MD.read_text(encoding="utf-8")
        LOG_MD.write_text(text.rstrip() + "\n" + md + "\n", encoding="utf-8")

    print(json.dumps(row, ensure_ascii=False, indent=2))
    print("DUALSIM_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
