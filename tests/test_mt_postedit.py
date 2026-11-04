"""Call-domain MT postedit regression."""
from __future__ import annotations

from app.mt_postedit import postedit_zh_en


def test_reply_message_direction():
    assert (
        postedit_zh_en("他现在没有回我的消息", "He hasn't heard back from me yet.")
        == "He hasn't replied to my messages yet."
    )
    assert (
        postedit_zh_en("啊现在没有回我的消息", "Ah, no news of me now.")
        == "He hasn't replied to my messages yet."
    )
    assert postedit_zh_en("你回我消息了吗", "Did you get my message?") == (
        "Did you reply to my message?"
    )


def test_interview_one_round():
    assert "one round" in postedit_zh_en(
        "我现在面试的这个公司只有一面",
        "I'm only interviewing one side of the company.",
    ).lower()
    assert "round" in postedit_zh_en(
        "我可能过不了这一面", "I may not pass this one side."
    ).lower()


def test_hear_weather_slow():
    assert "clearly" in postedit_zh_en(
        "我听不清你说什么", "I can't hear you."
    ).lower()
    assert postedit_zh_en("今天天气怎么样", "How was the weather today.").startswith(
        "How's the weather today"
    )
    assert "slow" in postedit_zh_en(
        "感觉你翻译的有点慢", "You're a little slow to translate."
    ).lower()


def test_unrelated_untouched():
    en = "Can you say that again?"
    assert postedit_zh_en("你能再说一遍吗", en) == en
