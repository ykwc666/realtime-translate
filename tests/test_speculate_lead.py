"""Adaptive speculate trigger silence — pure function regression."""
from __future__ import annotations

from app.speculate_lead import (
    DECODE_MS_BIAS,
    DECODE_MS_PER_AUDIO_SEC,
    estimate_2pass_wall_ms,
    speculate_trigger_silence_sec,
)


def test_adaptive_false_equals_fixed():
    for sec in (0.0, 0.5, 1.2, 4.0, 20.0):
        got = speculate_trigger_silence_sec(
            sec,
            adaptive=False,
            vad_silence_sec=0.45,
            fixed_silence_sec=0.25,
            min_silence_sec=0.08,
            margin_sec=0.05,
        )
        assert got == 0.25


def test_long_not_later_than_short():
    short = speculate_trigger_silence_sec(
        1.0,
        adaptive=True,
        vad_silence_sec=0.45,
        fixed_silence_sec=0.25,
        min_silence_sec=0.08,
        margin_sec=0.05,
    )
    long = speculate_trigger_silence_sec(
        4.0,
        adaptive=True,
        vad_silence_sec=0.45,
        fixed_silence_sec=0.25,
        min_silence_sec=0.08,
        margin_sec=0.05,
    )
    assert long <= short


def test_result_clamped_in_bounds():
    for sec in (0.0, 0.2, 1.0, 2.0, 5.0, 15.0):
        got = speculate_trigger_silence_sec(
            sec,
            adaptive=True,
            vad_silence_sec=0.45,
            fixed_silence_sec=0.25,
            min_silence_sec=0.08,
            margin_sec=0.05,
        )
        assert 0.08 <= got <= 0.25


def test_zero_and_bad_inputs_safe():
    for bad in (0.0, -1.0, float("nan"), float("inf")):
        ms = estimate_2pass_wall_ms(bad)
        assert ms >= 180.0
        got = speculate_trigger_silence_sec(
            bad,
            adaptive=True,
            vad_silence_sec=0.45,
            fixed_silence_sec=0.25,
            min_silence_sec=0.08,
            margin_sec=0.05,
        )
        assert 0.08 <= got <= 0.25


def test_fit_coefficients_documented():
    # Sanity: mid-length ~1.4s → pred around 300ms band from bench
    pred = estimate_2pass_wall_ms(1.4)
    assert 250 <= pred <= 400
    assert DECODE_MS_PER_AUDIO_SEC > 0
    assert DECODE_MS_BIAS < 0
