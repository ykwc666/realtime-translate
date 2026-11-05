"""postedit 整句锚定：禁止子串 canned 删掉复合句后半段。"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.mt_postedit import _token_count, postedit_en_zh, postedit_zh_en

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "src,hyp,expect_canned",
    [
        ("他现在没有回我的消息。", "He has not replied to my message.", True),
        (
            "现在是星期二，他现在没有回我的消息。",
            "It is Tuesday and he has not replied to my message now.",
            False,
        ),
        ("我觉得听不清。", "Something wrong audio.", True),
        (
            "我觉得听不清，你能再说一遍吗？",
            "I feel I cannot hear clearly, can you say that again?",
            False,
        ),
        ("你能再说一遍吗", "Can you say that again?", False),
    ],
)
def test_zh_en_anchor_table(src: str, hyp: str, expect_canned: bool) -> None:
    out = postedit_zh_en(src, hyp)
    if expect_canned:
        assert out != hyp
        ol = out.lower()
        assert ("repli" in ol) or ("hear" in ol) or ("clearly" in ol)
    else:
        assert out == hyp


def test_zh_en_weather_compound_keeps_hyp() -> None:
    src = "今天天气怎么样？我下午想出去走走。"
    hyp = "How is the weather today? I want to go for a walk this afternoon."
    # 无 ， 但有两句；天气规则 fullmatch 不得吃掉后半
    assert postedit_zh_en(src, hyp) == hyp


def test_en_zh_reply_fullmatch_not_substring() -> None:
    hyp = "他周二就回了我消息，还说了别的。"
    # 子串含 reply to my message，但整句更长 → 不得整句替换成短 canned
    src = "On Tuesday he replied to my message and said something else."
    out = postedit_en_zh(src, hyp)
    assert out == hyp


def test_short_canned_beats_verbose_hyp() -> None:
    """长度闸已删：fullmatch 短句必须吃 canned，即使 hyp 更长。"""
    hyp = (
        "I am afraid that I am not able to hear what you are saying "
        "clearly enough right now"
    )
    assert postedit_zh_en("听不清", hyp) == "I can't hear clearly."


def test_clause_gate_still_blocks_compound_reply() -> None:
    src = "现在是星期二，他现在没有回我的消息。"
    hyp = "It is Tuesday and he has not replied to my message now."
    assert postedit_zh_en(src, hyp) == hyp


def test_clause_compound_hear_keeps_hyp() -> None:
    src = "我觉得听不清，另外会议室空调太吵了。"
    hyp = "I can't hear clearly, and the AC in the meeting room is too loud."
    assert postedit_zh_en(src, hyp) == hyp


def test_invariant_ledger_srcs_not_halved() -> None:
    manifest = ROOT / "data" / "ledger_corpus" / "manifest.jsonl"
    if not manifest.is_file():
        pytest.skip("no ledger corpus")
    rows = [
        json.loads(l)
        for l in manifest.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    rows = [r for r in rows if r.get("corpus") in ("ami", "fleurs")]
    assert len(rows) >= 50
    for r in rows:
        ref = (r.get("ref") or "").strip()
        if not ref:
            continue
        # 英会议 ref → en_zh；顺带 zh 假 hyp
        hyp_en = " ".join(["word"] * max(8, len(ref.split())))
        out = postedit_en_zh(ref, "占位译文" * 4)
        assert _token_count(out) >= 0.5 * _token_count("占位译文" * 4) - 1e-9
        # 中文方向：用 ref 当「假中文」不合适；用固定长 hyp
        hyp_zh_en = "This is a long enough hypothesis sentence for the gate."
        out2 = postedit_zh_en("无关中文句子没有关键字。", hyp_zh_en)
        assert out2 == hyp_zh_en
        assert _token_count(out2) >= 0.5 * _token_count(hyp_zh_en)
