"""拍板回归：可数白名单 + final skip + 方案 A 边界。"""
from __future__ import annotations

import time

from app.endpoint_arbiter import ArbiterAction, EndpointArbiter
from app.mt_policy import (
    completeness_tier,
    is_weekday_incomplete,
    should_skip_final_mt,
    strip_weekday_incomplete_suffix,
)


def test_hao_de_not_whitelist():
    assert not is_weekday_incomplete("好的")
    assert completeness_tier("好的") != "strong"


def test_long_sentence_xingqi_no_window():
    t = "我星期一要去开会，现在是星期"
    assert len(t) > 8
    assert not is_weekday_incomplete(t)


def test_forced_weekday_still_translates():
    """半截星期也译，避免「这是星期」被静音。"""
    assert is_weekday_incomplete("现在是星")
    assert is_weekday_incomplete("这是星期")
    assert not should_skip_final_mt("现在是星", forced_truncation=True)
    assert not should_skip_final_mt("这是星期", forced_truncation=True)


def test_wenda_and_ai_not_whitelist():
    assert not is_weekday_incomplete("延迟大概有多少")
    assert not is_weekday_incomplete("人工智能")
    assert not is_weekday_incomplete("明星")


def test_strip_same_whitelist():
    safe, tail = strip_weekday_incomplete_suffix("现在是星期")
    assert tail == "星期" and safe == "现在是"
    safe2, tail2 = strip_weekday_incomplete_suffix("延迟大概有多少")
    assert tail2 == "" and safe2 == "延迟大概有多少"


def test_arbiter_hao_de_immediate():
    arb = EndpointArbiter()
    t0 = time.perf_counter()
    arb.on_vad_candidate(1, now=t0)
    d = arb.tick("好的", 2, now=t0 + 0.15)
    assert d.action == ArbiterAction.CUT
    assert not d.forced_truncation


def test_arbiter_xing_completion_once():
    arb = EndpointArbiter()
    t0 = time.perf_counter()
    arb.on_vad_candidate(1, now=t0)
    d1 = arb.tick("现在是星", 2, now=t0 + 0.15)
    assert d1.action == ArbiterAction.WAIT_COMPLETION
    d2 = arb.tick("现在是星", 2, now=t0 + 0.15 + 0.90)
    assert d2.action == ArbiterAction.CUT_FORCED
