"""Parameterized coverage for prefer_2pass_asr (P2-9)."""
from __future__ import annotations

import pytest

from app.asr_stream import prefer_2pass_asr


@pytest.mark.parametrize(
    ("stream", "offline", "expect"),
    [
        ("", "今天天气怎么样", "今天天气怎么样"),
        ("今天天气怎么样", "", "今天天气怎么样"),
        ("延迟大概有多", "延迟大概有多少", "延迟大概有多少"),
        ("延迟大概有多少", "延迟大概有多", "延迟大概有多少"),
        ("星期", "星期三", "星期三"),
        ("现在是星期二", "现在是星期二。", "现在是星期二"),
        ("你好", "你好啊", "你好啊"),  # offline extends
        ("欺负", "十六", "欺负"),  # conflict, short — keep stream
        ("他现在没有回我的消息", "他现在没有回我的消息", "他现在没有回我的消息"),
        ("今天", "今天天气怎么样", "今天天气怎么样"),  # offline longer +2
    ],
)
def test_prefer_2pass_cases(stream: str, offline: str, expect: str) -> None:
    assert prefer_2pass_asr(stream, offline, mode="legacy") == expect


def test_prefer_2pass_offline_mode() -> None:
    assert prefer_2pass_asr("欺负", "十六", mode="offline") == "十六"
    # Weekday protect: complete online beats offline hallucination
    assert prefer_2pass_asr("现在是星期三", "现在是星期啊", mode="offline") == "现在是星期三"
    # Incomplete online still yields to offline completion
    assert prefer_2pass_asr("现在是星期", "现在是星期二", mode="offline") == "现在是星期二"


def test_prefer_2pass_offline_conflict() -> None:
    # Weekday protect also applies in offline_conflict
    assert (
        prefer_2pass_asr("现在是星期三", "现在是星期啊", mode="offline_conflict")
        == "现在是星期三"
    )
    # Clear conflict, short stream kept by legacy → offline_conflict flips
    assert prefer_2pass_asr("欺负", "十六", mode="offline_conflict") == "十六"
    # Extension still prefers longer offline
    assert prefer_2pass_asr("延迟大概有多", "延迟大概有多少", mode="offline_conflict") == (
        "延迟大概有多少"
    )


def test_prefer_2pass_weekday_protect_legacy() -> None:
    assert prefer_2pass_asr("现在是星期三", "现在是星期啊", mode="legacy") == "现在是星期三"
    assert prefer_2pass_asr("星期二", "星期啊", mode="legacy") == "星期二"
