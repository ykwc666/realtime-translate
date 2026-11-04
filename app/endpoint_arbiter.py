"""Utterance-scoped endpoint arbiter: VAD candidate × text completeness × timeout.

VAD silence only proposes a cut. Completeness decides immediate cut vs a short
completion window. Never lengthens the global silence floor.

Completion window timing (frozen): after silence candidate + partial sync, if
text is weekday-incomplete, deadline = now + ENDPOINT_COMPLETION_WINDOW_SEC
(additive to VAD_SILENCE_SEC, not carved out of it).

Assumption: final-only MT quality depends on final ASR completeness (trailing
chars). Do not reintroduce open hang blacklists to paper over ASR truncation.
See docs/BASELINE_FINAL_ONLY.md.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from . import config
from .mt_policy import is_weekday_incomplete



class ArbiterAction(str, Enum):
    NONE = "none"
    WAIT_PARTIAL = "wait_partial"
    WAIT_COMPLETION = "wait_completion"
    CUT = "cut"
    CUT_FORCED = "cut_forced"


@dataclass
class ArbiterDecision:
    action: ArbiterAction
    forced_truncation: bool = False
    reason: str = ""


class EndpointArbiter:
    """Per-utterance state machine; call reset() on every new utt."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._candidate = False
        self._wait_seq: int | None = None
        self._sync_deadline = 0.0
        self._completion_deadline = 0.0
        self._completion_rounds = 0  # 同句最多 N 轮补全，避免二次 VAD 立刻强切
        self._cut_done = False
        self._last_text = ""

    @property
    def awaiting(self) -> bool:
        return self._candidate and not self._cut_done

    def on_vad_candidate(self, asr_seq: int, now: float | None = None) -> ArbiterDecision:
        """Silence floor hit → candidate cut; sync to a fresher partial first."""
        if self._cut_done:
            return ArbiterDecision(ArbiterAction.NONE, reason="already_cut")
        now = time.perf_counter() if now is None else now
        sync_ms = float(getattr(config, "ENDPOINT_PARTIAL_SYNC_MS", 100)) / 1000.0
        self._candidate = True
        self._wait_seq = int(asr_seq)
        self._sync_deadline = now + max(0.0, sync_ms)
        self._completion_deadline = 0.0
        return ArbiterDecision(ArbiterAction.WAIT_PARTIAL, reason="sync_partial")


    def on_partial(self, text: str, asr_seq: int, now: float | None = None) -> ArbiterDecision:
        self._last_text = text or ""
        if not self._candidate or self._cut_done:
            return ArbiterDecision(ArbiterAction.NONE)
        now = time.perf_counter() if now is None else now
        if self._wait_seq is not None and asr_seq <= self._wait_seq and now < self._sync_deadline:
            return ArbiterDecision(ArbiterAction.WAIT_PARTIAL, reason="await_newer_seq")
        return self._judge_text(self._last_text, now)

    def tick(self, text: str, asr_seq: int, now: float | None = None) -> ArbiterDecision:
        """Poll while holding a candidate (no new partial)."""
        self._last_text = text or self._last_text
        if not self._candidate or self._cut_done:
            return ArbiterDecision(ArbiterAction.NONE)
        now = time.perf_counter() if now is None else now

        # Still in partial-sync window
        if self._completion_deadline <= 0:
            if self._wait_seq is not None and asr_seq <= self._wait_seq and now < self._sync_deadline:
                return ArbiterDecision(ArbiterAction.WAIT_PARTIAL, reason="await_newer_seq")
            # sync timeout or seq advanced → judge
            return self._judge_text(self._last_text, now)

        # Completion window
        if now < self._completion_deadline:
            if not is_weekday_incomplete(self._last_text):
                return self._cut(forced=False, reason="resolved")
            return ArbiterDecision(ArbiterAction.WAIT_COMPLETION, reason="completion_window")
        return self._cut(forced=True, reason="completion_timeout")

    def _judge_text(self, text: str, now: float) -> ArbiterDecision:
        self._wait_seq = None
        # 仅白名单开补全窗口；其余立即收句
        if is_weekday_incomplete(text):
            max_rounds = int(getattr(config, "ENDPOINT_COMPLETION_MAX_ROUNDS", 2))
            if self._completion_rounds >= max_rounds:
                return self._cut(forced=True, reason="completion_exhausted")
            win = float(getattr(config, "ENDPOINT_COMPLETION_WINDOW_SEC", 0.85))
            self._completion_deadline = now + max(0.05, win)
            self._completion_rounds += 1
            return ArbiterDecision(
                ArbiterAction.WAIT_COMPLETION,
                reason=f"weekday_incomplete_r{self._completion_rounds}",
            )
        return self._cut(forced=False, reason="cut_now")


    def _cut(self, forced: bool, reason: str) -> ArbiterDecision:
        self._cut_done = True
        self._candidate = False
        action = ArbiterAction.CUT_FORCED if forced else ArbiterAction.CUT
        return ArbiterDecision(action, forced_truncation=forced, reason=reason)
