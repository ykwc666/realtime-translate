from __future__ import annotations

import re
from dataclasses import dataclass


_SENT_END = re.compile(r"(?<=[。！？!?\.])")
_TAIL_PUNCT = "。！？.!?,，、；; "


def common_prefix(a: str, b: str) -> str:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return a[:i]


def _strip_tail_punct(s: str) -> str:
    return s.rstrip(_TAIL_PUNCT)


def _content_chars(s: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", s, flags=re.UNICODE)


def untranslated_tail(final_text: str, translated_src: str) -> str:
    """Return final suffix not yet covered by WRITE cursor (punct-insensitive)."""
    final_text = (final_text or "").strip()
    translated_src = (translated_src or "").strip()
    if not final_text:
        return ""
    if not translated_src:
        return final_text
    if final_text.startswith(translated_src):
        return final_text[len(translated_src) :]

    fn = _content_chars(final_text)
    tn = _content_chars(translated_src)
    if not tn:
        return final_text
    if fn.startswith(tn):
        need = len(tn)
    else:
        need = len(common_prefix(fn, tn))
        # SenseVoice final often differs by 1–2 chars (打/达); keep overlap
        if need < max(2, int(0.35 * min(len(tn), len(fn)))):
            return final_text

    seen = 0
    for i, ch in enumerate(final_text):
        if re.match(r"[\w\u4e00-\u9fff]", ch, flags=re.UNICODE):
            seen += 1
            if seen >= need:
                return final_text[i + 1 :].lstrip("。！？.!?,，、；; ")
    return ""


@dataclass
class StablePrefixTracker:
    """
    Commit stable text from streaming/partial ASR.

    Cursors (source side):
    - stable/committed: confirmed ASR prefix
    - asr_seq: only accept newer ASR hypotheses
    Target TTS pending buffer is separate (pending_tts).
    """

    agree_needed: int = 2
    min_commit_chars: int = 2
    committed: str = ""
    asr_seq: int = 0
    _last_partial: str = ""
    _candidate: str = ""
    _agree: int = 0
    pending_tts: str = ""

    def update(self, partial: str, seq: int | None = None) -> str:
        if seq is not None and seq < self.asr_seq:
            return ""
        if seq is not None:
            self.asr_seq = seq
        partial = (partial or "").strip()
        if not partial:
            return ""

        if not self._last_partial:
            self._last_partial = partial
            return ""

        last = self._last_partial
        shared = common_prefix(last, partial)
        last_core = _strip_tail_punct(last)
        growing = (
            partial.startswith(last_core)
            and len(partial) > len(last_core)
        ) or (
            len(shared) >= max(1, len(last_core) - 1) and len(partial) > len(last)
        )

        self._last_partial = partial

        # Prefer the confirmed older span when growing
        target = last_core if growing and last_core else shared
        if len(target) <= len(self.committed):
            return ""
        if not target.startswith(self.committed) and not self.committed.startswith(
            target[: min(len(self.committed), len(target))]
        ):
            # hard rewrite — wait for finalize
            return ""
        if not partial.startswith(self.committed) and not target.startswith(self.committed):
            return ""

        if growing:
            if not target.startswith(self.committed):
                return ""
            delta = target[len(self.committed) :]
            if len(delta.strip()) < self.min_commit_chars:
                return ""
            self.committed = target
            self._candidate = target
            self._agree = self.agree_needed
            return delta

        # flickering path
        if shared == self._candidate:
            self._agree += 1
        else:
            self._candidate = shared
            self._agree = 1
        if self._agree < self.agree_needed:
            return ""
        if not shared.startswith(self.committed):
            return ""
        delta = shared[len(self.committed) :]
        if len(delta.strip()) < self.min_commit_chars:
            return ""
        self.committed = shared
        return delta

    def finalize(self, final_text: str, translated_src: str = "") -> str:
        """
        Accept final ASR as new stable text.
        Return only the untranslated tail relative to translated_src (WRITE cursor),
        so already-WRITEn source is not re-queued for MT.
        """
        final_text = (final_text or "").strip()
        # WRITE cursor only — never fall back to ASR committed (untranslated stable
        # text would be skipped and only a short tail like「期一」gets MT).
        translated_src = (translated_src or "").strip()
        self.asr_seq = max(self.asr_seq + 1, 10_000_000)  # final beats partials
        leftover = untranslated_tail(final_text, translated_src)
        self.committed = final_text
        self._last_partial = final_text
        self._candidate = final_text
        self._agree = self.agree_needed
        return leftover

    def reset(self) -> None:
        self.committed = ""
        self.asr_seq = 0
        self._last_partial = ""
        self._candidate = ""
        self._agree = 0
        self.pending_tts = ""

    def take_speakable(self, text: str, min_chars: int, force: bool = False) -> list[str]:
        buf = self.pending_tts + (text or "")
        if not buf.strip():
            self.pending_tts = ""
            return []

        # Protect EN abbreviations (p.m. / a.m. / U.S.) from splits on '.'
        masked = re.sub(r"\b([A-Za-z])\.", lambda m: m.group(1) + "\0", buf)
        parts = _SENT_END.split(masked)
        out: list[str] = []
        rem = ""
        for i, p in enumerate(parts):
            p = p.replace("\0", ".").strip()
            if not p:
                continue
            is_last = i == len(parts) - 1
            # sentence-final: CJK/!/? or '.' that is not only an abbrev tail
            stripped = buf.strip()
            complete = bool(re.search(r"[。！？!]$", stripped)) or (
                stripped.endswith(".")
                and not re.search(r"\b[A-Za-z]\.\s*$", stripped)
            )
            if is_last and not complete:
                rem = p
            else:
                out.append(p)

        if force and rem:
            out.append(rem)
            rem = ""
        elif not out and not force and len(rem) >= min_chars:
            out.append(rem)
            rem = ""

        self.pending_tts = rem
        return out

    def flush_pending(self) -> str:
        out = self.pending_tts.strip()
        self.pending_tts = ""
        return out
