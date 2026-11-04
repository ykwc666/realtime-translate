"""TTS play timing fields + persistent OutputStream flag / interrupt."""
from __future__ import annotations

import threading
import time

import numpy as np

from app import config
from app.tts_play import TtsPlayer


def test_play_array_exposes_timing_oneshot():
    prev = config.TTS_PERSISTENT_OUTPUT
    config.TTS_PERSISTENT_OUTPUT = False
    try:
        tts = TtsPlayer()
        sr = 22050
        audio = np.zeros(int(sr * 0.15), dtype=np.float32)
        audio[200:400] = 0.05
        t0 = time.perf_counter()
        played = tts.play_array(audio, sr, speak_t0=t0)
        pt = tts.last_play_timing
        assert pt is not None
        assert pt.persistent is False
        assert pt.stream_open_ms >= 0
        assert pt.ttfa_callback_ms > 0
        assert pt.samples_expected > 0
        assert pt.samples_written > 0
        assert played > 0
        tts.close()
    finally:
        config.TTS_PERSISTENT_OUTPUT = prev


def test_persistent_flag_reuses_stream_and_stop():
    prev = config.TTS_PERSISTENT_OUTPUT
    config.TTS_PERSISTENT_OUTPUT = True
    try:
        tts = TtsPlayer()
        sr = 22050
        audio = np.zeros(int(sr * 0.2), dtype=np.float32)
        audio[500:700] = 0.08
        tts.play_array(audio, sr)
        assert tts.last_play_timing is not None
        assert tts.last_play_timing.persistent is True
        # second play should reuse
        tts.play_array(audio, sr)
        assert tts.last_play_timing.stream_reused is True
        assert tts.last_play_timing.stream_open_ms == 0.0

        long_audio = np.zeros(int(sr * 0.9), dtype=np.float32)
        long_audio[1000:1300] = 0.1
        done = threading.Event()
        played = [0.0]

        def run() -> None:
            played[0] = tts.play_array(long_audio, sr)
            done.set()

        th = threading.Thread(target=run, daemon=True)
        th.start()
        time.sleep(0.12)
        t0 = time.perf_counter()
        tts.stop()
        assert done.wait(timeout=2.0)
        stop_ms = (time.perf_counter() - t0) * 1000
        assert played[0] < 0.55
        assert stop_ms < 500
        tts.close()
    finally:
        config.TTS_PERSISTENT_OUTPUT = prev


def test_persistent_off_still_interruptible():
    prev = config.TTS_PERSISTENT_OUTPUT
    config.TTS_PERSISTENT_OUTPUT = False
    try:
        tts = TtsPlayer()
        sr = 22050
        audio = np.zeros(int(sr * 0.8), dtype=np.float32)
        done = threading.Event()
        played = [0.0]

        def run() -> None:
            played[0] = tts.play_array(audio, sr)
            done.set()

        th = threading.Thread(target=run, daemon=True)
        th.start()
        time.sleep(0.12)
        tts.stop()
        assert done.wait(timeout=2.0)
        assert played[0] < 0.55
        tts.close()
    finally:
        config.TTS_PERSISTENT_OUTPUT = prev
