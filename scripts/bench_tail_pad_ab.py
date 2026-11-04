"""A/B ONLINE_TAIL_PAD_SEC: quality (src) vs asr_wait / mt_ready.

  .venv\\Scripts\\python.exe scripts\\bench_tail_pad_ab.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.config import setup_env  # noqa: E402

setup_env()
from app import config  # noqa: E402
from app.pipeline import TranslatePipeline  # noqa: E402

_bp = importlib.util.spec_from_file_location("bp", ROOT / "scripts" / "bench_paced.py")
assert _bp and _bp.loader
bp = importlib.util.module_from_spec(_bp)
_bp.loader.exec_module(bp)

WAVS = [
    ROOT / "cache" / "ab_weekday" / "pause_xingqi_er.wav",
    ROOT / "cache" / "ab_weekday" / "accept_phrases.wav",
    ROOT / "cache" / "ab_weekday" / "cont_tuesday.wav",
    ROOT / "cache" / "ab_weekday" / "tail_duosimo.wav",
]
PADS = (1.0, 0.5, 0.25)


def _med(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[len(s) // 2]


def main() -> int:
    config.VAD_SILENCE_SEC = 0.45
    config.ENDPOINT_EARLY_CUT_PROBE = False
    config.ASR_SPECULATE_REARM_MODE = "offline"
    print("load…")
    pipe = TranslatePipeline(enable_tts=False)
    out: list[dict] = []
    for wav in WAVS:
        if not wav.exists():
            print(f"missing {wav}")
            continue
        ref_srcs: list[str] | None = None
        for pad in PADS:
            config.ONLINE_TAIL_PAD_SEC = float(pad)
            print(f"\n## {wav.name} pad={pad}")
            rows = bp._run_paced(
                pipe,
                f"pad{pad}-{wav.stem}",
                wav,
                pace=1.0,
                note=f"pad={pad}",
                play=False,
                close_tts=False,
            )
            srcs = [str(r.get("src") or "") for r in rows]
            # strip display punct for compare
            bare = [s.rstrip("。！？.!?,，、；; ") for s in srcs]
            if pad == 1.0 and ref_srcs is None:
                ref_srcs = list(bare)
            waits = [
                float(r["asr_wait_after_cut_ms"])
                for r in rows
                if r.get("asr_wait_after_cut_ms") is not None
            ]
            mts = [
                float(r["latency_perceived_ms"])
                for r in rows
                if r.get("latency_perceived_ms") is not None
            ]
            mismatch = bare != (ref_srcs or bare)
            rec = {
                "wav": wav.name,
                "pad_sec": pad,
                "srcs": srcs,
                "bare_srcs": bare,
                "mismatch_vs_1s": mismatch,
                "asr_wait_med": _med(waits),
                "mt_ready_med": _med(mts),
                "n": len(rows),
                "spec_hit": sum(1 for r in rows if r.get("speculate_hit")),
            }
            out.append(rec)
            print(
                f"  wait={rec['asr_wait_med']} mt={rec['mt_ready_med']} "
                f"mismatch={mismatch} srcs={bare}"
            )
    path = ROOT / "docs" / "tail_pad_ab.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {path}")
    # restore default
    config.ONLINE_TAIL_PAD_SEC = 1.0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
