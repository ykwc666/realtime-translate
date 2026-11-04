"""Pure helpers: when to start speculative 2-pass finalize.

Coefficients from scripts/bench_2pass_ab.py parallel online=3 offline=2
on accept_phrases + tail_duosimo (6 segs), linear fit:

  audio_sec: 1.476, 1.380, 1.188, 1.732, 1.540, 1.380
  wall_med:  353,   293,   266,   497,   402,   294
  wall_ms ≈ -310.93 + 456.60 * audio_sec   (R≈OK; clamp floor 180ms)

trigger_silence ≈ VAD_SILENCE − pred_decode − margin,
clamped to [min_silence, fixed ASR_SPECULATE_SILENCE_SEC].
"""
from __future__ import annotations

import math


# Fit from docs/bench_2pass_ab_raw.txt (par o=3 f=2 wall_med), 2026-11-04.
DECODE_MS_BIAS = -310.93
DECODE_MS_PER_AUDIO_SEC = 456.60
DECODE_MS_FLOOR = 180.0


def estimate_2pass_wall_ms(
    audio_sec: float,
    *,
    bias_ms: float = DECODE_MS_BIAS,
    per_audio_sec_ms: float = DECODE_MS_PER_AUDIO_SEC,
    floor_ms: float = DECODE_MS_FLOOR,
) -> float:
    if audio_sec is None or not math.isfinite(float(audio_sec)) or float(audio_sec) <= 0:
        return float(floor_ms)
    pred = float(bias_ms) + float(per_audio_sec_ms) * float(audio_sec)
    if not math.isfinite(pred):
        return float(floor_ms)
    return max(float(floor_ms), pred)


def speculate_trigger_silence_sec(
    audio_sec: float,
    *,
    adaptive: bool,
    vad_silence_sec: float,
    fixed_silence_sec: float,
    min_silence_sec: float,
    margin_sec: float,
    bias_ms: float = DECODE_MS_BIAS,
    per_audio_sec_ms: float = DECODE_MS_PER_AUDIO_SEC,
    floor_ms: float = DECODE_MS_FLOOR,
) -> float:
    """Return silence seconds required before starting a speculative job."""
    fixed = float(fixed_silence_sec)
    lo = float(min_silence_sec)
    hi = fixed
    if lo > hi:
        lo, hi = hi, lo
    if not adaptive:
        return fixed
    pred_ms = estimate_2pass_wall_ms(
        audio_sec,
        bias_ms=bias_ms,
        per_audio_sec_ms=per_audio_sec_ms,
        floor_ms=floor_ms,
    )
    pred_sec = pred_ms / 1000.0
    raw = float(vad_silence_sec) - pred_sec - float(margin_sec)
    if not math.isfinite(raw):
        return fixed
    return min(hi, max(lo, raw))
