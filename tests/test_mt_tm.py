"""Exact TM lookup regression."""
from __future__ import annotations

from app.mt_tm import lookup_tm, normalize_tm_key, reload_tm, tm_size


def setup_function() -> None:
    reload_tm()


def test_normalize_strips_punct_and_case():
    assert normalize_tm_key("Hello!", "en") == "hello"
    assert normalize_tm_key("你好。", "zh") == "你好"
    assert normalize_tm_key("  How's the weather? ", "en") == "howstheweather"


def test_zh_en_hits():
    assert lookup_tm("你好", "zh") == "Hello."
    assert lookup_tm("你好！", "zh") == "Hello."
    assert lookup_tm("延迟大概有多少", "zh") == "About how much delay is there?"
    assert lookup_tm("我可能过不了这一面", "zh") == (
        "I might not pass this interview round."
    )


def test_en_zh_hits():
    assert lookup_tm("Hello", "en") == "你好。"
    assert lookup_tm("hello!", "en") == "你好。"
    assert lookup_tm("How much delay is there today?", "en") == (
        "今天延迟大概有多少？"
    )
    assert lookup_tm("Did you reply to my message?", "en") == "你回我消息了吗？"


def test_miss_falls_through():
    assert lookup_tm("这是一句完全不在词表里的话", "zh") is None
    assert lookup_tm("This sentence is definitely not in the TM.", "en") is None


def test_tm_has_entries():
    n_zh, n_en = tm_size()
    assert n_zh >= 20
    assert n_en >= 20
