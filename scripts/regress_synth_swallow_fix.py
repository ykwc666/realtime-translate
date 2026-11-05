"""Synth regression after emitted_swallowed fix.

python scripts/regress_synth_swallow_fix.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402
from app.audio_io import load_wav  # noqa: E402
from app.dual_track import DualTrackEngine  # noqa: E402
from app.pipeline import TranslatePipeline  # noqa: E402

SR = int(config.SAMPLE_RATE)
BLOCK = int(SR * 0.1)
WAVS = [
    ROOT / "cache" / "ab_weekday" / "accept_phrases.wav",
    ROOT / "cache" / "ab_weekday" / "tail_duosimo.wav",
    ROOT / "cache" / "ab_weekday" / "pause_xingqi_er.wav",
]
OUT = ROOT / "docs" / "synth_swallow_fix_regress.json"


def run_one(pipe: TranslatePipeline, wav: Path) -> dict:
    audio = load_wav(str(wav))
    engine = DualTrackEngine(
        pipe.asr, pipe.translator, None, play=False, force_src_lang="zh", label="reg"
    )
    # accept/tail/pause are zh phrases in ab_weekday
    utts = []
    for i in range(0, audio.size, BLOCK):
        fin = engine.feed_chunk(audio[i : i + BLOCK], allow_async_partial=False)
        if fin is not None:
            utts.append(
                {
                    "src": fin.source or "",
                    "tgt": fin.translated or "",
                    "audio_sec": fin.audio_sec_final,
                    "latency_perceived_ms": fin.latency_perceived_ms,
                    "first_stable_mt_ms": fin.first_stable_mt_ms,
                }
            )
    if engine.in_speech and engine.buf.size or engine._holding_cut:
        pad = np.zeros(int(config.VAD_SILENCE_SEC * SR) + 1600, np.float32)
        for _ in range(40):
            fin = engine.feed_chunk(pad, allow_async_partial=False)
            if fin is not None:
                utts.append(
                    {
                        "src": fin.source or "",
                        "tgt": fin.translated or "",
                        "audio_sec": fin.audio_sec_final,
                        "latency_perceived_ms": fin.latency_perceived_ms,
                        "first_stable_mt_ms": fin.first_stable_mt_ms,
                    }
                )
                break
        if engine._holding_cut:
            held = engine._force_hold_cut("regress_flush")
            if held is not None:
                fin = engine._finalize_utt(held)
                if fin is not None:
                    utts.append(
                        {
                            "src": fin.source or "",
                            "tgt": fin.translated or "",
                            "audio_sec": fin.audio_sec_final,
                            "latency_perceived_ms": fin.latency_perceived_ms,
                            "first_stable_mt_ms": fin.first_stable_mt_ms,
                        }
                    )
    lats = [u["latency_perceived_ms"] for u in utts if u.get("latency_perceived_ms") is not None]
    return {
        "wav": wav.name,
        "n_utts": len(utts),
        "srcs": [u["src"] for u in utts],
        "tgts": [u["tgt"] for u in utts],
        "latency_perceived_ms_p50": (
            sorted(lats)[len(lats) // 2] if lats else None
        ),
        "utts": utts,
    }


def main() -> int:
    pipe = TranslatePipeline(enable_tts=False)
    rows = [run_one(pipe, w) for w in WAVS if w.exists()]
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for r in rows:
        print(f"{r['wav']}: n={r['n_utts']} lat_p50={r['latency_perceived_ms_p50']}")
        for s in r["srcs"]:
            print(f"  src={s!r}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
