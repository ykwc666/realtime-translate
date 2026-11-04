"""句尾 MT 辅助 + 可数星期白名单。

实机默认 final-only：中途不译。本模块保留：
- is_weekday_incomplete / should_skip_final_mt（断句补全与 forced skip）
- has_repeated_span（ASR 复读强切）
- should_translate 等中途门控仍在，但 dual_track 默认不再调用
"""
from __future__ import annotations

import math
import re

from . import config

_BOUNDARY = re.compile(r"[。！？!?；;]$")

# ZH function / hanging words that often mean "wait for the next content"
_ZH_HANG = {
    "的",
    "地",
    "得",
    "和",
    "与",
    "或",
    "及",
    "在",
    "把",
    "被",
    "从",
    "对",
    "向",
    "给",
    "让",
    "用",
    "比",
    "为",
    "而",
    "但",
    "并",
    "也",
    "又",
    "再",
    "就",
    "才",
    "都",
    "很",
    "更",
    "最",
    "太",
    "还",
    "不",
    "没",
    "有",
    "是",
    "会",
    "能",
    "要",
    "想",
    "像",
    "说",
    "看",
    "听",
    "如果",
    "虽然",
    "因为",
    "所以",
    "但是",
    "然后",
    "而且",
    "或者",
    "一个",
    "这个",
    "那个",
    "一些",
    "有些",
    "有点",
    "一点",
    "感觉",
    "觉得",
    "好像",
    "比较",
    "非常",
    "特别",
    "那么",
    "这么",
    "怎么",
    "什么",
    "多少",
    "翻译",
}

# 数字/时间量词：说到「十/点/分」就 WRITE 会得到 It's 10 p.m. + A little.
_ZH_NUM_HANG = {
    "十",
    "百",
    "千",
    "万",
    "亿",
    "点",
    "时",
    "分",
    "秒",
    "年",
    "月",
    "日",
    "号",
    "半",
    "多",
    "几",
    "两",
}
_EN_HANG = {
    "the",
    "a",
    "an",
    "of",
    "to",
    "for",
    "with",
    "and",
    "or",
    "but",
    "in",
    "on",
    "at",
    "from",
    "by",
    "as",
    "if",
    "when",
    "that",
}


def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def content_info_score(text: str) -> float:
    """Approximate information mass of pending source text."""
    t = (text or "").strip()
    if not t:
        return 0.0

    score = 0.0
    # multi-char hang first
    i = 0
    chars = list(t)
    while i < len(chars):
        matched = False
        for n in (3, 2):
            if i + n <= len(chars):
                piece = "".join(chars[i : i + n])
                if piece in _ZH_HANG:
                    score += 0.15
                    i += n
                    matched = True
                    break
        if matched:
            continue
        ch = chars[i]
        if _is_cjk(ch):
            score += 0.15 if ch in _ZH_HANG else 1.0
        elif ch.isalpha():
            # accumulate latin run as one token later
            j = i + 1
            while j < len(chars) and chars[j].isalpha():
                j += 1
            word = "".join(chars[i:j]).lower()
            score += 0.2 if word in _EN_HANG else 0.9
            i = j
            continue
        elif ch.isdigit():
            score += 0.4
        # punct / space: 0
        i += 1

    # diversity bonus (unique CJK / letters)
    uniq = {c.lower() for c in t if _is_cjk(c) or c.isalpha()}
    if uniq:
        score += min(2.0, math.log(1 + len(uniq)) * 0.35)
    return score


# 断句补全窗口：同一条可数白名单（禁止再扩开放集合）
# - 以「星期」结尾且总长 ≤ 8（覆盖「现在是星期」，排除长句气口末「星期」）
# - 或以「是星」结尾且总长 ≤ 4（覆盖「现在是星」，排除明星/星星）
_WEEKDAY_INCOMPLETE_MAX_LEN_星期 = 8
_WEEKDAY_INCOMPLETE_MAX_LEN_星 = 4


def ends_hanging(text: str) -> bool:
    t = (text or "").strip().rstrip("，,、 ")
    if not t:
        return False
    # 完整星期几：可以收句
    if re.search(r"(?:星期|礼拜|周)[一二三四五六日天]$", t):
        return False
    # 「现在是星期 / 是星」还没说到「一/二」
    if t.endswith("星期") or t.endswith("礼拜") or t.endswith("周"):
        return True
    if t.endswith("星") and not t.endswith("星期"):
        return True
    for n in (3, 2, 1):
        if len(t) >= n and (t[-n:] in _ZH_HANG or t[-n:] in _ZH_NUM_HANG):
            return True
    # ASCII / 中文数字未说完（「五十八」的「八」不是 str.isdigit）
    # 但「星期二」已在上方放行
    if t[-1].isdigit() or t[-1] in "零〇一二三四五六七八九":
        return True
    # last latin word
    m = re.search(r"([A-Za-z]+)$", t)
    if m and m.group(1).lower() in _EN_HANG:
        return True
    return False


