"""Microbench: 2-pass ASR serial vs parallel × thread split.

Reads cache/ab_weekday/{accept_phrases,tail_duosimo}.wav, segments with VAD,
rebuilds StreamingAsr per (online_threads, offline_threads, parallel).

Also probes feed_stream per-chunk ms under different ASR_THREADS_ONLINE
(speaking-path; not finalize).
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402
from app.asr_stream import AsrIncState, StreamingAsr  # noqa: E402
from app.audio_io import load_wav  # noqa: E402
from app.vad_stream import segment_audio  # noqa: E402

WAVS = [
    ROOT / "cache" / "ab_weekday" / "accept_phrases.wav",
    ROOT / "cache" / "ab_weekday" / "tail_duosimo.wav",
]


def _median(xs: list[float]) -> float:
    return float(statistics.median(xs)) if xs else float("nan")


def _load_segments() -> list[tuple[str, object]]:
    segs: list[tuple[str, object]] = []
    for wav in WAVS:
        if not wav.exists():
            print(f"MISSING {wav}")
            continue
        audio = load_wav(str(wav))
        parts = segment_audio(audio)
        print(f"{wav.name}: samples={audio.size} segs={len(parts)}")
        for i, seg in enumerate(parts):
            segs.append((f"{wav.stem}#{i}", seg.audio))
    return segs


def _make_asr(online_t: int, offline_t: int, parallel: bool) -> StreamingAsr:
    config.ASR_THREADS = max(online_t, offline_t, int(config.ASR_THREADS))
    config.ASR_THREADS_ONLINE = int(online_t)
    config.ASR_THREADS_OFFLINE = int(offline_t)
    config.ASR_2PASS_PARALLEL = bool(parallel)
    config.ASR_FINAL_2PASS = True
    config.ASR_BACKEND = "sherpa_paraformer_online"
    return StreamingAsr()


def _run_seg(asr: StreamingAsr, audio, mode: str) -> tuple[str, float, float]:
    """Return (text, wall_ms, model_ms).

    finalize uses empty AsrIncState → oneshot+offline via _run_2pass_pair
    (same as cold cut without live stream).
    """
    t0 = time.perf_counter()
    if mode == "finalize":
        res = asr.finalize_stream(audio, AsrIncState())
    else:
        res = asr.transcribe_with_2pass(audio)
    wall = (time.perf_counter() - t0) * 1000
    return (res.text or "").strip(), wall, float(res.ms)


def _bench_combo(
    segs: list[tuple[str, object]],
    online_t: int,
    offline_t: int,
    parallel: bool,
    reps: int,
    mode: str,
) -> dict:
    asr = _make_asr(online_t, offline_t, parallel)
    try:
        # warmup
        if segs:
            _run_seg(asr, segs[0][1], mode)
        walls: list[float] = []
        models: list[float] = []
        texts: list[str] = []
        for name, audio in segs:
            w_rep: list[float] = []
            m_rep: list[float] = []
            last_t = ""
            for _ in range(reps):
                text, wall, model = _run_seg(asr, audio, mode)
                w_rep.append(wall)
                m_rep.append(model)
                last_t = text
            walls.append(_median(w_rep))
            models.append(_median(m_rep))
            texts.append(last_t)
            print(
                f"  [{('par' if parallel else 'ser')} o={online_t} f={offline_t}] "
                f"{name}: wall_med={_median(w_rep):.0f} min={min(w_rep):.0f} "
                f"model={_median(m_rep):.0f} text={last_t!r}"
            )
        return {
            "online": online_t,
            "offline": offline_t,
            "parallel": parallel,
            "wall_med": _median(walls),
            "wall_min": min(walls) if walls else float("nan"),
            "model_med": _median(models),
            "texts": texts,
            "n": len(segs),
        }
    finally:
        asr.close()


def _probe_feed_stream(segs: list[tuple[str, object]], threads: list[int], block_ms: int = 100) -> None:
    """Speaking-path: feed_stream per 100ms chunk under different ONLINE threads."""
    print("\n## feed_stream per-chunk (speaking path)")
    print("| online_threads | chunks | median_ms | p90_ms | max_ms |")
    print("|---|---:|---:|---:|---:|")
    block = int(config.SAMPLE_RATE * block_ms / 1000)
    # use longest segment
    audio = max((a for _, a in segs), key=lambda x: x.size) if segs else None
    if audio is None:
        print("(no audio)")
        return
    for ot in threads:
        config.ASR_THREADS_ONLINE = int(ot)
        config.ASR_THREADS_OFFLINE = 2  # irrelevant for feed
        config.ASR_2PASS_PARALLEL = False
        config.ASR_FINAL_2PASS = False  # skip loading sensevoice for this probe? need online only
        # Still construct with 2pass off to isolate online
        config.ASR_FINAL_2PASS = False
        asr = StreamingAsr()
        try:
            st = AsrIncState()
            times: list[float] = []
            for i in range(0, audio.size, block):
                chunk = audio[i : i + block]
                if chunk.size == 0:
                    break
                t0 = time.perf_counter()
                asr.feed_stream(chunk, st)
                times.append((time.perf_counter() - t0) * 1000)
            if not times:
                continue
            s = sorted(times)
            p90 = s[int(0.9 * (len(s) - 1))]
            print(
                f"| {ot} | {len(times)} | {_median(times):.2f} | {p90:.2f} | {max(times):.2f} |"
            )
        finally:
            asr.close()
        config.ASR_FINAL_2PASS = True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", nargs="+", type=int, default=[6, 4, 3, 2])
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--parallel", choices=["on", "off", "both"], default="both")
    ap.add_argument(
        "--mode",
        choices=["finalize", "2pass", "both"],
        default="both",
        help="finalize_stream and/or transcribe_with_2pass",
    )
    ap.add_argument("--no-feed-probe", action="store_true")
    args = ap.parse_args()

    print(
        f"host cores: logical={__import__('os').cpu_count()} "
        f"(expect 8 phys / 16 logical on 4800H)"
    )
    segs = _load_segments()
    if not segs:
        print("no segments")
        return 1
    print(f"total segs={len(segs)} reps={args.reps}")

    parallel_modes: list[bool] = []
    if args.parallel in ("off", "both"):
        parallel_modes.append(False)
    if args.parallel in ("on", "both"):
        parallel_modes.append(True)

    run_modes: list[str] = []
    if args.mode in ("finalize", "both"):
        run_modes.append("finalize")
    if args.mode in ("2pass", "both"):
        run_modes.append("2pass")

    # serial baseline texts at 6/6 for equality check
    baseline_texts: dict[str, list[str]] = {}

    rows: list[dict] = []
    for mode in run_modes:
        print(f"\n===== mode={mode} =====")
        for par in parallel_modes:
            for t in args.threads:
                # when parallel: both sides use t; when serial: both use t (same as today)
                online_t = t
                offline_t = t
                print(f"\n-- threads={t} parallel={par} --")
                row = _bench_combo(segs, online_t, offline_t, par, args.reps, mode)
                row["decode_mode"] = mode
                key = mode
                if not par and t == args.threads[0]:
                    baseline_texts[key] = row["texts"]
                elif key in baseline_texts:
                    base = baseline_texts[key]
                    mism = [
                        (i, a, b)
                        for i, (a, b) in enumerate(zip(base, row["texts"]))
                        if a != b
                    ]
                    if mism:
                        print(f"TEXT MISMATCH vs serial@{args.threads[0]}: {len(mism)}")
                        for i, a, b in mism[:8]:
                            print(f"  seg{i}: {a!r} != {b!r}")
                    else:
                        print(f"text OK vs serial@{args.threads[0]} ({len(row['texts'])} segs)")
                rows.append(row)

        # extra: parallel with asymmetric split (online=t, offline=max(2,t-1)) for best candidates
        if True in parallel_modes:
            for ot, oft in ((3, 3), (4, 2), (3, 2), (2, 2), (4, 3)):
                if ot not in args.threads and oft not in args.threads:
                    pass
                print(f"\n-- asymmetric online={ot} offline={oft} parallel=True --")
                row = _bench_combo(segs, ot, oft, True, args.reps, mode)
                row["decode_mode"] = mode
                if mode in baseline_texts:
                    base = baseline_texts[mode]
                    mism = [
                        (i, a, b)
                        for i, (a, b) in enumerate(zip(base, row["texts"]))
                        if a != b
                    ]
                    if mism:
                        print(f"TEXT MISMATCH: {len(mism)}")
                        for i, a, b in mism[:8]:
                            print(f"  seg{i}: {a!r} != {b!r}")
                    else:
                        print("text OK")
                rows.append(row)

    print("\n## summary table")
    print("| decode | online | offline | parallel | wall_med_ms | wall_min_ms | model_med_ms |")
    print("|---|---:|---:|---|---:|---:|---:|")
    for r in rows:
        print(
            f"| {r['decode_mode']} | {r['online']} | {r['offline']} | "
            f"{'on' if r['parallel'] else 'off'} | "
            f"{r['wall_med']:.0f} | {r['wall_min']:.0f} | {r['model_med']:.0f} |"
        )

    # recommend
    for mode in run_modes:
        subset = [r for r in rows if r["decode_mode"] == mode]
        ser = [r for r in subset if not r["parallel"]]
        par = [r for r in subset if r["parallel"]]
        best_ser = min(ser, key=lambda r: r["wall_med"]) if ser else None
        best_par = min(par, key=lambda r: r["wall_med"]) if par else None
        print(f"\nbest serial ({mode}): {best_ser}")
        print(f"best parallel ({mode}): {best_par}")
        if best_ser and best_par:
            gain = best_ser["wall_med"] - best_par["wall_med"]
            print(f"parallel gain vs best serial wall_med: {gain:.0f}ms")

    if not args.no_feed_probe:
        _probe_feed_stream(segs, args.threads)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
