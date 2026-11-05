"""Trace ami_000 swallow path: arbiter / empty-abort / early finalize drop.

python scripts/diagnose_ami000_swallow.py
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
from app.endpoint_arbiter import ArbiterAction  # noqa: E402
from app.pipeline import TranslatePipeline  # noqa: E402

SR = int(config.SAMPLE_RATE)
BLOCK = int(SR * 0.1)
WAV = ROOT / "data" / "ledger_corpus" / "ami" 
# resolve from manifest
MAN = ROOT / "data" / "ledger_corpus" / "manifest.jsonl"


def main() -> int:
    rows = [json.loads(l) for l in MAN.read_text(encoding="utf-8").splitlines() if l.strip()]
    item = next(r for r in rows if r["id"] == "ami_000")
    wav = ROOT / item["path"]
    raw = load_wav(str(wav))
    pad_n = int(0.5 * SR)
    audio = np.concatenate(
        [
            np.zeros(pad_n, np.float32),
            raw,
            np.zeros(int(1.0 * SR), np.float32),
        ]
    )
    pipe = TranslatePipeline(enable_tts=False)
    engine = DualTrackEngine(
        pipe.asr, pipe.translator, None, play=False, force_src_lang="en", label="ami000"
    )
    log: list[dict] = []

    orig_on = engine.arbiter.on_vad_candidate
    orig_tick = engine.arbiter.tick
    orig_apply = engine._apply_arbiter
    orig_fin = engine._finalize_utt
    orig_fs = pipe.asr.finalize_stream

    def on_spy(asr_seq, now=None):  # noqa: ANN001
        d = orig_on(asr_seq, now=now)
        log.append({"ev": "on_vad_candidate", "action": d.action.value, "reason": d.reason})
        return d

    def tick_spy(text, asr_seq, now=None):  # noqa: ANN001
        d = orig_tick(text, asr_seq, now=now)
        if d.action != ArbiterAction.NONE:
            log.append(
                {
                    "ev": "tick",
                    "action": d.action.value,
                    "reason": d.reason,
                    "text": text,
                }
            )
        return d

    def fin_spy(seg, mute_cb=None):  # noqa: ANN001
        early = (not engine._utt_announced) and not (engine._last_partial_text or "").strip()
        log.append(
            {
                "ev": "finalize_utt_enter",
                "early_no_partial": early,
                "announced": engine._utt_announced,
                "partial": engine._last_partial_text,
                "audio_sec": engine.audio_sec,
                "holding": engine._holding_cut,
            }
        )
        out = orig_fin(seg, mute_cb=mute_cb)
        log.append({"ev": "finalize_utt_exit", "returned": out is not None})
        return out

    def fs_spy(a, s):  # noqa: ANN001
        log.append({"ev": "finalize_stream", "samples": int(np.asarray(a).size)})
        return orig_fs(a, s)

    engine.arbiter.on_vad_candidate = on_spy  # type: ignore
    engine.arbiter.tick = tick_spy  # type: ignore
    engine._finalize_utt = fin_spy  # type: ignore
    pipe.asr.finalize_stream = fs_spy  # type: ignore

    n_ret = 0
    for i in range(0, audio.size, BLOCK):
        fin = engine.feed_chunk(audio[i : i + BLOCK], allow_async_partial=False)
        if fin is not None:
            n_ret += 1
            log.append({"ev": "returned", "src": fin.source, "tgt": fin.translated})
    if engine.in_speech and engine.buf.size:
        pad = np.zeros(int(config.VAD_SILENCE_SEC * SR) + 1600, np.float32)
        for _ in range(40):
            fin = engine.feed_chunk(pad, allow_async_partial=False)
            if fin is not None:
                n_ret += 1
                log.append({"ev": "returned", "src": fin.source, "tgt": fin.translated})
                break
        else:
            log.append(
                {
                    "ev": "flush_exhausted",
                    "in_speech": engine.in_speech,
                    "holding": engine._holding_cut,
                    "partial": engine._last_partial_text,
                    "audio_sec": engine.audio_sec,
                }
            )

    out = {
        "id": "ami_000",
        "ref": item["ref"],
        "n_returned": n_ret,
        "log": log,
        "conclusion": None,
    }
    early = any(e.get("early_no_partial") for e in log if e.get("ev") == "finalize_utt_enter")
    fs = any(e.get("ev") == "finalize_stream" for e in log)
    if early and not fs:
        out["conclusion"] = (
            "emitted_swallowed via _finalize_utt early return "
            "(no utt_announced / no partial) before finalize_stream"
        )
    elif any(e.get("ev") == "flush_exhausted" and e.get("holding") for e in log):
        out["conclusion"] = "arbiter hold never released before flush exhausted"
    elif not any(e.get("ev") == "on_vad_candidate" for e in log):
        out["conclusion"] = "no VAD candidate (unexpected for ami_000 class)"
    else:
        out["conclusion"] = "see log"

    path = ROOT / "docs" / "ami000_swallow_diagnosis.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: out[k] for k in out if k != "log"}, ensure_ascii=False, indent=2))
    print("events:", [e.get("ev") for e in log])
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
