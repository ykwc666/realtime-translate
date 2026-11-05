"""Phase A: attribute every ledger item's pipeline emptiness.

drop_reason ∈ {
  vad_never_open, emitted_swallowed, finalize_empty, mt_skip, produced
}

python scripts/decompose_empty_utts.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
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
OUT = ROOT / "docs" / "empty_utt_decompose.jsonl"
SUM = ROOT / "docs" / "empty_utt_decompose_summary.json"


def _pad(raw: np.ndarray) -> np.ndarray:
    pad_n = int(0.5 * SR)
    return np.concatenate(
        [
            np.zeros(pad_n, np.float32),
            np.asarray(raw, dtype=np.float32).reshape(-1),
            np.zeros(int(1.0 * SR), np.float32),
        ]
    )


def classify_one(pipe: TranslatePipeline, wav: Path) -> dict:
    audio = _pad(load_wav(str(wav)))
    engine = DualTrackEngine(
        pipe.asr,
        pipe.translator,
        None,
        play=False,
        force_src_lang="en",
        label="decomp",
    )

    flags = {
        "endpoint_in_speech_ever": False,
        "engine_in_speech_ever": False,
        "seg_emitted": 0,
        "finalize_utt_entered": 0,
        "finalize_stream_called": 0,
        "finalize_utt_early_no_partial": 0,
        "finalize_utt_empty_asr": 0,
        "finalize_utt_short_skip": 0,
        "empty_abort": 0,
        "returned_utts": 0,
        "last_src": "",
        "last_tgt": "",
        "hold_events": 0,
    }

    # Spy endpoint.feed
    ep = engine.endpoint
    orig_feed = ep.feed

    def feed_spy(chunk):  # noqa: ANN001
        if ep.in_speech:
            flags["endpoint_in_speech_ever"] = True
        seg = orig_feed(chunk)
        if ep.in_speech:
            flags["endpoint_in_speech_ever"] = True
        if seg is not None:
            flags["seg_emitted"] += 1
        return seg

    ep.feed = feed_spy  # type: ignore[method-assign]

    # Spy finalize_stream
    asr = pipe.asr
    orig_fs = asr.finalize_stream

    def fs_spy(full_audio, state):  # noqa: ANN001
        flags["finalize_stream_called"] += 1
        return orig_fs(full_audio, state)

    asr.finalize_stream = fs_spy  # type: ignore[method-assign]

    # Spy _finalize_utt
    orig_fin = engine._finalize_utt

    def fin_spy(seg, mute_cb=None):  # noqa: ANN001
        flags["finalize_utt_entered"] += 1
        # mirror early-drop detection before call
        if not engine._utt_announced and not (engine._last_partial_text or "").strip():
            flags["finalize_utt_early_no_partial"] += 1
        out = orig_fin(seg, mute_cb=mute_cb)
        if out is None and flags["finalize_stream_called"] > 0:
            # called ASR then dropped empty
            if flags["finalize_utt_early_no_partial"] == 0:
                flags["finalize_utt_empty_asr"] += 1
        if out is not None:
            src = (out.source or "").strip()
            tgt = (out.translated or "").strip()
            flags["last_src"] = src
            flags["last_tgt"] = tgt
            if src and not tgt:
                flags["finalize_utt_short_skip"] += 1
        return out

    engine._finalize_utt = fin_spy  # type: ignore[method-assign]

    # Spy reset paths that print empty abort — wrap feed_chunk and count resets
    # after in_speech with no return: use print sniff via empty abort message
    # Better: wrap reset_utt when coming from empty abort by checking audio_sec
    orig_reset = engine.reset_utt
    _prev_in = False

    def reset_spy():  # noqa: ANN001
        nonlocal _prev_in
        # Heuristic: if we had in_speech, buf, no text → empty abort-ish
        if (
            engine.in_speech
            and engine.buf.size
            and not (engine._last_partial_text or "").strip()
            and engine.audio_sec >= float(getattr(config, "VAD_EMPTY_ABORT_SEC", 2.0)) * 0.9
        ):
            flags["empty_abort"] += 1
        return orig_reset()

    # Don't wrap reset_utt — too noisy. Patch feed_chunk empty-abort by
    # detecting when in_speech goes true then false without returned utt
    # after seg was emitted — classified below.

    utts = []
    for i in range(0, audio.size, BLOCK):
        if engine.endpoint.in_speech or engine.in_speech:
            flags["endpoint_in_speech_ever"] = (
                flags["endpoint_in_speech_ever"] or engine.endpoint.in_speech
            )
            flags["engine_in_speech_ever"] = (
                flags["engine_in_speech_ever"] or engine.in_speech
            )
        if engine._holding_cut:
            flags["hold_events"] += 1
        finished = engine.feed_chunk(audio[i : i + BLOCK], allow_async_partial=False)
        if finished is not None:
            flags["returned_utts"] += 1
            utts.append(
                {
                    "src": finished.source or "",
                    "tgt": finished.translated or "",
                }
            )

    if engine.in_speech and engine.buf.size:
        pad = np.zeros(int(config.VAD_SILENCE_SEC * SR) + 1600, np.float32)
        for _ in range(40):
            if engine._holding_cut:
                flags["hold_events"] += 1
            finished = engine.feed_chunk(pad, allow_async_partial=False)
            if finished is not None:
                flags["returned_utts"] += 1
                utts.append(
                    {
                        "src": finished.source or "",
                        "tgt": finished.translated or "",
                    }
                )
                break

    # restore spies
    ep.feed = orig_feed  # type: ignore[method-assign]
    asr.finalize_stream = orig_fs  # type: ignore[method-assign]
    engine._finalize_utt = orig_fin  # type: ignore[method-assign]

    joined_src = " ".join(u["src"] for u in utts if u.get("src")).strip()
    joined_tgt = "".join(u["tgt"] for u in utts if u.get("tgt")).strip()

    # Attribution (exactly one)
    if joined_src and joined_tgt:
        reason = "produced"
    elif joined_src and not joined_tgt:
        reason = "mt_skip"
    elif flags["finalize_stream_called"] > 0 and not joined_src:
        reason = "finalize_empty"
    elif flags["finalize_utt_short_skip"] > 0 and flags["last_src"] and not flags["last_tgt"]:
        # short path returns stats with src — should have been returned
        reason = "mt_skip"
        joined_src = flags["last_src"]
    elif flags["seg_emitted"] > 0 and flags["finalize_stream_called"] == 0:
        reason = "emitted_swallowed"
    elif flags["finalize_utt_entered"] > 0 and flags["finalize_stream_called"] == 0:
        # entered finalize but early-dropped before ASR
        reason = "emitted_swallowed"
    elif not flags["endpoint_in_speech_ever"] and flags["seg_emitted"] == 0:
        reason = "vad_never_open"
    elif flags["engine_in_speech_ever"] and flags["seg_emitted"] == 0 and not joined_src:
        # opened then empty-aborted without VAD seg
        reason = "emitted_swallowed" if flags["empty_abort"] else "vad_never_open"
        if flags["empty_abort"] or flags["engine_in_speech_ever"]:
            # Prefer: if engine thought it was in speech but never finalized
            if flags["finalize_stream_called"] == 0 and flags["returned_utts"] == 0:
                reason = "emitted_swallowed"
    else:
        # fallback
        if not flags["endpoint_in_speech_ever"]:
            reason = "vad_never_open"
        elif flags["finalize_stream_called"] == 0:
            reason = "emitted_swallowed"
        else:
            reason = "finalize_empty"

    return {
        "drop_reason": reason,
        "src": joined_src,
        "tgt": joined_tgt,
        "n_utts": len(utts),
        **flags,
    }


def main() -> int:
    rows = [
        json.loads(l)
        for l in MANIFEST.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    print(f"loading pipeline… n={len(rows)}")
    pipe = TranslatePipeline(enable_tts=False)
    out_rows: list[dict] = []
    for i, it in enumerate(rows):
        wav = ROOT / it["path"]
        print(f"[{i+1}/{len(rows)}] {it['id']}")
        c = classify_one(pipe, wav)
        rec = {
            "id": it["id"],
            "corpus": it["corpus"],
            "path": it["path"],
            "ref": it.get("ref"),
            **c,
        }
        out_rows.append(rec)
        print(f"  → {c['drop_reason']} seg={c['seg_emitted']} fs={c['finalize_stream_called']}")

    with OUT.open("w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    by_reason = Counter(r["drop_reason"] for r in out_rows)
    empty = [r for r in out_rows if r["drop_reason"] != "produced"]
    by_corp: dict[str, dict] = {}
    for r in out_rows:
        c = r["corpus"]
        by_corp.setdefault(c, Counter())[r["drop_reason"]] += 1
    by_corp_out = {k: dict(v) for k, v in by_corp.items()}

    # Integrity: each empty has exactly one reason; count empties
    n_empty = len(empty)
    summary = {
        "n": len(out_rows),
        "n_empty": n_empty,
        "n_produced": by_reason.get("produced", 0),
        "by_reason": dict(by_reason),
        "by_corpus": by_corp_out,
        "empty_ids": {
            k: [r["id"] for r in empty if r["drop_reason"] == k]
            for k in (
                "vad_never_open",
                "emitted_swallowed",
                "finalize_empty",
                "mt_skip",
            )
        },
    }
    SUM.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"wrote {OUT}")
    print(f"wrote {SUM}")
    # assert unique attribution
    assert all(
        r["drop_reason"]
        in (
            "vad_never_open",
            "emitted_swallowed",
            "finalize_empty",
            "mt_skip",
            "produced",
        )
        for r in out_rows
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
