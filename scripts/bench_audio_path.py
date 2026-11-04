"""Profile mic callback path: interval jitter, queue lag, GIL contention.

Does not load ASR/MT. Optional CPU worker holds GIL to mimic inference.

Examples:
  python scripts/bench_audio_path.py --seconds 8
  python scripts/bench_audio_path.py --block-ms 20,40,100 --gil-worker
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402

LOG_JSONL = ROOT / "docs" / "bench_history.jsonl"
LOG_MD = ROOT / "docs" / "OPTIMIZATION_LOG.md"


@dataclass
class BlockResult:
    block_ms: int
    seconds: float
    gil_worker: bool
    sample_rate: int
    n_callbacks: int
    n_consumed: int
    n_dropped: int
    n_status: int
    interval_ms_median: float | None
    interval_ms_p95: float | None
    interval_ms_p99: float | None
    interval_ms_max: float | None
    lag_ms_median: float | None
    lag_ms_p95: float | None
    lag_ms_p99: float | None
    lag_ms_max: float | None
    late_callbacks_pct: float  # interval > 1.5x nominal
    notes: list[str] = field(default_factory=list)


def _pct(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = min(len(sorted_vals) - 1, max(0, int(round((p / 100.0) * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def _gil_burn(stop: threading.Event) -> None:
    """Hold GIL with pure-Python work (numpy matmul releases GIL — not a fair stress)."""
    while not stop.is_set():
        s = 0
        for i in range(50_000):
            s += (i * i) ^ (i << 1)
        if s == -1:
            break


def run_one(block_ms: int, seconds: float, gil_worker: bool, device: int | None) -> BlockResult:
    sr = int(config.SAMPLE_RATE)
    block = max(1, int(sr * block_ms / 1000))
    nominal = block / float(sr) * 1000.0

    from queue import Empty, Full, Queue

    q: Queue = Queue(maxsize=100)
    cb_times: list[float] = []
    status_hits = 0
    dropped = 0
    lock = threading.Lock()

    def callback(indata, frames, time_info, status):  # noqa: ANN001
        nonlocal status_hits, dropped
        t = time.perf_counter()
        if status:
            with lock:
                status_hits += 1
        mono = np.asarray(indata[:, 0], dtype=np.float32).copy()
        try:
            q.put_nowait((t, mono))
        except Full:
            try:
                q.get_nowait()
            except Empty:
                pass
            try:
                q.put_nowait((t, mono))
            except Full:
                with lock:
                    dropped += 1
        with lock:
            cb_times.append(t)

    stop_gil = threading.Event()
    gil_thread = None
    if gil_worker:
        gil_thread = threading.Thread(target=_gil_burn, args=(stop_gil,), daemon=True)
        gil_thread.start()

    lags: list[float] = []
    n_consumed = 0
    stream = sd.InputStream(
        samplerate=sr,
        channels=1,
        dtype="float32",
        blocksize=block,
        device=device,
        callback=callback,
    )
    stream.start()
    t_end = time.perf_counter() + seconds
    try:
        while time.perf_counter() < t_end:
            try:
                t_cb, _pcm = q.get(timeout=0.2)
            except Empty:
                continue
            lag_ms = (time.perf_counter() - t_cb) * 1000.0
            lags.append(lag_ms)
            n_consumed += 1
            # light consumer work (like feeding VAD energy)
            _ = float(np.sqrt(np.mean(_pcm.astype(np.float64) ** 2)))
    finally:
        stream.stop()
        stream.close()
        stop_gil.set()
        if gil_thread:
            gil_thread.join(timeout=1)

    with lock:
        times = list(cb_times)
        n_status = status_hits
        n_dropped = dropped

    intervals: list[float] = []
    for i in range(1, len(times)):
        intervals.append((times[i] - times[i - 1]) * 1000.0)

    intervals_s = sorted(intervals)
    lags_s = sorted(lags)
    late = 0
    if intervals:
        late = sum(1 for x in intervals if x > nominal * 1.5)
    late_pct = (100.0 * late / len(intervals)) if intervals else 0.0

    notes: list[str] = []
    if n_status:
        notes.append(f"sounddevice status flags={n_status}")
    if n_dropped:
        notes.append(f"queue drops={n_dropped}")
    if late_pct >= 5:
        notes.append(f"late_callbacks>={late_pct:.1f}%")

    return BlockResult(
        block_ms=block_ms,
        seconds=seconds,
        gil_worker=gil_worker,
        sample_rate=sr,
        n_callbacks=len(times),
        n_consumed=n_consumed,
        n_dropped=n_dropped,
        n_status=n_status,
        interval_ms_median=statistics.median(intervals) if intervals else None,
        interval_ms_p95=_pct(intervals_s, 95),
        interval_ms_p99=_pct(intervals_s, 99),
        interval_ms_max=max(intervals) if intervals else None,
        lag_ms_median=statistics.median(lags) if lags else None,
        lag_ms_p95=_pct(lags_s, 95),
        lag_ms_p99=_pct(lags_s, 99),
        lag_ms_max=max(lags) if lags else None,
        late_callbacks_pct=round(late_pct, 2),
        notes=notes,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=6.0)
    parser.add_argument("--block-ms", default="100,40,20", help="comma list")
    parser.add_argument("--gil-worker", action="store_true", help="also run with GIL burner")
    parser.add_argument("--device", type=int, default=None)
    parser.add_argument("--tag", default="audio-path")
    args = parser.parse_args()

    blocks = [int(x.strip()) for x in args.block_ms.split(",") if x.strip()]
    modes = [False]
    if args.gil_worker:
        modes.append(True)

    print("input devices (first 8):")
    try:
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0 and i < 16:
                print(f"  [{i}] {d['name']}")
    except Exception as exc:  # noqa: BLE001
        print(f"  (query failed: {exc})")

    rows: list[dict] = []
    for gil in modes:
        for b in blocks:
            label = f"block={b}ms gil_worker={gil}"
            print(f"\n=== {label} ({args.seconds}s) ===")
            try:
                r = run_one(b, args.seconds, gil, args.device)
            except Exception as exc:  # noqa: BLE001
                print(f"FAILED: {exc}")
                rows.append(
                    {
                        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "tag": f"{args.tag}-fail",
                        "note": str(exc),
                        "mode": "audio_path",
                        "block_ms": b,
                        "gil_worker": gil,
                    }
                )
                continue
            print(
                f"cb={r.n_callbacks} consumed={r.n_consumed} drop={r.n_dropped} status={r.n_status}\n"
                f"  interval ms med/p95/p99/max = "
                f"{r.interval_ms_median}/{r.interval_ms_p95}/{r.interval_ms_p99}/{r.interval_ms_max}\n"
                f"  queue lag ms med/p95/p99/max = "
                f"{r.lag_ms_median}/{r.lag_ms_p95}/{r.lag_ms_p99}/{r.lag_ms_max}\n"
                f"  late_callbacks = {r.late_callbacks_pct}%  notes={r.notes}"
            )
            row = {
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "tag": args.tag,
                "note": label,
                "mode": "audio_path",
                **asdict(r),
            }
            rows.append(row)

    LOG_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with LOG_JSONL.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    if LOG_MD.exists() and rows:
        lines = []
        for row in rows:
            if "interval_ms_median" not in row:
                continue
            lines.append(
                f"| {row['time']} | {row['tag']} | block={row['block_ms']} gil={row['gil_worker']} "
                f"int_p99={row.get('interval_ms_p99')} lag_p99={row.get('lag_ms_p99')} "
                f"late={row.get('late_callbacks_pct')}% | audio path profile |"
            )
        if lines:
            text = LOG_MD.read_text(encoding="utf-8")
            LOG_MD.write_text(text.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")

    print("\n" + json.dumps(rows, ensure_ascii=False, indent=2))
    print("AUDIO_PATH_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
