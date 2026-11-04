"""Session harvest: side-channel utt logger (queue + writer thread).

Contract (one JSON object per finalized utterance, UTF-8 JSONL):
  t_wall                  ISO-ish local wall clock when the row was queued
  src                     final ASR text (display; may include punct if enabled)
  tgt                     text sent toward TTS (post TM/postedit)
  mt_raw                  CT2/transformers output before postedit; TM hit → same as tgt
  spec_hit                DualTrackStats.speculate_hit
  asr_wait_ms             t_asr_done_ms - t_cut_ms, or null
  latency_perceived_ms    DualTrackStats.latency_perceived_ms (mt_ready), or null
  latency_device_ttfa_ms  DualTrackStats.latency_device_ttfa_ms, or null if not yet known
  mode                    run mode string (loopback|vad|duplex|file|…)
  source_desc             free-text sound-source note from CLI

Hot path: put_nowait only. A daemon thread does open/write/flush so capture/finalize
never blocks on disk IO. close() drains the queue.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config


def next_harvest_path(root: Path | None = None) -> Path:
    base = Path(root) if root is not None else Path(config.ROOT) / "data" / "harvest"
    base.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y-%m-%d")
    n = 1
    while True:
        path = base / f"{day}-{n:03d}.jsonl"
        if not path.exists():
            return path
        n += 1


def row_from_stats(
    stats: Any,
    *,
    mode: str,
    source_desc: str,
    t_wall: str | None = None,
) -> dict:
    """Map DualTrackStats → harvest row. Missing timers → null (never invent)."""
    t_cut = getattr(stats, "t_cut_ms", None)
    t_asr = getattr(stats, "t_asr_done_ms", None)
    asr_wait = None
    if t_cut is not None and t_asr is not None:
        asr_wait = round(float(t_asr) - float(t_cut), 1)
    perc = getattr(stats, "latency_perceived_ms", None)
    dev = getattr(stats, "latency_device_ttfa_ms", None)
    mt_raw = getattr(stats, "mt_raw", None)
    tgt = getattr(stats, "translated", "") or ""
    if mt_raw is None:
        mt_raw = tgt
    return {
        "t_wall": t_wall or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "src": getattr(stats, "source", "") or "",
        "tgt": tgt,
        "mt_raw": mt_raw,
        "spec_hit": bool(getattr(stats, "speculate_hit", False)),
        "asr_wait_ms": asr_wait,
        "latency_perceived_ms": (
            None if perc is None else round(float(perc), 1)
        ),
        "latency_device_ttfa_ms": (
            None if dev is None else round(float(dev), 1)
        ),
        "mode": mode,
        "source_desc": source_desc,
    }


class HarvestWriter:
    """Async JSONL writer. emit() is safe from the audio/finalize thread."""

    def __init__(self, path: Path, *, mode: str, source_desc: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.source_desc = source_desc
        self._q: queue.Queue[dict | None] = queue.Queue()
        self._n = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="harvest-writer", daemon=True
        )
        self._thread.start()
        print(f"[harvest] 开始采集 → {self.path.resolve()}")
        print(f"[harvest] mode={mode!r} source={source_desc!r}")

    def emit_from_stats(self, stats: Any) -> None:
        row = row_from_stats(stats, mode=self.mode, source_desc=self.source_desc)
        self.emit(row)

    def emit(self, row: dict) -> None:
        try:
            self._q.put_nowait(row)
        except queue.Full:
            # unbounded Queue never Full; keep branch for safety
            print("[harvest] queue full, drop row")

    def _run(self) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            while True:
                item = self._q.get()
                if item is None:
                    break
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
                f.flush()
                with self._lock:
                    self._n += 1

    def close(self) -> None:
        self._q.put(None)
        self._thread.join(timeout=5.0)
        with self._lock:
            n = self._n
        print(f"[harvest] 结束，已写 {n} 行 → {self.path}")

    def attach_engine(self, engine: Any) -> None:
        """Side hook: DualTrackEngine.on_utt_final."""
        prev = getattr(engine, "on_utt_final", None)

        def _cb(stats: Any) -> None:
            self.emit_from_stats(stats)
            if callable(prev):
                prev(stats)

        engine.on_utt_final = _cb
