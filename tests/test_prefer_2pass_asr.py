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
    assert prefer_2pass_asr(stream, offline) == expect
