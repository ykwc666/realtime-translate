"""has_repeated_span: adjacent-only + language-aware (ASR loop cut P0)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.mt_policy import has_repeated_span

ROOT = Path(__file__).resolve().parents[1]
DETAIL = ROOT / "docs" / "ledger_eval_detail.jsonl"


# --- English true positives ---
@pytest.mark.parametrize(
    "text",
    [
        "you read through it you read through it again",
        "Open the window. You read through it you read through it again",
        "hello world foo hello world foo bar",
    ],
)
def test_en_true_adjacent_repeat(text: str) -> None:
    assert has_repeated_span(text) is True


# --- English false positives that old fn misfired on ---
@pytest.mark.parametrize(
    "text",
    [
        "Open the window, You read through it. You might click on, you know, close it again straight away",
        "Yeah, just to give them an idea, general idea what there is and so they can pick a",
        "BUT THAT WILL MEAN THAT YOU CAN ONLY DO THINGS ON ACTUALLY",
    ],
)
def test_en_no_false_adjacent(text: str) -> None:
    assert has_repeated_span(text) is False


def test_ledger_refs_zero_false_positive() -> None:
    rows = [
        json.loads(l)
        for l in DETAIL.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    # Prefer AMI+FLEURS refs; if libri present still count all with ref
    bad = [r["id"] for r in rows if (r.get("ref") or "").strip() and has_repeated_span(r["ref"])]
    assert bad == [], f"false positives on ledger refs: {bad}"


# --- Chinese true positives (adjacent char spans) ---
@pytest.mark.parametrize(
    "text",
    [
        "时快时慢的感觉你时快时慢的感觉你",
        "感觉你翻译的有感觉你翻译的有",
        "延迟大概有多少延迟大概有多少",
        "今天天气怎么样今天天气怎么样",
        "现在是星期二现在是星期二",
    ],
)
def test_zh_true_adjacent_repeat(text: str) -> None:
    assert has_repeated_span(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "你好，延迟大概有多少？",
        "今天天气怎么样？",
        "现在是星期二。",
        "他现在没有回我的消息。",
        "延迟大概有多少？",
        "现在是星期啊。",
    ],
)
def test_zh_normal_phrases_no_fp(text: str) -> None:
    assert has_repeated_span(text) is False
