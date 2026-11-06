"""SessionRecorder live.jsonl 落盘契约：行数与尾行 TTFA 回填。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.session_record import SessionRecorder


def _stats(**kwargs):
    base = dict(
        source="你好",
        translated="Hello",
        mt_raw="Hello",
        sub_lines=None,
        speculate_hit=False,
        t_cut_ms=100.0,
        t_asr_done_ms=200.0,
        latency_perceived_ms=300.0,
        latency_device_ttfa_ms=400.0,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_live_row_count_and_src_order(tmp_path):
    rec = SessionRecorder(session_dir=tmp_path / "s1", mode="vad")
    srcs = ["甲", "乙", "丙"]
    for s in srcs:
        rec.emit_live(_stats(source=s))
    rec.close()
    live = tmp_path / "s1" / "live.jsonl"
    rows = [json.loads(x) for x in live.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3
    assert [r["src"] for r in rows] == srcs


def test_live_tail_ttfa_backfill(tmp_path):
    rec = SessionRecorder(session_dir=tmp_path / "s1", mode="vad")
    a = _stats(source="先", latency_device_ttfa_ms=111.0)
    b = _stats(source="后", latency_device_ttfa_ms=None)
    rec.emit_live(a)
    rec.emit_live(b)
    b.latency_device_ttfa_ms = 888.0
    rec.close()
    rows = [
        json.loads(x)
        for x in (tmp_path / "s1" / "live.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 2
    assert rows[-1]["src"] == "后"
    assert rows[-1]["latency_device_ttfa_ms"] == 888.0
