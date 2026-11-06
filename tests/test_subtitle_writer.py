"""字幕写盘：契约字段 + 原子替换。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import app.subtitle as subtitle_mod
from app.subtitle import SubtitleWriter


def _wait_state(path: Path, pred, timeout: float = 2.0) -> dict:
    deadline = time.time() + timeout
    data: dict = {}
    while time.time() < deadline:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if pred(data):
                return data
        time.sleep(0.02)
    raise AssertionError(f"state not reached: last={data!r}")


def test_subtitle_writer_speaking_then_final(tmp_path: Path) -> None:
    path = tmp_path / "subtitle.json"
    w = SubtitleWriter(path)
    try:
        w.speaking("今天天气")
        data = _wait_state(
            path,
            lambda d: d.get("state") == "speaking" and d.get("partial_zh") == "今天天气",
        )
        assert set(data) >= {"ts", "state", "partial_zh", "last", "history"}
        assert data["last"] is None
        assert data["history"] == []

        w.final("今天天气怎么样？", "How's the weather today?")
        data = _wait_state(path, lambda d: d.get("state") == "final" and d.get("last"))
        assert data["partial_zh"] == ""
        assert data["last"]["zh"] == "今天天气怎么样？"
        assert data["last"]["en"] == "How's the weather today?"
        assert len(data["history"]) == 1
        assert data["history"][0]["zh"] == data["last"]["zh"]
    finally:
        w.close()


def test_close_leaves_idle_on_disk(tmp_path: Path) -> None:
    path = tmp_path / "subtitle.json"
    w = SubtitleWriter(path)
    w.final("对。", "Yes.")
    _wait_state(path, lambda d: d.get("state") == "final")
    w.close()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["state"] == "idle"
    assert data["last"]["zh"] == "对。"
    assert data["partial_zh"] == ""


def test_idle_gap_after_final(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(subtitle_mod, "_IDLE_GAP_SEC", 0.25)
    path = tmp_path / "subtitle.json"
    w = SubtitleWriter(path)
    try:
        w.final("The。", "缩")
        _wait_state(path, lambda d: d.get("state") == "final")
        data = _wait_state(
            path, lambda d: d.get("state") == "idle", timeout=2.0
        )
        assert data["last"]["zh"] == "The。"
        assert any(h.get("zh") == "The。" for h in data["history"])
        assert data["partial_zh"] == ""
    finally:
        w.close()
