"""实麦自检埋点：只记录，不改切分/阈值。独立于 harvest 契约。"""
from __future__ import annotations

import json
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config


def next_probe_paths(root: Path | None = None) -> tuple[Path, Path]:
    """返回 (jsonl, summary.json)，目录 data/probe/<YYYY-MM-DD-HHMM>/。"""
    base = Path(root) if root is not None else Path(config.ROOT) / "data" / "probe"
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    day_dir = base / stamp
    n = 0
    while day_dir.exists():
        n += 1
        day_dir = base / f"{stamp}_{n}"
    day_dir.mkdir(parents=True, exist_ok=True)
    return day_dir / f"{day_dir.name}.jsonl", day_dir / "summary.json"


def _pct(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    xs = sorted(float(v) for v in vals)
    if len(xs) == 1:
        return xs[0]
    idx = (p / 100.0) * (len(xs) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(xs) - 1)
    frac = idx - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def _fmt(v: float | None, digits: int = 3) -> str:
    if v is None:
        return "n/a"
    return f"{v:.{digits}f}"


class MicProbe:
    """会话级实麦取证：逐句 JSONL + 退出汇总。"""

    def __init__(self, jsonl_path: Path | None = None, summary_path: Path | None = None) -> None:
        if jsonl_path is None or summary_path is None:
            jsonl_path, summary_path = next_probe_paths()
        self.jsonl_path = Path(jsonl_path)
        self.summary_path = Path(summary_path)
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._rows: list[dict[str, Any]] = []
        self._utt_idx = 0
        self.empty_abort = 0
        self.emit_model = 0
        self.emit_silence_forced = 0
        self.emit_flush = 0
        self._engines: list[Any] = []
        print(f"[probe] 实麦自检 → {self.jsonl_path.parent.resolve()}")

    def attach_engine(self, engine: Any) -> None:
        self._engines.append(engine)
        engine._mic_probe = self  # noqa: SLF001 — 旁路钩子
        prev = getattr(engine, "on_utt_final", None)

        def _cb(stats: Any) -> None:
            self.record_from_stats(stats, engine=engine)
            if callable(prev):
                prev(stats)

        engine.on_utt_final = _cb

    def note_empty_abort(self, *, audio_sec: float, open_peak: float | None,
                         open_rms: float | None, open_prob: float | None,
                         emit_path: str | None) -> None:
        with self._lock:
            self.empty_abort += 1
            self._utt_idx += 1
            row = {
                "utt_idx": self._utt_idx,
                "audio_sec": round(float(audio_sec), 3),
                "open_peak": open_peak,
                "open_rms": open_rms,
                "open_prob": open_prob,
                "emit_path": emit_path,
                "cut_reason": "empty_abort",
                "trailing_silence_sec": 0.0,
                "n_partials": 0,
                "src": "",
                "tgt": "",
            }
            self._note_emit_path(emit_path)
            self._rows.append(row)
            self._append_jsonl(row)

    def record_from_stats(self, stats: Any, *, engine: Any = None) -> None:
        open_peak = getattr(stats, "probe_open_peak", None)
        open_rms = getattr(stats, "probe_open_rms", None)
        open_prob = getattr(stats, "probe_open_prob", None)
        emit_path = getattr(stats, "probe_emit_path", None)
        cut_reason = getattr(stats, "probe_cut_reason", None) or "unknown"
        trailing = getattr(stats, "probe_trailing_silence_sec", None)
        if trailing is None:
            trailing = 0.0
        audio_sec = getattr(stats, "audio_sec_final", None)
        if audio_sec is None:
            audio_sec = 0.0
        with self._lock:
            self._utt_idx += 1
            row = {
                "utt_idx": self._utt_idx,
                "audio_sec": round(float(audio_sec), 3),
                "open_peak": None if open_peak is None else round(float(open_peak), 6),
                "open_rms": None if open_rms is None else round(float(open_rms), 6),
                "open_prob": open_prob,
                "emit_path": emit_path,
                "cut_reason": cut_reason,
                "trailing_silence_sec": round(float(trailing), 4),
                "n_partials": int(getattr(stats, "partial_count", 0) or 0),
                "src": getattr(stats, "source", "") or "",
                "tgt": getattr(stats, "translated", "") or "",
            }
            self._note_emit_path(emit_path)
            self._rows.append(row)
            self._append_jsonl(row)

    def _note_emit_path(self, emit_path: str | None) -> None:
        if emit_path == "model_endpoint":
            self.emit_model += 1
        elif emit_path == "silence_forced":
            self.emit_silence_forced += 1
        elif emit_path == "flush":
            self.emit_flush += 1

    def _append_jsonl(self, row: dict) -> None:
        with self.jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _pull_endpoint_silence_forced(self) -> int:
        """VAD 侧累计的 silence_forced emit（含被仲裁吞掉的）。"""
        total = 0
        for eng in self._engines:
            ep = getattr(eng, "endpoint", None)
            if ep is None:
                continue
            total += int(getattr(ep, "emit_silence_forced_count", 0) or 0)
        return total

    def build_summary(self) -> dict[str, Any]:
        with self._lock:
            rows = list(self._rows)
            empty_abort = self.empty_abort
            emit_model = self.emit_model
            emit_flush = self.emit_flush
        # 会话级死分支证据：优先 VAD 计数器（含未 finalize 的 emit）
        ep_forced = self._pull_endpoint_silence_forced()
        emit_silence_forced = max(self.emit_silence_forced, ep_forced)
        peaks = [float(r["open_peak"]) for r in rows if r.get("open_peak") is not None]
        rmss = [float(r["open_rms"]) for r in rows if r.get("open_rms") is not None]
        reasons = Counter(str(r.get("cut_reason") or "unknown") for r in rows)
        trails = [float(r.get("trailing_silence_sec") or 0.0) for r in rows]
        trailing_all_zero = bool(rows) and all(t == 0.0 for t in trails)
        if not rows:
            trailing_all_zero = True  # 无句时视为「全为 0」证据空集
        return {
            "utts": len(rows),
            "empty_abort": empty_abort,
            "emit_model": emit_model,
            "emit_silence_forced": emit_silence_forced,
            "emit_silence_forced_endpoint": ep_forced,
            "flush": emit_flush,
            "open_peak": {
                "p10": _pct(peaks, 10),
                "p50": _pct(peaks, 50),
                "p90": _pct(peaks, 90),
            },
            "open_rms": {
                "p10": _pct(rmss, 10),
                "p50": _pct(rmss, 50),
                "p90": _pct(rmss, 90),
            },
            "cut_reason": dict(reasons),
            "trailing_silence_always_zero": trailing_all_zero,
            "rows": rows,
        }

    def format_summary(self, summary: dict[str, Any] | None = None) -> str:
        s = summary or self.build_summary()
        pk = s["open_peak"]
        rm = s["open_rms"]
        reasons = s.get("cut_reason") or {}
        reason_s = "  ".join(f"{k}={v}" for k, v in sorted(reasons.items())) or "(none)"
        lines = [
            "=== 实麦自检 ===",
            (
                f"utts={s['utts']}  empty_abort={s['empty_abort']}  "
                f"emit_model={s['emit_model']}  "
                f"emit_silence_forced={s['emit_silence_forced']}  "
                f"flush={s['flush']}"
            ),
            (
                f"open_peak  p10={_fmt(pk.get('p10'))} "
                f"p50={_fmt(pk.get('p50'))} p90={_fmt(pk.get('p90'))}"
            ),
            (
                f"open_rms   p10={_fmt(rm.get('p10'))} "
                f"p50={_fmt(rm.get('p50'))} p90={_fmt(rm.get('p90'))}"
            ),
            f"cut_reason {reason_s}",
            f"trailing_silence 全为 0: {'yes' if s['trailing_silence_always_zero'] else 'no'}",
            "--- 逐句 ---",
        ]
        for r in s.get("rows") or []:
            lines.append(
                f"#{r['utt_idx']} {r['audio_sec']}s "
                f"peak={_fmt(r.get('open_peak'), 3)} "
                f"rms={_fmt(r.get('open_rms'), 3)} "
                f"path={r.get('emit_path') or '-'} "
                f"reason={r.get('cut_reason') or '-'} "
                f"src={r.get('src')!r} tgt={r.get('tgt')!r}"
            )
        return "\n".join(lines)

    def close(self) -> None:
        summary = self.build_summary()
        # 落盘时去掉逐句副本里的重复大字段以外的 rows 仍保留（summary 需要）
        out = {k: v for k, v in summary.items()}
        with self.summary_path.open("w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(self.format_summary(summary))
        print(f"[probe] 已写 {self.jsonl_path} + {self.summary_path}")
