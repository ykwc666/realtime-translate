"""Minimal regression for final-only + weekday whitelist — run before each policy change.

python -m pytest tests/test_endpoint_regression.py -q
"""
from __future__ import annotations

import time

from app.endpoint_arbiter import ArbiterAction, EndpointArbiter
from app.mt_policy import (
    completeness_tier,
    is_strong_hang,
    is_weekday_incomplete,
    should_skip_final_mt,
    strip_weekday_incomplete_suffix,
)
from app.asr_stream import prefer_2pass_asr, prefer_final_asr
from app.tts_queue import TtsQueue


def test_prefer_final_asr_keeps_longer_prefix():
    assert prefer_final_asr("延迟大概有多少", "延迟大概有多") == "延迟大概有多少"
    assert prefer_final_asr("今天天气怎么", "今天天气怎么样") == "今天天气怎么样"
    assert prefer_final_asr("", "你好") == "你好"
    assert prefer_final_asr("你好", "") == "你好"
    # diverge → trust final
    assert prefer_final_asr("现在是新区", "现在是星期二") == "现在是星期二"


def test_prefer_2pass_extends_weekday():
    # Extension / empty paths — mode-invariant
    assert prefer_2pass_asr("星期", "星期三") == "星期三"
    assert prefer_2pass_asr("延迟大概有多", "延迟大概有多少") == "延迟大概有多少"
    assert prefer_2pass_asr("你好", "") == "你好"
    # Default is offline (AMI WER win); legacy still keeps stream on short conflict
    assert prefer_2pass_asr("欺负", "十六", mode="legacy") == "欺负"
    assert prefer_2pass_asr("欺负", "十六", mode="offline") == "十六"


def test_weekday_complete_vs_hang():
    assert completeness_tier("现在是星期") == "strong"
    assert completeness_tier("现在是星") == "strong"
    assert completeness_tier("现在是星期二") == "complete"
    assert not is_strong_hang("现在是星期二")
    assert not is_weekday_incomplete("现在是星期二")


def test_non_weekday_no_whitelist():
    assert not is_weekday_incomplete("五十八")
    assert not is_weekday_incomplete("感觉你翻译的有")
    assert not is_weekday_incomplete("延迟大概有多少")
    assert completeness_tier("好的") != "strong"


def test_final_never_skips_weekday_incomplete():
    assert not should_skip_final_mt("现在是星", forced_truncation=True)
    assert not should_skip_final_mt("这是星期", forced_truncation=True)
    assert not should_skip_final_mt("现在是星期二", forced_truncation=True)


def test_strip_weekday_only():
    safe, tail = strip_weekday_incomplete_suffix("现在是星期")
    assert safe == "现在是"
    assert tail == "星期"
    safe2, tail2 = strip_weekday_incomplete_suffix("感觉你翻译")
    assert tail2 == ""
    assert safe2 == "感觉你翻译"


def test_arbiter_strong_then_forced_window():
    arb = EndpointArbiter()
    t0 = time.perf_counter()
    d0 = arb.on_vad_candidate(asr_seq=2, now=t0)
    assert d0.action == ArbiterAction.WAIT_PARTIAL
    d1 = arb.tick("现在是星期", asr_seq=2, now=t0 + 0.15)
    assert d1.action == ArbiterAction.WAIT_COMPLETION
    # 窗口从 judge 时刻起算（静音后叠加），0.45s 未到期
    d_mid = arb.tick("现在是星期", asr_seq=2, now=t0 + 0.15 + 0.45)
    assert d_mid.action == ArbiterAction.WAIT_COMPLETION
    d2 = arb.tick("现在是星期", asr_seq=2, now=t0 + 0.15 + 0.90)
    assert d2.action == ArbiterAction.CUT_FORCED
    assert d2.forced_truncation is True


def test_completion_window_starts_at_judge_not_silence_budget():
    """写死：窗口 = judge 后 +0.85，不是静音 0.8 内剩余 0.05。"""
    from app import config

    arb = EndpointArbiter()
    t0 = 1000.0
    arb.on_vad_candidate(asr_seq=1, now=t0)
    # sync 过后才 judge；窗口从 t_judge 起算
    t_judge = t0 + 0.12
    d1 = arb.tick("现在是星期", asr_seq=2, now=t_judge)
    assert d1.action == ArbiterAction.WAIT_COMPLETION
    win = float(config.ENDPOINT_COMPLETION_WINDOW_SEC)
    assert arb.tick("现在是星期", 2, now=t_judge + win - 0.05).action == ArbiterAction.WAIT_COMPLETION
    assert arb.tick("现在是星期", 2, now=t_judge + win + 0.01).action == ArbiterAction.CUT_FORCED


def test_arbiter_second_vad_exhausted_when_max_rounds_1():
    """MAX_ROUNDS=1：首轮补全后，二次静音候选直接 completion_exhausted。"""
    from app import config

    assert int(config.ENDPOINT_COMPLETION_MAX_ROUNDS) == 1
    arb = EndpointArbiter()
    t0 = time.perf_counter()
    arb.on_vad_candidate(asr_seq=1, now=t0)
    d1 = arb.tick("现在是星期", asr_seq=2, now=t0 + 0.15)
    assert d1.action == ArbiterAction.WAIT_COMPLETION
    arb.on_vad_candidate(asr_seq=3, now=t0 + 0.4)
    d2 = arb.tick("现在是星期", asr_seq=4, now=t0 + 0.55)
    assert d2.action == ArbiterAction.CUT_FORCED
    assert d2.reason == "completion_exhausted"


def test_arbiter_complete_cuts_without_extra_wait():
    arb = EndpointArbiter()
    t0 = time.perf_counter()
    arb.on_vad_candidate(asr_seq=1, now=t0)
    d = arb.on_partial("现在是星期二", asr_seq=2, now=t0 + 0.05)
    assert d.action == ArbiterAction.CUT
    assert d.forced_truncation is False


def test_arbiter_weak_immediate_cut():
    """非白名单挂起词不再开补全窗口，立刻收句。"""
    arb = EndpointArbiter()
    t0 = time.perf_counter()
    arb.on_vad_candidate(asr_seq=1, now=t0)
    d = arb.tick("延迟大概有多少", asr_seq=2, now=t0 + 0.15)
    assert d.action == ArbiterAction.CUT
    assert not d.forced_truncation


def test_arbiter_resets_per_utterance():
    arb = EndpointArbiter()
    t0 = time.perf_counter()
    arb.on_vad_candidate(1, now=t0)
    arb.tick("现在是星期", 1, now=t0 + 0.2)
    arb.reset()
    assert arb.awaiting is False
    d = arb.on_vad_candidate(1, now=t0 + 1.0)
    assert d.action == ArbiterAction.WAIT_PARTIAL


def test_tts_version_invalidation_drops_stale():
    q = TtsQueue(tts=None, enabled=False)
    q.enabled = True
    q.tts = object()  # type: ignore[assignment]
    q._thread = None
    q.invalidate(utt_id=1, min_seq=0, min_version=2)
    from app.tts_queue import TtsJob

    stale = TtsJob(text="week", lang="en", utt_id=1, seq=1, version=1)
    fresh = TtsJob(text="Tuesday", lang="en", utt_id=1, seq=2, version=2)
    assert q._alive(stale) is False
    assert q._alive(fresh) is True
