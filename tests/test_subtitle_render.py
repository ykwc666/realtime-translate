"""字幕页 render：当前行与历史不重复 last。"""
from __future__ import annotations

from app.subtitle import render


def test_render_hist_skips_same_t_as_last() -> None:
    last = {"zh": "今天天气怎么样？现在是星期二。", "en": "How's the weather today?", "t": 100.0}
    older = {"zh": "你好。", "en": "Hello.", "t": 90.0}
    st = {
        "state": "final",
        "partial_zh": "",
        "last": last,
        "history": [last, older],
    }
    view = render(st)
    assert view["curZh"] == last["zh"]
    assert view["curEn"] == last["en"]
    assert view["histRows"] == [older]
    assert all(h.get("t") != last["t"] for h in view["histRows"])


def test_render_idle_clears_current() -> None:
    last = {"zh": "对。", "en": "Yes.", "t": 1.0}
    view = render(
        {
            "state": "idle",
            "partial_zh": "残影",
            "last": last,
            "history": [last],
        }
    )
    assert view["curZh"] == ""
    assert view["curEn"] == ""
    assert view["histRows"] == []


def test_render_speaking_uses_partial() -> None:
    view = render(
        {
            "state": "speaking",
            "partial_zh": "今天天气",
            "last": None,
            "history": [],
        }
    )
    assert view["curZh"] == "今天天气"
    assert view["curEn"] == ""
    assert view["histRows"] == []
