"""展示标点去重：只影响 display_src。"""
from __future__ import annotations

from app.dual_track import dedupe_display_punct


def test_dedupe_doubled_question_mark() -> None:
    punctuated = "今天天气怎么样？？现在是星期二。"
    out = dedupe_display_punct(punctuated)
    assert "？？" not in out
    assert out == "今天天气怎么样？现在是星期二。"


def test_single_question_unchanged() -> None:
    punctuated = "延迟大概有多少？"
    assert dedupe_display_punct(punctuated) == punctuated


def test_dedupe_comma_period_mix() -> None:
    assert "，。" not in dedupe_display_punct("然后，。发给产品")
    assert dedupe_display_punct("你好，，延迟") == "你好，延迟"
