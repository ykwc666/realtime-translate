"""A/B: VAD_MIN_UTT_SEC=0 vs 1.0 on continuous AMI (+ optional stitched shorts).

Metrics: fragment_rate (<1s, func-end), n_utts, first_stable_mt / latency_perceived.
device_ttfa is N/A without TTS playback — use first_stable_mt_ms as latency proxy
(gate: median rise ≤30ms).

python scripts/ab_vad_min_utt.py
"""
from __future__ import annotations

import json
import re
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
FUNC_ENDS = {
    "the",
    "a",
    "an",
    "to",
    "of",
    "and",
    "that",
    "he",
    "she",
    "was",
    "is",
    "do",
    "we",
}
CONT = ROOT / "cache" / "harvest_public" / "ami_en2001b_1993_2020.wav"
# Extra stress: a few longer ledger AMI clips joined with 0.35s silence
STITCH_IDS = [
    "ami_006",
    "ami_014",
    "ami_018",
    "ami_031",
    "ami_039",
]
MANIFEST = ROOT / "data" / "ledger_corpus" / "manifest.jsonl"
OUT_JSON = ROOT / "docs" / "vad_min_utt_ab.json"
OUT_JSONL = ROOT / "docs" / "vad_min_utt_ab_detail.jsonl"


def _norm_words(text: str) -> list[str]:
    t = (text or "").lower()
    t = re.sub(r"[^a-z0-9'\s]+", " ", t)
    return [w for w in t.split() if w]


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[len(s) // 2], 1)


def _p90(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[max(0, int(0.9 * (len(s) - 1)))], 1)


def build_audio() -> tuple[np.ndarray, dict]:
    parts: list[np.ndarray] = []
    meta: dict = {"parts": []}
    cont = load_wav(str(CONT))
    parts.append(cont)
    meta["parts"].append({"path": str(CONT.relative_to(ROOT)), "sec": round(cont.size / SR, 3)})

    gap = np.zeros(int(0.35 * SR), np.float32)
    man = {
        json.loads(l)["id"]: json.loads(l)
        for l in MANIFEST.read_text(encoding="utf-8").splitlines()
        if l.strip()
    }
    for uid in STITCH_IDS:
        it = man.get(uid)
        if not it:
            continue
        wav = load_wav(str(ROOT / it["path"]))
        parts.append(gap)
        parts.append(wav)
        meta["parts"].append(
            {
                "id": uid,
                "path": it["path"],
                "sec": round(wav.size / SR, 3),
                "ref": it.get("ref"),
            }
        )
    audio = np.concatenate(parts) if parts else np.zeros(0, np.float32)
    # trailing silence so last utt can finalize
    audio = np.concatenate([audio, np.zeros(int(1.2 * SR), np.float32)])
    meta["total_sec"] = round(audio.size / SR, 3)
    return audio, meta


def run_once(pipe: TranslatePipeline, audio: np.ndarray, min_utt: float) -> dict:
    config.VAD_MIN_UTT_SEC = float(min_utt)
    engine = DualTrackEngine(
        pipe.asr,
        pipe.translator,
        None,
        play=False,
        force_src_lang="en",
        label=f"vmin{min_utt}",
    )
    utts: list[dict] = []
    for i in range(0, audio.size, BLOCK):
        finished = engine.feed_chunk(audio[i : i + BLOCK], allow_async_partial=False)
        if finished is not None:
            words = _norm_words(finished.source or "")
            utts.append(
                {
                    "src": finished.source or "",
                    "tgt": finished.translated or "",
                    "audio_sec": float(finished.audio_sec_final or 0),
                    "first_stable_mt_ms": finished.first_stable_mt_ms,
                    "latency_perceived_ms": finished.latency_perceived_ms,
                    "latency_device_ttfa_ms": finished.latency_device_ttfa_ms,
                    "func_end": bool(words and words[-1] in FUNC_ENDS),
                    "short_lt_1s": float(finished.audio_sec_final or 0) < 1.0,
                }
            )
    if engine.in_speech and engine.buf.size:
        pad = np.zeros(int(config.VAD_SILENCE_SEC * SR) + 1600, np.float32)
        for _ in range(32):
            finished = engine.feed_chunk(pad, allow_async_partial=False)
            if finished is not None:
                words = _norm_words(finished.source or "")
                utts.append(
                    {
                        "src": finished.source or "",
                        "tgt": finished.translated or "",
                        "audio_sec": float(finished.audio_sec_final or 0),
                        "first_stable_mt_ms": finished.first_stable_mt_ms,
                        "latency_perceived_ms": finished.latency_perceived_ms,
                        "latency_device_ttfa_ms": finished.latency_device_ttfa_ms,
                        "func_end": bool(words and words[-1] in FUNC_ENDS),
                        "short_lt_1s": float(finished.audio_sec_final or 0) < 1.0,
                    }
                )
                break

    n = len(utts)
    short = sum(1 for u in utts if u["short_lt_1s"])
    func = sum(1 for u in utts if u["func_end"])
    mt_lat = [float(u["first_stable_mt_ms"]) for u in utts if u.get("first_stable_mt_ms") is not None]
    perc = [
        float(u["latency_perceived_ms"])
        for u in utts
        if u.get("latency_perceived_ms") is not None
    ]
    ttfa = [
        float(u["latency_device_ttfa_ms"])
        for u in utts
        if u.get("latency_device_ttfa_ms") is not None
    ]
    return {
        "VAD_MIN_UTT_SEC": min_utt,
        "n_utts": n,
        "short_lt_1s": short,
        "short_rate": round(short / n, 4) if n else 0.0,
        "func_end": func,
        "func_end_rate": round(func / n, 4) if n else 0.0,
        "first_stable_mt_ms_p50": _median(mt_lat),
        "first_stable_mt_ms_p90": _p90(mt_lat),
        "latency_perceived_ms_p50": _median(perc),
        "latency_device_ttfa_ms_p50": _median(ttfa),
        "utts": utts,
    }


def main() -> int:
    audio, meta = build_audio()
    print(f"audio_sec={meta['total_sec']} parts={len(meta['parts'])}")
    print("loading pipeline…")
    pipe = TranslatePipeline(enable_tts=False)

    detail_rows: list[dict] = []
    summaries: list[dict] = []
    for vmin in (0.0, 1.0):
        print(f"\n=== VAD_MIN_UTT_SEC={vmin} ===")
        r = run_once(pipe, audio, vmin)
        summaries.append({k: v for k, v in r.items() if k != "utts"})
        for i, u in enumerate(r["utts"]):
            detail_rows.append({"VAD_MIN_UTT_SEC": vmin, "i": i, **u})
        print(
            f"n_utts={r['n_utts']} short_rate={r['short_rate']} "
            f"func_end_rate={r['func_end_rate']} "
            f"mt_p50={r['first_stable_mt_ms_p50']} perc_p50={r['latency_perceived_ms_p50']}"
        )

    # restore default
    config.VAD_MIN_UTT_SEC = 0.0

    base = next(s for s in summaries if s["VAD_MIN_UTT_SEC"] == 0.0)
    treat = next(s for s in summaries if s["VAD_MIN_UTT_SEC"] == 1.0)
    mt0 = base.get("first_stable_mt_ms_p50")
    mt1 = treat.get("first_stable_mt_ms_p50")
    mt_delta = None
    if mt0 is not None and mt1 is not None:
        mt_delta = round(mt1 - mt0, 1)

    frag_improved = treat["short_rate"] < base["short_rate"] or (
        treat["func_end_rate"] < base["func_end_rate"]
        and treat["n_utts"] <= base["n_utts"]
    )
    latency_ok = mt_delta is None or mt_delta <= 30.0
    # Enable only if fragment improves AND latency gate holds AND we actually
    # observed short fragments at baseline (otherwise knob is idle).
    recommend = bool(
        frag_improved and latency_ok and base["short_lt_1s"] + base["func_end"] > 0
    )
    # If baseline has zero short fragments, keep off (no evidence on this clip).
    if base["short_lt_1s"] == 0 and treat["short_lt_1s"] == 0:
        recommend = False
        reason = (
            "baseline short_lt_1s=0 on this continuous mix; "
            "knob idle for <1s fragments — keep default 0"
        )
    elif recommend:
        reason = (
            f"fragment improved (short {base['short_rate']}→{treat['short_rate']}, "
            f"func_end {base['func_end_rate']}→{treat['func_end_rate']}) "
            f"and first_stable_mt_p50 delta={mt_delta}ms ≤30"
        )
    else:
        reason = (
            f"no net win: frag_improved={frag_improved} latency_ok={latency_ok} "
            f"mt_delta={mt_delta}"
        )

    out = {
        "audio": meta,
        "variants": summaries,
        "mt_p50_delta_ms": mt_delta,
        "latency_gate_30ms": latency_ok,
        "recommend_default_1_0": recommend,
        "decision_reason": reason,
        "note": (
            "device_ttfa N/A (enable_tts=False); latency proxy=first_stable_mt_ms_p50. "
            "No postedit/TM changes."
        ),
    }
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with OUT_JSONL.open("w", encoding="utf-8") as f:
        for r in detail_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps({k: out[k] for k in out if k != "audio"}, ensure_ascii=False, indent=2))
    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_JSONL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
