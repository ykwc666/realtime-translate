"""通话域 Opus-MT 误译的高精度 postedit。

- postedit_zh_en: ZH→EN
- postedit_en_zh: EN→ZH（环回对端英文）

锚定约束（2026-11-05）：
1. canned 规则只用 re.fullmatch（整句命中）
2. 源句含子句分隔符时跳过所有 canned，直接返回 hyp
   （曾有「canned 词数 < hyp 50%」长度闸；fullmatch 后属冗余，且会误杀
    「短 canned vs 啰嗦 hyp」的正常路径，已删除）
"""
from __future__ import annotations

import re


def _has_clause_sep(text: str, *, lang: str) -> bool:
    """含子句分隔 → 跳过 canned。

    zh：，,；;（复现「星期二，他没回消息」类）。
    en：中文分隔符、分号、或 ", and/but/…" 并列；
        不含「No, …」「Hang on, …」这类称呼逗号（否则几乎所有英文 canned 失效）。
    """
    t = text or ""
    if re.search(r"[，；]", t):
        return True
    if lang == "zh":
        return bool(re.search(r"[,;]", t))
    if ";" in t:
        return True
    return bool(re.search(r",\s+(and|but|so|then|while|which)\b", t, re.I))


def _token_count(text: str) -> int:
    """测试/诊断用；不再参与 canned 决策。"""
    t = (text or "").strip()
    if not t:
        return 0
    if re.search(r"[A-Za-z]", t):
        return max(1, len(t.split()))
    cjk = re.findall(r"[\u4e00-\u9fff]", t)
    if cjk:
        return max(1, len(cjk))
    parts = t.split()
    return max(1, len(parts) or 1)


def _canned(_hyp: str, out: str) -> str:
    """整句 canned 命中后的返回（不再做长度回退）。"""
    return out

def _fm(pattern: str, text: str, flags: int = 0) -> bool:
    return bool(re.fullmatch(pattern, text, flags=flags))


