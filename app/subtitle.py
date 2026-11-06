"""双语字幕状态契约 + 原子写盘（旁路；不得拖慢 feed_chunk）。

契约（data/live/subtitle.json，整文件替换）：
  ts           epoch 秒
  state        idle | speaking | final
  partial_zh   当前 partial 中文（无则 ""）
  last         {zh, en, t} 最近定稿（无则 null）
  history      [{zh, en, t}, ...] 最近 5 条，新的在前
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import config

DEFAULT_SUBTITLE_PATH = Path(config.ROOT) / "data" / "live" / "subtitle.json"
_HISTORY_MAX = 5


@dataclass
class SubtitleState:
    ts: float
    state: str  # idle | speaking | final
    partial_zh: str = ""
    last: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SubtitleWriter:
    """队列 + daemon 写线程；热路径仅 put_nowait。"""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_SUBTITLE_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._q: queue.Queue[tuple | None] = queue.Queue()
        self._last: dict[str, Any] | None = None
        self._history: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="subtitle-writer", daemon=True
        )
        self._thread.start()
        self.idle()
        print(f"[subtitle] 写盘 → {self.path.resolve()}")

    def speaking(self, partial_zh: str) -> None:
        try:
            self._q.put_nowait(("speaking", str(partial_zh or ""), time.time()))
        except queue.Full:
            pass

    def final(self, zh: str, en: str) -> None:
        try:
            self._q.put_nowait(
                ("final", str(zh or ""), str(en or ""), time.time())
            )
        except queue.Full:
            pass

    def idle(self) -> None:
        try:
            self._q.put_nowait(("idle", time.time()))
        except queue.Full:
            pass

    def close(self) -> None:
        try:
            self._q.put_nowait(None)
        except queue.Full:
            self._q.put(None)
        self._thread.join(timeout=3.0)

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                break
            kind = item[0]
            if kind == "idle":
                st = SubtitleState(
                    ts=float(item[1]),
                    state="idle",
                    partial_zh="",
                    last=self._last,
                    history=list(self._history),
                )
            elif kind == "speaking":
                st = SubtitleState(
                    ts=float(item[2]),
                    state="speaking",
                    partial_zh=item[1],
                    last=self._last,
                    history=list(self._history),
                )
            elif kind == "final":
                _zh, _en, ts = item[1], item[2], float(item[3])
                entry = {"zh": _zh, "en": _en, "t": ts}
                self._last = entry
                self._history = [entry, *[h for h in self._history if h != entry]][
                    :_HISTORY_MAX
                ]
                st = SubtitleState(
                    ts=ts,
                    state="final",
                    partial_zh="",
                    last=self._last,
                    history=list(self._history),
                )
            else:
                continue
            self._atomic_write(st.to_dict())

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except OSError as exc:
            print(f"[subtitle] write failed: {exc}")
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
