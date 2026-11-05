"""按句 endpoint 仲裁：VAD 候选 × 文本完整性 × 超时。

VAD 静音仅提出切分候选。完整性决定立即 CUT 或短补全窗。
不拉长全局静音底限。

补全窗计时（冻结）：静音候选 + partial sync 之后，若文本为「星期」未完成，
deadline = now + ENDPOINT_COMPLETION_WINDOW_SEC（与 VAD_SILENCE_SEC 叠加，非从中扣除）。

假设：final-only MT 质量依赖 final ASR 完整（末字）。
勿再用开放 hang 黑名单掩盖 ASR 截断。
见 docs/BASELINE_FINAL_ONLY.md。
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
    """每句状态机；新 utt 时须 reset()。"""

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
        """静音底限到达 → 候选切分；先 sync 到更新的 partial。"""
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
        """持有候选时轮询（无新 partial）。"""
        self._last_text = text or self._last_text
        if not self._candidate or self._cut_done:
            return ArbiterDecision(ArbiterAction.NONE)
        now = time.perf_counter() if now is None else now

        # 仍在 partial-sync 窗内
        if self._completion_deadline <= 0:
            if self._wait_seq is not None and asr_seq <= self._wait_seq and now < self._sync_deadline:
                return ArbiterDecision(ArbiterAction.WAIT_PARTIAL, reason="await_newer_seq")
            # sync 超时或 seq 前进 → 判定
            return self._judge_text(self._last_text, now)

        # 补全窗
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