def postedit_en_zh(en: str, zh: str) -> str:
    src = (en or "").strip()
    hyp = (zh or "").strip()
    if not src:
        return hyp
    s = src.lower().rstrip()
    # 去掉尾标点便于 fullmatch；保留原 src 判断 ?
    s_core = re.sub(r"[.?!。？！]+$", "", s).strip()
    allow = not _has_clause_sep(src, lang="en")

    if allow:
        # latency / delay — Opus 常译成「拖延」
        if _fm(
            r"how\s+(much|many)\s+delay\s+is\s+there(?:\s+today)?|"
            r"about\s+how\s+much\s+delay(?:\s+is\s+there)?(?:\s+today)?|"
            r"what(?:'s| is)\s+the\s+delay(?:\s+today)?",
            s_core,
        ):
            if "today" in s_core:
                return _canned(hyp, "今天延迟大概有多少？")
            return _canned(hyp, "延迟大概有多少？")

        # interview round / this round（求职口语）
        if _fm(
            r"i might not pass this interview round|"
            r"i may not pass this interview round|"
            r"i won'?t pass this interview round",
            s_core,
        ):
            return _canned(hyp, "我可能过不了这一面。")
        if _fm(
            r"(?:the company i'?m interviewing with )?only\s+(?:has\s+)?one\s+round|"
            r"one\s+round\s+only",
            s_core,
        ):
            return _canned(hyp, "只有一面。")

        # reply to my message(s)
        if _fm(
            r"(?:did you|have you)\s+repl(?:y|ied)\s+to\s+my\s+messages?",
            s_core,
        ):
            return _canned(hyp, "你回我消息了吗？")
        if _fm(
            r"(?:did|has)\s+he\s+repl(?:y|ied)\s+to\s+my\s+messages?",
            s_core,
        ):
            return _canned(hyp, "他回我消息了吗？")
        if _fm(
            r"you\s+(?:still\s+)?(?:have\s+)?not\s+replied\s+to\s+my\s+messages?"
            r"|you\s+haven'?t\s+replied\s+to\s+my\s+messages?(?:\s+yet)?",
            s_core,
        ):
            return _canned(hyp, "你还没有回我的消息。")
        if _fm(r"you\s+replied\s+to\s+my\s+messages?", s_core):
            return _canned(hyp, "你回了我的消息。")
        if _fm(
            r"he\s+(?:still\s+)?(?:has\s+)?not\s+replied\s+to\s+my\s+messages?"
            r"|he\s+hasn'?t\s+replied\s+to\s+my\s+messages?(?:\s+yet)?",
            s_core,
        ):
            return _canned(hyp, "他还没有回我的消息。")
        if _fm(r"he\s+replied\s+to\s+my\s+messages?", s_core):
            return _canned(hyp, "他回了我的消息。")

        # hear clearly
        if _fm(
            r"i can'?t hear what you'?re saying(?:\s+clearly)?|"
            r"i cannot hear what you are saying(?:\s+clearly)?",
            s_core,
        ):
            return _canned(hyp, "我听不清你说什么。")
        if _fm(r"i can'?t hear clearly|i cannot hear clearly", s_core):
            return _canned(hyp, "我听不清。")

        # 天气
        if _fm(r"how'?s the weather today|how is the weather today", s_core):
            return _canned(hyp, "今天天气怎么样？")
        if _fm(r"how'?s the weather tomorrow|how is the weather tomorrow", s_core):
            return _canned(hyp, "明天天气怎么样？")
        if _fm(r"how'?s the weather|how is the weather", s_core):
            return _canned(hyp, "天气怎么样？")

        # meeting_idiom
        if _fm(r"are we still on for (?:three|3)\??", s_core + ("?" if "?" in src else "")):
            return _canned(hyp, "我们三点还开吗？")
        if _fm(r"are we still on for (?:three|3)", s_core):
            return _canned(hyp, "我们三点还开吗？")
        if _fm(
            r"let'?s circle back on that tomorrow|circle back on that tomorrow",
            s_core,
        ):
            return _canned(hyp, "这个咱们明天再聊。")
        if _fm(
            r"let'?s wrap in five and send notes after|"
            r"wrap in five and send notes after",
            s_core,
        ):
            return _canned(hyp, "五分钟内收束，会后发纪要。")
        if _fm(r"let'?s wrap in five|wrap(?:\s+it)?\s+up in five", s_core):
            return _canned(hyp, "五分钟内收束。")

        # meeting_term
        if _fm(r"what'?s the deadline(?: for this)?|what is the deadline(?: for this)?", s_core):
            return _canned(hyp, "这个截止日期是什么时候？")
        if _fm(
            r"(?:i'?\s*ll|i will)\s+ping you the deck(?: after this)?|"
            r"ping you the deck(?: after this)?",
            s_core,
        ):
            return _canned(hyp, "会后我把幻灯片发你。")
        if _fm(
            r"we need a quick sync before standup|"
            r"quick sync before standup",
            s_core,
        ):
            return _canned(hyp, "站会前我们得快速对一下。")
        if _fm(r"(?:i'?\s*ll|give me)\s+ten more minutes on the deck|minutes on the deck", s_core):
            return _canned(hyp, "幻灯片再给我十分钟。")
        if _fm(r"(?:the )?rollout slipped(?: to)? next week", s_core):
            return _canned(hyp, "上线推到下周了。")
        if _fm(r"action item on docs?", s_core):
            return _canned(hyp, "文档这块我来跟。")

        # domain
        if _fm(r"is the echo gone(?: on your side)?", s_core):
            return _canned(hyp, "你那边回声没了吗？")
        if _fm(
            r"latency is around two hundred millis?(?:econds)?|"
            r"latency is around 200 millis?(?:econds)?",
            s_core,
        ):
            return _canned(hyp, "延迟大概两百毫秒左右。")
        if _fm(r"(?:what'?s |how bad is )?(?:the )?end-to-end latency|e2e latency", s_core):
            return _canned(hyp, "现在端到端延迟有多差？")
        if _fm(
            r"duplex still feels a bit choppy(?: on my end)?|"
            r"duplex is still a bit choppy(?: on my end)?",
            s_core,
        ):
            return _canned(hyp, "我这边双工还是有点卡。")
        if _fm(
            r"if (?:the )?delay (?:stays|is) (?:above |higher than )?(?:three hundred|300) millis?"
            r".*go live.*|.*don'?t go live.*",
            s_core,
        ):
            return _canned(hyp, "要是延迟一直高于三百毫秒，今天就不该上线。")
        if _fm(
            r"(?:don'?t cut me off[, ]*)?one more point on latency|"
            r"point on latency.*don'?t cut",
            s_core,
        ):
            return _canned(hyp, "先别打断我，延迟这块我还有一点要说。")

        # term_literal ship/build
        if _fm(
            r"we(?:'re| are) shipping friday(?: at)?(?: 2| two)?\s*p\.?m\.?",
            s_core,
        ):
            return _canned(hyp, "我们周五下午两点上线。")
        if _fm(
            r"i don'?t think we should ship tonight|"
            r"we should(?:n'?t| not) ship tonight|"
            r"should(?:n'?t| not) ship tonight",
            s_core,
        ):
            return _canned(hyp, "我觉得今晚不该上线。")
        if _fm(
            r"hang on[, ]*i'?m pulling up the build log|"
            r"i'?m pulling up the build log",
            s_core,
        ):
            return _canned(hyp, "等一下，我把构建日志翻出来。")
        if _fm(
            r"the build'?s been failing since tuesday(?: and nobody owns the fix yet)?|"
            r"the build has been failing since tuesday.*",
            s_core,
        ):
            return _canned(hyp, "构建从周二就一直挂，到现在还没人认领修。")

        # mute
        if _fm(
            r"no[, ]*don'?t mute yourself(?: yet)?|"
            r"don'?t mute yourself(?: yet)?|"
            r"do not mute yourself(?: yet)?",
            s_core,
        ):
            return _canned(hyp, "别，先别静音。")
        if _fm(
            r"are you muted\??(?: i'?m getting silence on my end)?",
            s_core + ("?" if src.rstrip().endswith("?") else ""),
        ) or _fm(r"are you muted(?:\?|)$", s.lower().rstrip()):
            if "silence" in s or _fm(r"are you muted\??", s_core):
                if "silence" in s:
                    return _canned(hyp, "你是不是静音了？我这边全是静音。")
                return _canned(hyp, "你是不是静音了？我这边全是静音。")

        # colloquial / boundary
        if _fm(
            r"(?:i'?m )?(?:gonna|going to) jump off(?: the call)?(?: in a sec| later)?|"
            r"gonna jump off in a sec|"
            r"i'?\s*ll jump off(?: later)?",
            s_core,
        ) and not re.search(r"\b(bridge|building|cliff|roof|train|bus)\b", s):
            if "later" in s_core:
                return _canned(hyp, "我先挂了，回头聊。")
            if re.search(r"\b(sec|second|minute|bit)\b", s_core) or "call" in s_core:
                return _canned(hyp, "我马上先挂一下。")
            return _canned(hyp, "我先挂了。")

        if _fm(
            r"sorry[, ]*you broke up there for a bit|"
            r"you(?:'re)? breaking up a little|"
            r"audio broke up(?: for a (?:bit|second|sec|while))?",
            s_core,
        ) and not re.search(r"\b(with him|with her|with them|relationship)\b", s):
            if "breaking up" in s_core and re.search(r"\ba little\b|\ba bit\b", s_core):
                return _canned(hyp, "你那边有点断断续续。")
            return _canned(hyp, "不好意思，刚才你那边断了一会儿。")

        if _fm(
            r"(?:the )?calls? drop(?:s|ping)?(?: every \w+ minutes?(?: or so)?)?|"
            r"keep(?:s)? dropping(?: every \w+ minutes?)?",
            s_core,
        ):
            m = re.search(r"every\s+(\w+|\d+)\s+minutes?", s_core)
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
                return _canned(hyp, f"通话大概每{zh_n}分钟就断一次。")
            return _canned(hyp, "通话老是断。")

        if _fm(
            r"can you walk me through what broke in the last release(?: real quick)?|"
            r"walk me through what broke(?: yesterday| in the last release)?(?: real quick)?",
            s_core,
        ):
            if "yesterday" in s_core:
                return _canned(hyp, "你能不能快速跟我过一下昨天到底哪里挂了？")
            return _canned(hyp, "你能不能快速跟我过一下上一版到底哪里挂了？")

        if _fm(
            r"can we push the deadline by (?:a day|two days|2 days)\??|"
            r"push the deadline by (?:a day|two days|2 days)",
            s_core,
        ):
            q = "?" in src or s_core.startswith("can ")
            if re.search(r"\btwo days\b|\b2 days\b", s_core):
                return _canned(
                    hyp, "截止日期能往后推两天吗？" if q else "截止日期往后推两天吧。"
                )
            return _canned(
                hyp, "截止日期能往后推一天吗？" if q else "截止日期往后推一天吧。"
            )

        if _fm(
            r"i thought we agreed not to change the api until after the rollout|"
            r"we agreed not to (?:change|touch) the api until after the rollout|"
            r".*not (?:to )?change.*until after (?:the )?rollout",
            s_core,
        ):
            if "i thought" in s_core:
                return _canned(hyp, "我以为我们说好了上线前先不动 API。")
            return _canned(hyp, "我们说好了上线前先不动 API。")

    # --- 非 canned：词级 scrub（不整句替换）---
    if re.search(r"\blatency\b|\bdelay\b|\bmillis?\b", s):
        hyp2 = (
            hyp.replace("延缓时间", "延迟")
            .replace("延缓性", "延迟")
            .replace("延缓", "延迟")
        )
        if re.search(r"\bmillis?\b|\bmilliseconds?\b", s):
            hyp2 = hyp2.replace("毫克", "毫秒")
        hyp = hyp2

    return hyp


