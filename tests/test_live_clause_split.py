"""A0 切分触发：只看 ASR 原生标点；'.' 数字/缩写保护。"""
from __future__ import annotations

from types import MethodType

from app.dual_track import DualTrackEngine
from app.retranscribe import (
    has_splitworthy_mid_end,
    split_sentences,
)


def _engine() -> DualTrackEngine:
    e = DualTrackEngine.__new__(DualTrackEngine)
    e._final_mt_clauses = MethodType(DualTrackEngine._final_mt_clauses, e)
    return e


def test_ami008_unpunctuated_english_no_split() -> None:
    """ami_008 回归：无标点英文不得因展示标点被拆。"""
    src = (
        "Read through it you might click on you know "
        "close it again straight away"
    )
    assert has_splitworthy_mid_end(src) is False
    assert _engine()._final_mt_clauses(src) == [src]


def test_weather_tuesday_mid_q_splits() -> None:
    src = "今天天气怎么样？现在是星期二。"
    assert has_splitworthy_mid_end(src) is True
    parts = _engine()._final_mt_clauses(src)
    assert len(parts) == 2
    assert "天气" in parts[0]
    assert "星期二" in parts[1]


def test_single_zh_sentence_no_split() -> None:
    src = "现在的星期二。"
    assert has_splitworthy_mid_end(src) is False
    assert _engine()._final_mt_clauses(src) == [src]


def test_decimal_not_split() -> None:
    src = "大概10.5秒"
    assert has_splitworthy_mid_end(src) is False
    assert split_sentences(src) == [src]
    assert _engine()._final_mt_clauses(src) == [src]


def test_abbrev_am_mr_not_split() -> None:
    assert split_sentences("a.m.") == ["a.m."]
    assert has_splitworthy_mid_end("a.m.") is False
    assert split_sentences("Mr. Smith") == ["Mr. Smith"]
    assert has_splitworthy_mid_end("Mr. Smith") is False
    assert _engine()._final_mt_clauses("Mr. Smith") == ["Mr. Smith"]


def test_true_english_period_mid_splits() -> None:
    src = "You already started on doing stuff. I guess we can try again."
    assert has_splitworthy_mid_end(src) is True
    parts = split_sentences(src)
    assert len(parts) >= 2
