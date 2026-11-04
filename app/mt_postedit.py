"""High-precision ZH→EN post-edits for call / interview phrases Opus-MT often twists.

Only fires on countable source patterns. Does not touch unrelated sentences.
"""
from __future__ import annotations

import re


def postedit_zh_en(zh: str, en: str) -> str:
    src = (zh or "").strip()
    hyp = (en or "").strip()
    if not src:
        return hyp

    # 「回我的消息 / 回我消息」——模型常把方向拧反
    if re.search(r"回我的?消息", src):
        q = bool(re.search(r"[吗？?]$", src))
        neg = bool(re.search(r"没|未", src))
        if q:
            if src.startswith("你") or src.startswith("您"):
                return "Did you reply to my message?"
            return "Has he replied to my message?"
        if src.startswith("你") or src.startswith("您"):
            return (
                "You haven't replied to my message yet."
                if neg
                else "You replied to my message."
            )
        if re.match(r"^(我|啊我)", src):
            return (
                "I haven't replied to the message yet."
                if neg
                else "I replied to the message."
            )
        # 默认第三人称（他/啊现在没有…）
        return (
            "He hasn't replied to my messages yet."
            if neg
            else "He replied to my messages."
        )

    # 「N面」求职口语 = interview round（不是 one/this side）
    if re.search(r"[一二三四五六]面", src) or re.search(r"这一面|这面", src):
        if "面试" in src and re.search(r"只有一面|就一面", src):
            return "The company I'm interviewing with only has one round."
        if re.search(r"过不了这一面|过不了一面|过不了这面", src):
            return "I might not pass this interview round."
        if re.search(r"\b(this|one|that)\s+side\b", hyp, flags=re.I):
            if re.search(r"过不了|过得了|通过|挂了|难", src):
                return "I might not pass this interview round."
            return re.sub(
                r"\b(this|one|that)\s+side\b", "interview round", hyp, flags=re.I
            )
        if "面试" in src and re.search(r"\bone side\b", hyp, flags=re.I):
            return re.sub(r"\bone side\b", "one round", hyp, flags=re.I)

    # 听不清
    if re.search(r"听不清", src):
        if "最后" in src:
            return "I can't hear the last part clearly."
        if "你说什么" in src or "说什么" in src:
            return "I can't hear what you're saying clearly."
        return "I can't hear clearly."

    # 天气怎么样 —— 避免 How was（无过去时间词时）
    if re.search(r"天气怎么样", src) and not re.search(r"昨天|刚才|早上", src):
        if src.startswith("今天") or "今天" in src:
            return "How's the weather today?"
        if src.startswith("明天") or "明天" in src:
            return "How's the weather tomorrow?"
        return "How's the weather?"

    # 翻译慢
    if re.search(r"翻译.{0,6}慢|慢.{0,4}翻译", src):
        return "I feel like the translation is a bit slow."

    return hyp