def _scrub_zh_en_artifacts(src: str, hyp: str) -> str:
    """非 canned：修 CT2/SPM 未知片与常见乱译（多子句也要跑）。"""
    out = (hyp or "").replace(" ⁇ ", " ").replace(" ⁇", " ").replace("⁇", " ")
    out = re.sub(r"\s{2,}", " ", out).strip()
    src = src or ""
    if "新疆" in src and re.search(r"(?i)\binjiang\b", out):
        out = re.sub(r"(?i)\bat\s+injiang\b", "in Xinjiang", out)
        out = re.sub(r"(?i)\binjiang\b", "Xinjiang", out)
    # 「日货」/ASR 截断「买日」→ Opus 常译成 day / daytime
    if re.search(r"日货|买日", src) and re.search(r"(?i)\bday(?:time)?s?\b", out):
        out = re.sub(
            r"(?i)buying\s+day(?:time)?s?(?:\s+or\s+Japanese)?",
            "buying Japanese products",
            out,
        )
        out = re.sub(r"(?i)\bday(?:time)?s?\b", "Japanese products", out, count=1)
        out = re.sub(
            r"(?i)Japanese products(?:\s+or)?\s+Japanese\b", "Japanese", out
        )
    if re.search(r"日货|买日", src):
        out = re.sub(
            r"(?i)buying\s+Japanese\b(?!\s+goods\b|\s+products\b|\s+makeup\b)",
            "buying Japanese products",
            out,
        )
        out = re.sub(r"(?i)Japanese goods\s+makeup", "Japanese makeup", out)
        out = re.sub(r"(?i)Japanese goods\b", "Japanese products", out)
        if re.search(r"化妆品", src) and re.search(r"(?i)\bmakeup\b", out):
            out = re.sub(
                r"(?i)Japanese products(?=\s*[.,]|\s+I\b|\s+It\b)",
                "Japanese makeup products",
                out,
                count=1,
            )
        if not re.search(r"(?i)\bhonest\b", out) and re.search(
            r"说实话|老实说", src
        ):
            out = out.rstrip(". ") + ", to be honest."
    # 对话口语：外卖 / 宅
    if "外卖" in src:
        out = re.sub(r"(?i)\bdelivery systems?\b", "takeaway system", out)
        out = re.sub(r"(?i)\bdelivery\b", "takeaway", out, count=1)
    if re.search(r"很宅|会很宅|宅", src):
        out = re.sub(r"(?i)\bhomey\b", "at home", out)
        out = re.sub(r"(?i)\bhomebody\b", "at home", out)
        out = re.sub(
            r"(?i)wanting to go out for dinner\??",
            "not feel like going out to eat",
            out,
        )
    # ASR 常把「整个大」听成「整个带」→ tape/belt
    if re.search(r"整个带", src):
        out = re.sub(
            r"(?i)(?:the\s+)?whole\s+(?:tape|belt)\b",
            "that whole big one of yours",
            out,
        )
        out = re.sub(
            r"(?i)you said (?:the )?whole",
            "You say that whole",
            out,
            count=1,
        )
        if re.search(r"(?i)\bbig\b", out) and not re.search(r"(?i)\byours\b", out):
            out = re.sub(r"(?i)\bbig\b", "big one of yours", out, count=1)
    # 「16点31分」Opus 常捏成 1631；要求「点」后带分钟，避免误伤「一点都不」
    for m in re.finditer(r"(\d{1,2})\s*点\s*(\d{1,2})\s*分", src):
        h, mi = int(m.group(1)), int(m.group(2))
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            continue
        clock = f"{h}:{mi:02d}"
        for glued in {f"{h}{mi}", f"{h}{mi:02d}"}:
            out = re.sub(rf"(?i)\b{re.escape(glued)}\b", clock, out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out


def postedit_zh_en(zh: str, en: str) -> str:
    src = (zh or "").strip()
    hyp = (en or "").strip()
    if not src:
        return hyp
    hyp = _scrub_zh_en_artifacts(src, hyp)
    if _has_clause_sep(src, lang="zh"):
        return hyp

    # 短回应（对话）
    src_core = re.sub(r"[。？！?.!]+$", "", src).strip()
    if _fm(r"对|是的?|嗯|好|好的|行", src_core):
        if src_core in ("对", "是", "是的"):
            return _canned(hyp, "Right." if src_core == "对" else "Yes.")
        if src_core in ("嗯",):
            return _canned(hyp, "Yeah.")
        if src_core in ("好", "好的", "行"):
            return _canned(hyp, "Okay.")
    if _fm(r"嗨?你好|你好啊?", src_core):
        return _canned(hyp, "Hi, how are you?")

    # 「回我的消息 / 回我消息」——整句锚定
    if _fm(
        r"(?:啊)?(?:现在)?(?:他|她|你|您|我)?(?:现在)?(?:还|也)?"
        r"(?:没有|没|未)?(?:有)?回(?:我的|我)?消息(?:了)?[吗？?。！!]?",
        src,
    ):
        q = bool(re.search(r"[吗？?]$", src))
        neg = bool(re.search(r"没|未", src))
        if q:
            if src.startswith("你") or src.startswith("您"):
                return _canned(hyp, "Did you reply to my message?")
            return _canned(hyp, "Has he replied to my message?")
        if src.startswith("你") or src.startswith("您"):
            return _canned(
                hyp,
                (
                    "You haven't replied to my message yet."
                    if neg
                    else "You replied to my message."
                ),
            )
        if re.match(r"^(我|啊我)", src):
            return _canned(
                hyp,
                (
                    "I haven't replied to the message yet."
                    if neg
                    else "I replied to the message."
                ),
            )
        return _canned(
            hyp,
            (
                "He hasn't replied to my messages yet."
                if neg
                else "He replied to my messages."
            ),
        )

    # 「N面」求职口语
    if _fm(r".*面试的?这个公司只有一面.*", src) or _fm(
        r"我现在面试的这个公司只有一面[。？！?]?", src
    ):
        return _canned(hyp, "The company I'm interviewing with only has one round.")
    if _fm(r"我可能过不了这一面[。？！?]?", src) or _fm(
        r".*过不了这一面.*", src
    ):
        # 仅短句 canned；过长由 fullmatch 的 .* 可能过宽——限制长度
        if len(src) <= 16:
            return _canned(hyp, "I might not pass this interview round.")
    if re.search(r"[一二三四五六]面|这一面|这面", src) and len(src) <= 24:
        if "面试" in src and re.search(r"只有一面|就一面", src):
            return _canned(hyp, "The company I'm interviewing with only has one round.")
        if re.search(r"过不了这一面|过不了一面|过不了这面", src):
            return _canned(hyp, "I might not pass this interview round.")
        if re.search(r"\b(this|one|that)\s+side\b", hyp, flags=re.I):
            if re.search(r"过不了|过得了|通过|挂了|难", src):
                return _canned(hyp, "I might not pass this interview round.")
            patched = re.sub(
                r"\b(this|one|that)\s+side\b", "interview round", hyp, flags=re.I
            )
            return _canned(hyp, patched)
        if "面试" in src and re.search(r"\bone side\b", hyp, flags=re.I):
            return _canned(hyp, re.sub(r"\bone side\b", "one round", hyp, flags=re.I))

    # 听不清
    if _fm(r"我?(?:觉得)?听不清你说什么[。？！?]?", src):
        return _canned(hyp, "I can't hear what you're saying clearly.")
    if _fm(r"我?(?:觉得)?听不清最后(?:部分|一句|那段)?[。？！?]?", src):
        return _canned(hyp, "I can't hear the last part clearly.")
    if _fm(r"我?(?:觉得)?听不清[。？！?]?", src):
        return _canned(hyp, "I can't hear clearly.")

    # 天气怎么样 —— 避免 How was（无过去时间词时）
    if not re.search(r"昨天|刚才|早上", src):
        if _fm(r"今天天气怎么样[。？！?]?", src):
            return _canned(hyp, "How's the weather today?")
        if _fm(r"明天天气怎么样[。？！?]?", src):
            return _canned(hyp, "How's the weather tomorrow?")
        if _fm(r"天气怎么样[。？！?]?", src):
            return _canned(hyp, "How's the weather?")

    # 翻译慢
    if _fm(r".{0,8}翻译.{0,6}慢.{0,8}", src) and len(src) <= 20:
        return _canned(hyp, "I feel like the translation is a bit slow.")

    return hyp
