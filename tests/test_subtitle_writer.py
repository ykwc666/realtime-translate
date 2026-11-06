"""字幕写盘：契约字段 + 原子替换。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from app.subtitle import SubtitleWriter


def test_subtitle_writer_speaking_then_final(tmp_path: Path) -> None:
    path = tmp_path / "subtitle.json"
    w = SubtitleWriter(path)
    try:
        w.speaking("今天天气")
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("state") == "speaking" and data.get("partial_zh") == "今天天气":
                    break
            time.sleep(0.02)
        else:
            raise AssertionError("speaking state not written")
        assert set(data) >= {"ts", "state", "partial_zh", "last", "history"}
        assert data["last"] is None
        assert data["history"] == []

        w.final("今天天气怎么样？", "How's the weather today?")
        deadline = time.time() + 2.0
        while time.time() < deadline:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("state") == "final" and data.get("last"):
                break
            time.sleep(0.02)
        else:
            raise AssertionError("final state not written")
        assert data["partial_zh"] == ""
        assert data["last"]["zh"] == "今天天气怎么样？"
        assert data["last"]["en"] == "How's the weather today?"
        assert len(data["history"]) == 1
        assert data["history"][0]["zh"] == data["last"]["zh"]
    finally:
        w.close()