def is_weekday_incomplete(text: str) -> bool:
    """Countable whitelist for completion-window / prestart strip. Do not expand."""
    t = (text or "").strip().rstrip("，,、 ")
    if not t:
        return False
    if re.search(r"(?:星期|礼拜|周)[一二三四五六日天]$", t):
        return False
    if t.endswith("星期") and len(t) <= _WEEKDAY_INCOMPLETE_MAX_LEN_星期:
        return True
    # 「现在是星」：必须带「是星」，避免「明星/星星」
    if t.endswith("是星") and len(t) <= _WEEKDAY_INCOMPLETE_MAX_LEN_星:
        return True
    return False


# Back-compat alias used by dual_track / tests
def is_strong_hang(text: str) -> bool:
    return is_weekday_incomplete(text)


def should_skip_final_mt(text: str, forced_truncation: bool) -> bool:
    """不再因星期白名单跳过 final：别人真说「这是星期」也要译。"""
    return False


def completeness_tier(text: str) -> str:
    """complete | weak | strong — strong only for weekday whitelist."""
    t = (text or "").strip()
    if not t:
        return "complete"
    if is_weekday_incomplete(t):
        return "strong"
    if ends_hanging(t):
        return "weak"
    return "complete"


def strip_weekday_incomplete_suffix(text: str) -> tuple[str, str]:
    """Same whitelist as endpoint: peel 星期/星 for prestart only."""
    t = (text or "").strip()
    if not t or not is_weekday_incomplete(t):
        return t, ""
    if t.endswith("星期"):
        return t[: -len("星期")].strip(), "星期"
    if t.endswith("是星"):
        return t[: -len("是星")].strip(), "是星"
    return t, ""


def strip_strong_hang_suffix(text: str) -> tuple[str, str]:
    """Alias → strip_weekday_incomplete_suffix (single whitelist)."""
    return strip_weekday_incomplete_suffix(text)


def has_repeated_span(text: str, min_span: int = 4) -> bool:
    """Detect ASR sticky loops like「…时快时慢的感觉你…」re-saying the head."""
    t = re.sub(r"\s+", "", text or "")
    if len(t) < min_span * 2:
        return False
    # Also: head prefix reappears after a particle (的/了)
    for n in range(min(12, len(t) // 2), min_span - 1, -1):
        head = t[:n]
        if t.find(head, n) >= 0:
            return True
    max_n = min(len(t) // 2, 16)
    for n in range(max_n, min_span - 1, -1):
        for i in range(0, len(t) - 2 * n + 1):
            span = t[i : i + n]
            if t.find(span, i + n) >= 0:
                return True
    return False



def should_translate(
    pending: str,
    force: bool = False,
    pending_age_sec: float | None = None,
) -> tuple[bool, str]:
    """
    Return (write?, reason).
    Hard timeout / hard_chars are UPSTREAM of hang gate (break read-loop).
    """
    p = (pending or "").strip()
    if not p:
        return False, "empty"
    if force:
        return True, "force"
    if _BOUNDARY.search(p):
        return True, "boundary"

    hard_max = int(getattr(config, "MT_PENDING_HARD_CHARS", 18))
    max_wait = float(getattr(config, "MT_PENDING_MAX_WAIT_SEC", 1.5))
    min_info = float(getattr(config, "MT_INFO_MIN", 4.0))

    def _soft_utt_end(s: str) -> bool:
        s = (s or "").strip()
        if not s:
            return False
        # 不含「了」：「解不了」常未说完（题），易误触发超时半句
        if s[-1] in "啊呢吧吗呀嘛。！？!?；;":
            return True
        return bool(_BOUNDARY.search(s))


    # Upstream overrides — must run before hang early-return
    if pending_age_sec is not None and pending_age_sec >= max_wait:
        safe, _tail = strip_weekday_incomplete_suffix(p)
        body = (safe or p).strip().lstrip("的地得了吗呢吧啊，,、 ")
        if not body:
            return False, "wait_timeout_hang"
        if len(body) < int(config.MIN_MT_CHARS) or content_info_score(body) < min_info * 0.6:
            return False, "wait_timeout_thin"
        # 未到软句尾则继续等 endpoint，避免「准确度」超时半句 + final「不高啊」
        if not _soft_utt_end(p) and len(p) < hard_max:
            return False, "wait_timeout_hold"
        return True, "reina_timeout"

    if len(p) >= hard_max:
        return True, "reina_hard"

    hang = ends_hanging(p)
    policy = getattr(config, "MT_POLICY", "chars")
    if policy == "chars":
        if hang:
            return False, "wait_hang"
        if len(p) >= config.MIN_MT_CHARS:
            return True, "chars"
        return False, "wait_chars"

    # reina-lite
    if hang:
        return False, "wait_hang"

    info = content_info_score(p)
    if info >= min_info:
        return True, "reina"

    return False, "wait_info"

