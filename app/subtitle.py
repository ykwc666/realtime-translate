"""双语字幕状态契约 + 原子写盘（旁路；不得拖慢 feed_chunk）。

契约（data/live/subtitle.json，整文件替换）：
  ts           epoch 秒
  state        idle | speaking | final
  partial_zh   当前 partial 中文（无则 ""）
  last         {zh, en, t} 最近定稿（无则 null）
  history      [{zh, en, t}, ...] 最近若干条，新的在前
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
# 含 last 共 6 条：页面跳过 last.t 后历史最多仍显示 5 条
_HISTORY_MAX = 6
_IDLE_GAP_SEC = 3.0
_QUEUE_MAX = 8


@dataclass
class SubtitleState:
    ts: float
    state: str  # idle | speaking | final
    partial_zh: str = ""
    last: dict[str, Any] | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def render(state: dict[str, Any] | None) -> dict[str, Any]:
    """页面渲染纯函数：当前行 + 历史（跳过与 last 同 t 的条目，避免双显）。"""
    st = state or {}
    kind = str(st.get("state") or "idle")
    partial = str(st.get("partial_zh") or "")
    last = st.get("last")
    history = st.get("history") if isinstance(st.get("history"), list) else []

    cur_zh, cur_en = "", ""
    if kind == "speaking":
        cur_zh, cur_en = partial, ""
    elif kind == "final" and isinstance(last, dict):
        cur_zh = str(last.get("zh") or "")
        cur_en = str(last.get("en") or "")
    # idle → 当前行空

    last_t = last.get("t") if isinstance(last, dict) else None
    hist_rows: list[dict[str, Any]] = []
    for h in history:
        if not isinstance(h, dict):
            continue
        if last_t is not None and h.get("t") == last_t:
            continue
        hist_rows.append(h)

    return {"curZh": cur_zh, "curEn": cur_en, "histRows": hist_rows}


class SubtitleWriter:
    """队列 + daemon 写线程；热路径仅 put_nowait。"""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_SUBTITLE_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._q: queue.Queue[tuple | None] = queue.Queue(maxsize=_QUEUE_MAX)
        self._last: dict[str, Any] | None = None
        self._history: list[dict[str, Any]] = []
        self._t_seq = 0  # 保证 final.t 唯一（页面按 t 跳过 last）
        self._thread = threading.Thread(
            target=self._run, name="subtitle-writer", daemon=True
        )
        self._thread.start()
        self.idle()
        print(f"[subtitle] 写盘 → {self.path.resolve()}")

    def _offer(self, item: tuple | None) -> None:
        """有界队列：满则丢最旧再塞。"""
        while True:
            try:
                self._q.put_nowait(item)
                return
            except queue.Full:
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    return

    def speaking(self, partial_zh: str) -> None:
        self._offer(("speaking", str(partial_zh or ""), time.time()))

    def final(self, zh: str, en: str) -> None:
        self._offer(("final", str(zh or ""), str(en or ""), time.time()))

    def idle(self) -> None:
        self._offer(("idle", time.time()))

    def close(self) -> None:
        # 先 idle 落盘，再毒丸结束写线程
        self.idle()
        self._offer(None)
        self._thread.join(timeout=5.0)

    def _emit_idle(self, ts: float) -> None:
        st = SubtitleState(
            ts=ts,
            state="idle",
            partial_zh="",
            last=self._last,
            history=list(self._history),
        )
        self._atomic_write(st.to_dict())

    def _run(self) -> None:
        idle_deadline: float | None = None
        while True:
            timeout: float | None = None
            if idle_deadline is not None:
                timeout = max(0.0, idle_deadline - time.time())
            try:
                if timeout is None:
                    item = self._q.get()
                else:
                    item = self._q.get(timeout=timeout)
            except queue.Empty:
                # 定稿后静置到期 → idle（句子留在 history）
                self._emit_idle(time.time())
                idle_deadline = None
                continue

            if item is None:
                break

            kind = item[0]
            if kind == "idle":
                idle_deadline = None
                self._emit_idle(float(item[1]))
            elif kind == "speaking":
                idle_deadline = None
                st = SubtitleState(
                    ts=float(item[2]),
                    state="speaking",
                    partial_zh=item[1],
                    last=self._last,
                    history=list(self._history),
                )
                self._atomic_write(st.to_dict())
            elif kind == "final":
                _zh, _en, ts = item[1], item[2], float(item[3])
                # Windows time.time() 分辨率粗：连续 final 可能同戳 → 按 t 去重会误伤
                self._t_seq += 1
                ts = ts + self._t_seq * 1e-6
                entry = {"zh": _zh, "en": _en, "t": ts}
                self._last = entry
                # 不去重短句/噪声句：一律进 history（含 The。→缩）
                self._history = [entry, *self._history][:_HISTORY_MAX]
                st = SubtitleState(
                    ts=ts,
                    state="final",
                    partial_zh="",
                    last=self._last,
                    history=list(self._history),
                )
                self._atomic_write(st.to_dict())
                idle_deadline = time.time() + _IDLE_GAP_SEC
            else:
                continue

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
