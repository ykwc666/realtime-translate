"""HarvestWriter 落盘契约：行数守恒、延迟 TTFA 回填、空 src 不写。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.harvest import HarvestWriter


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


def test_harvest_row_count_conserved(tmp_path):
    path = tmp_path / "h.jsonl"
    w = HarvestWriter(path, mode="vad", source_desc="t")
    srcs = [f"src-{i}" for i in range(94)]
    for s in srcs:
        w.emit_from_stats(_stats(source=s))
    w.close()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 94
    got = [json.loads(x)["src"] for x in lines]
    assert got == srcs
    assert len(set(got)) == 94


def test_harvest_late_ttfa_backfill(tmp_path):
    path = tmp_path / "h.jsonl"
    w = HarvestWriter(path, mode="vad", source_desc="t")
    first = _stats(source="第一句", latency_device_ttfa_ms=None)
    w.emit_from_stats(first)
    first.latency_device_ttfa_ms = 673.0
    w.close()
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["src"] == "第一句"
    assert rows[0]["latency_device_ttfa_ms"] == 673.0


def test_harvest_blank_src_skipped(tmp_path):
    path = tmp_path / "h.jsonl"
    w = HarvestWriter(path, mode="vad", source_desc="t")
    w.emit_from_stats(SimpleNamespace(source="   "))
    w.close()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    assert text.strip() == ""
    assert len([ln for ln in text.splitlines() if ln.strip()]) == 0
