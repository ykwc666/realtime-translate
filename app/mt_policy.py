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

# 中文功能/挂起词，常表示「还有后续内容要说」
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
    """近似估计待译源文本的信息量。"""
    t = (text or "").strip()
    if not t:
        return 0.0

    score = 0.0
    # 先匹配多字 hang
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
            # 拉丁连续段稍后作为一个 token 累计
            j = i + 1
            while j < len(chars) and chars[j].isalpha():
                j += 1
            word = "".join(chars[i:j]).lower()
            score += 0.2 if word in _EN_HANG else 0.9
            i = j
            continue
        elif ch.isdigit():
            score += 0.4
        # 标点/空格：0 分
        i += 1

    # 多样性加成（唯一 CJK / 字母）
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
    # 末尾拉丁词
    m = re.search(r"([A-Za-z]+)$", t)
    if m and m.group(1).lower() in _EN_HANG:
        return True
    return False


def is_weekday_incomplete(text: str) -> bool:
    """补全窗/prestart 剥离用的可数白名单。勿扩大。"""
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


# dual_track / 测试用的向后兼容别名
def is_strong_hang(text: str) -> bool:
    return is_weekday_incomplete(text)


def should_skip_final_mt(text: str, forced_truncation: bool) -> bool:
    """不再因星期白名单跳过 final：别人真说「这是星期」也要译。"""
    return False


def completeness_tier(text: str) -> str:
    """complete | weak | strong — strong 仅用于星期白名单。"""
    t = (text or "").strip()
    if not t:
        return "complete"
    if is_weekday_incomplete(t):
        return "strong"
    if ends_hanging(t):
        return "weak"
    return "complete"


def strip_weekday_incomplete_suffix(text: str) -> tuple[str, str]:
    """与 endpoint 相同白名单：仅 prestart 时剥离 星期/星。"""
    t = (text or "").strip()
    if not t or not is_weekday_incomplete(t):
        return t, ""
    if t.endswith("星期"):
        return t[: -len("星期")].strip(), "星期"
    if t.endswith("是星"):
        return t[: -len("是星")].strip(), "是星"
    return t, ""


def strip_strong_hang_suffix(text: str) -> tuple[str, str]:
    """别名 → strip_weekday_incomplete_suffix（单一白名单）。"""
    return strip_weekday_incomplete_suffix(text)


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_EN_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def _has_adjacent_char_repeat(t: str, min_span: int) -> bool:
    """紧邻字符块重复：t[i:i+n] == t[i+n:i+2n]，n ≥ min_span。"""
    if len(t) < min_span * 2:
        return False
    max_n = min(len(t) // 2, 32)
    for n in range(max_n, min_span - 1, -1):
        for i in range(0, len(t) - 2 * n + 1):
            if t[i : i + n] == t[i + n : i + 2 * n]:
                return True
    return False


def _has_adjacent_word_repeat(
    text: str, min_words: int = 3, min_joined_chars: int = 12
) -> bool:
    """纯 ASCII：连续 ≥min_words 个词背靠背重复，且拼接合计够长。

    min_joined_chars 挡口语自我修正短段（如 written to a / written to a），
    避免把说话人重复当 ASR 粘住。
    """
    words = [w.lower() for w in _EN_WORD_RE.findall(text or "")]
    if len(words) < min_words * 2:
        return False
    max_n = len(words) // 2
    for n in range(max_n, min_words - 1, -1):
        for i in range(0, len(words) - 2 * n + 1):
            a = words[i : i + n]
            b = words[i + n : i + 2 * n]
            if a == b and len("".join(a)) >= min_joined_chars:
                return True
    return False


def has_repeated_span(text: str, min_span: int = 4) -> bool:
    """检测 ASR 粘住复读（仅紧邻重复，不认任意位置二次出现）。

    - 含 CJK：去空白后按字符，min_span（默认 4）紧邻块重复。
    - 纯 ASCII：连续 ≥3 词且拼接合计 ≥12 字符的紧邻重复，或 ≥12 字符紧邻重复。
    """
    raw = (text or "").strip()
    if not raw:
        return False
    if _CJK_RE.search(raw):
        t = re.sub(r"\s+", "", raw)
        return _has_adjacent_char_repeat(t, min_span)
    # 英文 / 其它 ASCII：词优先，字符兜底（长粘连无空格）
    if _has_adjacent_word_repeat(raw, min_words=3, min_joined_chars=12):
        return True
    t = re.sub(r"\s+", "", raw)
    return _has_adjacent_char_repeat(t, max(12, min_span))



def should_translate(
    pending: str,
    force: bool = False,
    pending_age_sec: float | None = None,
) -> tuple[bool, str]:
    """
    返回 (是否写入?, 原因)。
    hard timeout / hard_chars 在 hang 门控上游（打破读循环）。
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


    # 上游 override — 须在 hang 早退之前执行
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

    # reina-lite 策略
    if hang:
        return False, "wait_hang"

    info = content_info_score(p)
    if info >= min_info:
        return True, "reina"

    return False, "wait_info"

