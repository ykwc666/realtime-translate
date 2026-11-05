"""Diagnose ami_046: KEEP_TAIL vs REJECT_STALE vs loop soft.

python scripts/diag_ami046_loop_spec.py
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
MANIFEST = ROOT / "data" / "ledger_corpus" / "manifest.jsonl"
OUT = ROOT / "docs" / "ami046_loop_spec_diag.json"

CASES = [
    {
        "name": "current_defaults",
        "ASR_LOOP_GUARD_MODE": "soft",
        "ASR_LOOP_KEEP_TAIL": True,
        "ASR_SPEC_REJECT_STALE": True,
        "ASR_SPEC_MISS_GROW_USE_P90": True,
    },
    {
        "name": "stale_off",
        "ASR_LOOP_GUARD_MODE": "soft",
        "ASR_LOOP_KEEP_TAIL": True,
        "ASR_SPEC_REJECT_STALE": False,
        "ASR_SPEC_MISS_GROW_USE_P90": True,
    },
    {
        "name": "keep_tail_off",
        "ASR_LOOP_GUARD_MODE": "soft",
        "ASR_LOOP_KEEP_TAIL": False,
        "ASR_SPEC_REJECT_STALE": True,
        "ASR_SPEC_MISS_GROW_USE_P90": True,
    },
    {
        "name": "legacy_loop_stale_on",
        "ASR_LOOP_GUARD_MODE": "legacy",
        "ASR_LOOP_KEEP_TAIL": True,
        "ASR_SPEC_REJECT_STALE": True,
        "ASR_SPEC_MISS_GROW_USE_P90": True,
    },
    {
        "name": "pre_loop_fix_like",
        "ASR_LOOP_GUARD_MODE": "legacy",
        "ASR_LOOP_KEEP_TAIL": False,
        "ASR_SPEC_REJECT_STALE": False,
        "ASR_SPEC_MISS_GROW_USE_P90": False,
    },
]


def _pad(raw: np.ndarray) -> np.ndarray:
    n = int(0.5 * SR)
    return np.concatenate(
        [
            np.zeros(n, np.float32),
            np.asarray(raw, dtype=np.float32).reshape(-1),
            np.zeros(n, np.float32),
        ]
    )


def run_case(pipe: TranslatePipeline, audio: np.ndarray, case: dict) -> dict:
    for k, v in case.items():
        if k == "name":
            continue
        setattr(config, k, v)
    eng = DualTrackEngine(
        pipe.asr, pipe.translator, None, play=False, force_src_lang="en", label="046"
    )
    utts = []
    for i in range(0, audio.size, BLOCK):
        fin = eng.feed_chunk(audio[i : i + BLOCK], allow_async_partial=False)
        if fin is not None:
            utts.append(
                {
                    "src": fin.source or "",
                    "audio_sec": float(fin.audio_sec_final or 0),
                }
            )
    if eng.in_speech and eng.buf.size or eng._holding_cut:
        pad = np.zeros(int(config.VAD_SILENCE_SEC * SR) + 1600, np.float32)
        for _ in range(40):
            fin = eng.feed_chunk(pad, allow_async_partial=False)
            if fin is not None:
                utts.append(
                    {
                        "src": fin.source or "",
                        "audio_sec": float(fin.audio_sec_final or 0),
                    }
                )
                break
        if eng._holding_cut:
            held = eng._force_hold_cut("046_flush")
            if held is not None:
                fin = eng._finalize_utt(held)
                if fin is not None:
                    utts.append(
                        {
                            "src": fin.source or "",
                            "audio_sec": float(fin.audio_sec_final or 0),
                        }
                    )
    return {
        "name": case["name"],
        "config": {k: v for k, v in case.items() if k != "name"},
        "n_utts": len(utts),
        "srcs": [u["src"] for u in utts],
        "spec_counters": dict(eng.spec_counters),
    }


def main() -> int:
    man = {
        json.loads(l)["id"]: json.loads(l)
        for l in MANIFEST.read_text(encoding="utf-8").splitlines()
        if l.strip()
    }
    item = man["ami_046"]
    wav = ROOT / item["path"]
    audio = _pad(load_wav(str(wav)))
    print("loading pipeline…", wav)
    pipe = TranslatePipeline(enable_tts=False)
    rows = []
    for case in CASES:
        print(f"\n=== {case['name']} ===")
        row = run_case(pipe, audio, case)
        rows.append(row)
        print("n_utts", row["n_utts"], "srcs", row["srcs"])
        print("spec", row["spec_counters"])
    out = {"id": "ami_046", "path": item["path"], "ref": item.get("ref"), "cases": rows}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
