"""A/B: batch vs clause-stream TTS + OutputStream open/callback overhead.

Metrics (all from speak_t0 unless noted):
  ttfa_pcm_ms       first nonempty PCM ready
  stream_open_ms    OutputStream construct/start (0 if reused)
  ttfa_callback_ms  first callback write of nonzero PCM
  close_or_drain_ms last write → play returned
  residual_ms       speak_wall - synth_ms - play_sec*1000
  pcm_to_cb_ms      ttfa_callback - ttfa_pcm  (device+open after PCM)

Usage:
  python scripts/bench_tts_stream_ab.py
  python scripts/bench_tts_stream_ab.py --reps 5 --overhead-only
  python scripts/bench_tts_stream_ab.py --persistent-ab
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402
from app.tts_play import TtsPlayer  # noqa: E402

PHRASES = [
    ("en", "Hello."),
    ("en", "How's the weather today?"),
    ("en", "I might not pass this interview round."),
    (
        "en",
        "Hang on a second, I'll be right back after I check the meeting notes.",
    ),
    (
        "en",
        "Okay, please say that again, and speak a little slower.",
    ),
    ("zh", "稍等一下，我马上回来。"),
    ("zh", "延迟大概有多少"),
    ("zh", "我可能过不了这一面"),
]


def _median(xs: list[float]) -> float:
    return float(statistics.median(xs)) if xs else float("nan")


def _timing_fields(syn, play_sec: float, wall: float) -> dict:
    pt = syn.play_timing
    ttfa_pcm = float(getattr(syn, "ttfa_pcm_ms", None) or syn.ttfa_ms or 0.0)
    open_ms = float(pt.stream_open_ms) if pt else 0.0
    ttfa_cb = float(pt.ttfa_callback_ms) if pt else 0.0
    close_ms = float(pt.close_or_drain_ms) if pt else 0.0
    underruns = int(pt.underruns) if pt else 0
    reused = bool(pt.stream_reused) if pt else False
    residual = wall - float(syn.ms) - float(play_sec) * 1000.0
    pcm_to_cb = (ttfa_cb - ttfa_pcm) if ttfa_cb > 0 else float("nan")
    return {
        "ttfa_ms": round(ttfa_pcm, 1),
        "ttfa_pcm_ms": round(ttfa_pcm, 1),
        "stream_open_ms": round(open_ms, 1),
        "ttfa_callback_ms": round(ttfa_cb, 1),
        "close_or_drain_ms": round(close_ms, 1),
        "pcm_to_cb_ms": round(pcm_to_cb, 1) if pcm_to_cb == pcm_to_cb else None,
        "residual_ms": round(residual, 1),
        "underruns": underruns,
        "stream_reused": reused,
        "synth_or_wall_ms": round(float(syn.ms), 1),
        "speak_wall_ms": round(wall, 1),
        "play_sec": round(play_sec, 3),
        "audio_samples": int(syn.audio.size) if syn.audio is not None else 0,
    }


def _run_one(
    tts: TtsPlayer, text: str, lang: str, stream: bool, play: bool
) -> dict:
    config.TTS_STREAM_PLAY = stream
    t0 = time.perf_counter()
    if stream:
        syn, play_sec = tts.speak(text, lang, save_wav=False)
        wall = (time.perf_counter() - t0) * 1000
        base = {
            "mode": "stream",
            "lang": lang,
            "text": text,
            "persistent": bool(getattr(config, "TTS_PERSISTENT_OUTPUT", False)),
        }
        base.update(_timing_fields(syn, play_sec, wall))
        return base

    syn = tts.synthesize(text, lang, save_wav=False)
    syn.ttfa_pcm_ms = float(syn.ms)
    syn.ttfa_ms = float(syn.ms)
    play_sec = 0.0
    if play and syn.audio is not None and syn.audio.size:
        play_sec = tts.play_array(syn.audio, syn.sample_rate, speak_t0=t0)
        syn.play_timing = tts.last_play_timing
    wall = (time.perf_counter() - t0) * 1000
    base = {
        "mode": "batch",
        "lang": lang,
        "text": text,
        "persistent": bool(getattr(config, "TTS_PERSISTENT_OUTPUT", False)),
    }
    base.update(_timing_fields(syn, play_sec, wall))
    return base


def _print_row(row: dict) -> None:
    print(
        f"{row['mode']:<7} {'P' if row.get('persistent') else '-':<1} "
        f"{row['lang']:<3} "
        f"pcm={row['ttfa_pcm_ms']:6.1f} open={row['stream_open_ms']:5.1f} "
        f"cb={row['ttfa_callback_ms']:6.1f} drain={row['close_or_drain_ms']:5.1f} "
        f"res={row['residual_ms']:6.1f} | {row['text'][:36]}"
    )


def _run_matrix(
    tts: TtsPlayer, reps: int, play: bool, persistent: bool
) -> list[dict]:
    config.TTS_PERSISTENT_OUTPUT = persistent
    rows: list[dict] = []
    for stream in (False, True):
        for lang, text in PHRASES:
            buckets: dict[str, list[float]] = {
                "ttfa_pcm_ms": [],
                "stream_open_ms": [],
                "ttfa_callback_ms": [],
                "close_or_drain_ms": [],
                "residual_ms": [],
                "pcm_to_cb_ms": [],
                "synth_or_wall_ms": [],
                "speak_wall_ms": [],
            }
            last: dict = {}
            for _ in range(reps):
                last = _run_one(tts, text, lang, stream=stream, play=play)
                for k in buckets:
                    v = last.get(k)
                    if v is not None:
                        buckets[k].append(float(v))
            row = {**last, "reps": reps}
            for k, xs in buckets.items():
                if xs:
                    row[k] = round(_median(xs), 1)
            if "ttfa_ms" in row:
                row["ttfa_ms"] = row["ttfa_pcm_ms"]
            rows.append(row)
            _print_row(row)
    return rows


def _overhead_verdict(rows: list[dict]) -> dict:
    """Gate from task prompt: stop Phase2 only if BOTH
    (per-utt extra overhead <80) AND (pcm→callback <80).

    Extra overhead ≈ residual on single-clause rows (multi-clause syn.ms
    overlaps play → residual meaningless). Prefer close_or_drain + open.
    """
    stream_rows = [r for r in rows if r["mode"] == "stream" and not r.get("persistent")]
    # single-clause-ish: residual > 0 (multi-clause stream goes largely negative)
    single = [r for r in stream_rows if float(r.get("residual_ms") or 0) > -500]
    opens = [float(r["stream_open_ms"]) for r in stream_rows]
    drains = [float(r["close_or_drain_ms"]) for r in stream_rows]
    pcm_cbs = [
        float(r["pcm_to_cb_ms"])
        for r in stream_rows
        if r.get("pcm_to_cb_ms") is not None
    ]
    extras = [
        float(r["stream_open_ms"]) + float(r["close_or_drain_ms"]) for r in stream_rows
    ]
    residuals = [float(r["residual_ms"]) for r in single]
    med_open = _median(opens)
    med_drain = _median(drains)
    med_extra = _median(extras)
    med_pcm_cb = _median(pcm_cbs) if pcm_cbs else float("nan")
    med_res = _median(residuals) if residuals else float("nan")
    # Enter Phase2 unless BOTH overhead and pcm_to_cb are <80
    overhead_ok_skip = med_extra < 80.0
    pcm_ok_skip = (med_pcm_cb == med_pcm_cb) and med_pcm_cb < 80.0
    recommend = not (overhead_ok_skip and pcm_ok_skip)
    return {
        "stream_open_median_ms": round(med_open, 1),
        "close_or_drain_median_ms": round(med_drain, 1),
        "extra_open_plus_drain_median_ms": round(med_extra, 1),
        "pcm_to_cb_median_ms": round(med_pcm_cb, 1) if med_pcm_cb == med_pcm_cb else None,
        "residual_single_median_ms": round(med_res, 1) if med_res == med_res else None,
        "gate_threshold_ms": 80.0,
        "recommend_persistent": recommend,
        "reason": (
            "extra_overhead_or_pcm_to_cb_ge_80"
            if recommend
            else "both_extra_and_pcm_to_cb_lt_80"
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument(
        "--no-play",
        action="store_true",
        help="batch: skip play; stream still plays",
    )
    ap.add_argument(
        "--overhead-only",
        action="store_true",
        help="only oneshot baseline + overhead gate (skip persistent A/B)",
    )
    ap.add_argument(
        "--persistent-ab",
        action="store_true",
        help="force oneshot vs persistent A/B even if gate < 80ms",
    )
    args = ap.parse_args()

    tts = TtsPlayer()
    print("warmup…")
    print(tts.warmup())

    print(
        "\nmode P lang  pcm/open/cb/drain/residual | text  "
        "(P=persistent)"
    )
    print("-" * 100)

    oneshot_rows = _run_matrix(
        tts, args.reps, play=not args.no_play, persistent=False
    )
    verdict = _overhead_verdict(oneshot_rows)
    print("\n## overhead gate (oneshot stream)")
    print(json.dumps(verdict, ensure_ascii=False, indent=2))

    persist_rows: list[dict] = []
    persist_delta: dict | None = None
    do_persist = (not args.overhead_only) and (
        args.persistent_ab or verdict["recommend_persistent"]
    )
    if do_persist:
        print("\n## persistent OutputStream A/B")
        persist_rows = _run_matrix(
            tts, args.reps, play=not args.no_play, persistent=True
        )
        # paired: stream mode oneshot vs persist on same text
        deltas_open: list[float] = []
        deltas_cb: list[float] = []
        deltas_wall: list[float] = []
        print("\n| text | open_on | open_p | cb_on | cb_p | wall_on | wall_p |")
        print("|---|---:|---:|---:|---:|---:|---:|")
        for lang, text in PHRASES:
            a = next(
                r
                for r in oneshot_rows
                if r["mode"] == "stream" and r["text"] == text
            )
            b = next(
                r
                for r in persist_rows
                if r["mode"] == "stream" and r["text"] == text
            )
            deltas_open.append(a["stream_open_ms"] - b["stream_open_ms"])
            deltas_cb.append(a["ttfa_callback_ms"] - b["ttfa_callback_ms"])
            deltas_wall.append(a["speak_wall_ms"] - b["speak_wall_ms"])
            print(
                f"| {text[:36]} | {a['stream_open_ms']:.1f} | {b['stream_open_ms']:.1f} "
                f"| {a['ttfa_callback_ms']:.1f} | {b['ttfa_callback_ms']:.1f} "
                f"| {a['speak_wall_ms']:.1f} | {b['speak_wall_ms']:.1f} |"
            )
        persist_delta = {
            "open_delta_median_ms": round(_median(deltas_open), 1),
            "callback_ttfa_delta_median_ms": round(_median(deltas_cb), 1),
            "speak_wall_delta_median_ms": round(_median(deltas_wall), 1),
        }
        # Default on only if callback TTFA improves ≥40ms and underruns not worse
        und_on = sum(int(r.get("underruns") or 0) for r in oneshot_rows if r["mode"] == "stream")
        und_p = sum(int(r.get("underruns") or 0) for r in persist_rows if r["mode"] == "stream")
        default_on = (
            persist_delta["callback_ttfa_delta_median_ms"] >= 40.0
            and und_p <= und_on + 2
        )
        persist_delta["underruns_oneshot"] = und_on
        persist_delta["underruns_persistent"] = und_p
        persist_delta["recommend_default_on"] = default_on
        print("\npersist delta:", json.dumps(persist_delta, ensure_ascii=False))
    else:
        print(
            "\nskip persistent A/B "
            f"(gate_metric={verdict['gate_metric_ms']} < 80; "
            "pass --persistent-ab to force)"
        )

    # legacy paired TTFA (batch − stream) on oneshot
    print("\n## paired TTFA_pcm delta (batch − stream, oneshot)")
    print("| text | batch_pcm | stream_pcm | delta_ms |")
    print("|---|---:|---:|---:|")
    deltas: list[float] = []
    for lang, text in PHRASES:
        b = next(r for r in oneshot_rows if r["mode"] == "batch" and r["text"] == text)
        s = next(r for r in oneshot_rows if r["mode"] == "stream" and r["text"] == text)
        d = b["ttfa_pcm_ms"] - s["ttfa_pcm_ms"]
        deltas.append(d)
        print(
            f"| {text[:40]} | {b['ttfa_pcm_ms']:.1f} | {s['ttfa_pcm_ms']:.1f} | {d:+.1f} |"
        )
    print(
        f"\nTTFA_pcm median delta (batch-stream) = {_median(deltas):+.1f} ms"
    )

    out = ROOT / "docs" / "tts_stream_ab.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for r in oneshot_rows + persist_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        f.write(
            json.dumps(
                {
                    "summary": True,
                    "ttfa_pcm_delta_median_ms": round(_median(deltas), 1),
                    "overhead": verdict,
                    "persistent_delta": persist_delta,
                    "preroll_sec": config.TTS_STREAM_PREROLL_SEC,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    print(f"wrote {out}")
    config.TTS_STREAM_PLAY = True
    config.TTS_PERSISTENT_OUTPUT = False
    tts.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
