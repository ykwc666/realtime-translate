"""Persistent OutputStream: interrupt → resume integrity + active underrun semantics.

Real device. Run:
  .venv\\Scripts\\python.exe -m pytest tests/test_tts_persist_stress.py -q --tb=short
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from app import config
from app.tts_play import TtsPlayer


def _tone(sr: int, sec: float, freq: float = 440.0, amp: float = 0.08) -> np.ndarray:
    n = int(sr * sec)
    t = np.arange(n, dtype=np.float32) / float(sr)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _run_interrupt_cycle(tts: TtsPlayer, sr: int, long_sec: float = 1.0) -> dict:
    long_a = _tone(sr, long_sec, 440.0)
    short_b = _tone(sr, 0.25, 660.0)
    short_c = _tone(sr, 0.25, 880.0)
    pad = float(getattr(config, "TTS_PLAY_TAIL_PAD_SEC", 0.18))

    # 1) interrupt long
    import threading

    done = threading.Event()
    played = [0.0]

    def run_long() -> None:
        played[0] = tts.play_array(long_a, sr)
        done.set()

    th = threading.Thread(target=run_long, daemon=True)
    th.start()
    time.sleep(0.12)
    t0 = time.perf_counter()
    tts.stop()
    assert done.wait(2.0), "long play hung after stop"
    stop_ms = (time.perf_counter() - t0) * 1000
    assert played[0] < long_sec * 0.55, f"interrupt too late: {played[0]:.3f}s"
    assert stop_ms < 500, f"stop slow: {stop_ms:.0f}ms"

    # idle gap (≥300ms) then two full shorts
    time.sleep(0.35)
    expect_b = int(short_b.size + pad * sr)
    pb = tts.play_array(short_b, sr)
    tb = tts.last_play_timing
    assert tb is not None
    assert tb.first_callback_t is not None, "short B missing first_callback_t"
    assert tb.callback_valid is True
    assert tb.samples_written >= int(expect_b * 0.95), (
        f"B short write {tb.samples_written} vs expect {expect_b}"
    )
    assert abs(tb.samples_written - expect_b) <= max(512, int(0.02 * sr))
    assert tb.active_underruns == 0, f"B active underruns={tb.active_underruns}"
    assert pb >= 0.20

    time.sleep(0.05)
    expect_c = int(short_c.size + pad * sr)
    pc = tts.play_array(short_c, sr)
    tc = tts.last_play_timing
    assert tc is not None
    assert tc.first_callback_t is not None
    assert tc.callback_valid is True
    assert abs(tc.samples_written - expect_c) <= max(512, int(0.02 * sr))
    assert tc.active_underruns == 0
    assert pc >= 0.20
    return {
        "stop_ms": stop_ms,
        "b_written": tb.samples_written,
        "c_written": tc.samples_written,
        "b_underruns": tb.active_underruns,
        "c_underruns": tc.active_underruns,
    }


@pytest.mark.parametrize("persistent", [True, False])
def test_interrupt_resume_20_cycles(persistent: bool):
    prev = config.TTS_PERSISTENT_OUTPUT
    config.TTS_PERSISTENT_OUTPUT = persistent
    tts = TtsPlayer()
    sr = 22050
    try:
        # warm device once
        tts.play_array(_tone(sr, 0.05), sr)
        fails = 0
        stop_ms_list: list[float] = []
        for i in range(20):
            try:
                info = _run_interrupt_cycle(tts, sr)
                stop_ms_list.append(info["stop_ms"])
            except AssertionError as exc:
                fails += 1
                print(f"cycle {i} FAIL persistent={persistent}: {exc}")
        assert fails == 0, f"{fails}/20 cycles failed (persistent={persistent})"
        assert max(stop_ms_list) < 500
    finally:
        tts.close()
        config.TTS_PERSISTENT_OUTPUT = prev


def test_idle_zero_not_active_underrun_persistent():
    prev = config.TTS_PERSISTENT_OUTPUT
    config.TTS_PERSISTENT_OUTPUT = True
    tts = TtsPlayer()
    sr = 22050
    try:
        a = _tone(sr, 0.2)
        tts.play_array(a, sr)
        # idle ≥300ms with stream still open
        time.sleep(0.35)
        tts.play_array(a, sr)
        pt = tts.last_play_timing
        assert pt is not None
        assert pt.persistent is True
        assert pt.stream_reused is True
        assert pt.active_underruns == 0
        assert pt.first_callback_t is not None
    finally:
        tts.close()
        config.TTS_PERSISTENT_OUTPUT = prev


def test_silence_no_fake_callback_ttfa():
    prev = config.TTS_PERSISTENT_OUTPUT
    config.TTS_PERSISTENT_OUTPUT = True
    tts = TtsPlayer()
    sr = 22050
    try:
        silence = np.zeros(int(sr * 0.2), dtype=np.float32)
        tts.play_array(silence, sr)
        pt = tts.last_play_timing
        assert pt is not None
        # must not claim a real nonzero first callback
        assert pt.first_callback_t is None
        assert pt.callback_valid is False
    finally:
        tts.close()
        config.TTS_PERSISTENT_OUTPUT = prev
