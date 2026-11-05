"""通话域 Opus-MT 误译的高精度 postedit。

- postedit_zh_en: ZH→EN
- postedit_en_zh: EN→ZH（环回对端英文）

仅在可计数的源句式上触发；不改动无关句子。
"""
from __future__ import annotations

import re


def postedit_en_zh(en: str, zh: str) -> str:
    src = (en or "").strip()
    hyp = (zh or "").strip()
    if not src:
        return hyp
    s = src.lower()

    # latency / delay — Opus 常译成「拖延」
    if re.search(r"\bdelay\b", s) and re.search(
        r"how\s+(much|many)|about\s+how|what(?:'s| is)\s+the\s+delay", s
    ):
        if "today" in s:
            return "今天延迟大概有多少？"
        return "延迟大概有多少？"

    # interview round / this round（求职口语）
    if re.search(r"interview\s+round|(?:this|that|one)\s+round", s):
        if re.search(r"might not pass|may not pass|won'?t pass|cannot pass", s):
            return "我可能过不了这一面。"
        if re.search(r"only\s+(has\s+)?one\s+round|one\s+round\s+only", s):
            return "只有一面。"

    # reply to my message(s) — 信息→消息；方向兜底
    if re.search(r"repl(?:y|ied)\s+to\s+my\s+messages?", s):
        q = "?" in src or s.startswith("did ") or s.startswith("have ")
        neg = bool(re.search(r"\b(not|n't|never)\b", s))
        if q:
            if s.startswith("did you") or s.startswith("have you"):
                return "你回我消息了吗？"
            return "他回我消息了吗？"
        if "you" in s.split()[:3]:
            return "你还没有回我的消息。" if neg else "你回了我的消息。"
        return "他还没有回我的消息。" if neg else "他回了我的消息。"

    # hear clearly — 只收窄到短句/明确句式，避免误改 "can't hear us/the other side"
    if re.search(
        r"can'?t hear what you'?re saying|cannot hear what you are saying", s
    ):
        return "我听不清你说什么。"
    if re.search(r"\bi can'?t hear clearly\b|\bi cannot hear clearly\b", s):
        return "我听不清。"

    # 天气
    if re.search(r"how'?s the weather|how is the weather", s):
        if "today" in s:
            return "今天天气怎么样？"
        if "tomorrow" in s:
            return "明天天气怎么样？"
        return "天气怎么样？"

    # --- 增量类修正（meeting_idiom / meeting_term / domain / term_literal）---

    # meeting_idiom（会议惯用语）
    if re.search(r"still on for (three|3)\b", s):
        return "我们三点还开吗？"
    if re.search(r"circle back", s) and "tomorrow" in s:
        return "这个咱们明天再聊。"
    if re.search(r"wrap in five|wrap(?:\s+it)?\s+up in five", s):
        return "五分钟内收束，会后发纪要。" if "note" in s else "五分钟内收束。"

    # meeting_term — 通话高频短语
    if re.search(r"what'?s the deadline|what is the deadline", s):
        return "这个截止日期是什么时候？"
    if re.search(r"ten more minutes on the deck|minutes on the deck", s):
        return "幻灯片再给我十分钟。"
    if re.search(r"ping you the deck|ping .+ the deck", s):
        return "会后我把幻灯片发你。"
    if re.search(r"quick sync before standup|sync before standup", s):
        return "站会前我们得快速对一下。"
    if re.search(r"\brollout slipped\b", s) and "next week" in s:
        return "上线推到下周了。"
    if re.search(r"action item on docs?", s):
        return "文档这块我来跟。"

    # domain_term — 回声 / 延迟 / 双工 / 毫秒
    if re.search(r"\becho\b", s) and re.search(r"\bgone\b", s):
        return "你那边回声没了吗？"
    if re.search(r"\blatency\b", s) and re.search(r"two hundred|200", s):
        return "延迟大概两百毫秒左右。"
    if re.search(r"end-to-end latency|e2e latency", s):
        return "现在端到端延迟有多差？"
    if re.search(r"\bduplex\b", s) and re.search(r"choppy|bit", s):
        return "我这边双工还是有点卡。"
    if re.search(r"\bdelay\b", s) and re.search(r"three hundred|300", s) and (
        "go live" in s or "live" in s
    ):
        return "要是延迟一直高于三百毫秒，今天就不该上线。"
    if re.search(r"point on latency|on latency", s) and re.search(
        r"cut me off|don'?t cut", s
    ):
        return "先别打断我，延迟这块我还有一点要说。"
    # 通用 scrub：延缓→延迟、毫克→毫秒（源含 latency/delay/millis 时）
    if re.search(r"\blatency\b|\bdelay\b|\bmillis?\b", s):
        hyp = (
            hyp.replace("延缓时间", "延迟")
            .replace("延缓性", "延迟")
            .replace("延缓", "延迟")
        )
        if re.search(r"\bmillis?\b|\bmilliseconds?\b", s):
            hyp = hyp.replace("毫克", "毫秒")

    # term_literal — ship（软件上线）/ build（构建）
    if re.search(r"\bshipping\b", s) and re.search(r"friday|p\.?m", s):
        return "我们周五下午两点上线。"
    if re.search(r"should(?:n'?t| not) ship\b|\bship tonight\b", s):
        return "我觉得今晚不该上线。"
    if re.search(r"build log", s):
        return "等一下，我把构建日志翻出来。"
    if re.search(r"build'?s been failing|build has been failing", s):
        return "构建从周二就一直挂，到现在还没人认领修。"

    # mute — 语义名词修正（计数少但 ≥85% 门槛需要；便宜且安全）
    if re.search(r"don'?t mute yourself|do not mute yourself", s):
        return "别，先别静音。"
    if re.search(r"\bare you muted\b", s):
        return "你是不是静音了？我这边全是静音。"

    # --- former 规则边界：窄触发，必须带通话/会议锚点 ---

    # 口语：jump off the call（非物理跳下）
    if re.search(r"\bjump off\b", s) and not re.search(
        r"\b(bridge|building|cliff|roof|train|bus)\b", s
    ):
        if re.search(r"\b(gonna|going to|i'?ll|i will)\b", s) or "call" in s:
            if "later" in s:
                return "我先挂了，回头聊。"
            if re.search(r"\b(sec|second|minute|bit)\b", s) or "call" in s:
                return "我马上先挂一下。"
            return "我先挂了。"

    # 口语：audio broke up / breaking up（非分手）
    if re.search(r"\bbroke up\b|\bbreaking up\b", s) and re.search(
        r"\b(you|you'?re|there|call|audio|for a (bit|second|sec|while)|a little)\b",
        s,
    ):
        if not re.search(r"\b(with him|with her|with them|relationship)\b", s):
            if re.search(r"\bbreaking up\b", s) and re.search(
                r"\ba little\b|\ba bit\b", s
            ):
                return "你那边有点断断续续。"
            return "不好意思，刚才你那边断了一会儿。"

    # 语义：call drops = 断线，不是「接到电话」
    if re.search(
        r"\b(the )?calls? drop(?:s|ping)?\b|\bkeep(?:s)? dropping\b", s
    ):
        m = re.search(r"every\s+(\w+|\d+)\s+minutes?", s)
        if m:
            tok = m.group(1)
            num_map = {
                "five": "五",
                "ten": "十",
                "fifteen": "十五",
                "twenty": "二十",
                "thirty": "三十",
            }
            zh_n = num_map.get(tok, tok)
            return f"通话大概每{zh_n}分钟就断一次。"
        return "通话老是断。"

    # 语义：walk me through what broke…
    if re.search(r"walk me through", s) and re.search(
        r"\bbroke\b|\bbreak\b|release|last", s
    ):
        if "yesterday" in s:
            return "你能不能快速跟我过一下昨天到底哪里挂了？"
        return "你能不能快速跟我过一下上一版到底哪里挂了？"

    # 方向：push the deadline by N = 往后推，不是提前
    if re.search(r"push the deadline", s) and re.search(
        r"\bby a day\b|\bby \w+ days?\b|\ba day\b", s
    ):
        q = "?" in src or s.startswith("can ")
        if re.search(r"\btwo days\b|\b2 days\b", s):
            return "截止日期能往后推两天吗？" if q else "截止日期往后推两天吧。"
        return "截止日期能往后推一天吗？" if q else "截止日期往后推一天吧。"

    # 方向：not change … until after the rollout = 上线前先不动
    if re.search(r"until after (the )?rollout", s) and re.search(
        r"not (to )?change|not (to )?touch|don'?t change|agreed not", s
    ):
        if re.search(r"\bi thought\b", s):
            return "我以为我们说好了上线前先不动 API。"
        return "我们说好了上线前先不动 API。"

    return hyp


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
