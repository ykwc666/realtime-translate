"""TTS interruptible play: stop() aborts before full duration."""
from __future__ import annotations

import threading
import time

import numpy as np

from app.tts_play import TtsPlayer


def test_play_array_stop_cuts_short():
    tts = TtsPlayer()
    sr = 22050
    # 0.8s of silence (no loud pop if a device is open)
    audio = np.zeros(int(sr * 0.8), dtype=np.float32)
    done = threading.Event()
    played = [0.0]

    def run() -> None:
        played[0] = tts.play_array(audio, sr)
        done.set()

    th = threading.Thread(target=run, daemon=True)
    th.start()
    time.sleep(0.12)
    t0 = time.perf_counter()
    tts.stop()
    assert done.wait(timeout=2.0), "play did not finish after stop"
    stop_latency = (time.perf_counter() - t0) * 1000
    # Should not have played the full 0.8s (+tail pad)
    assert played[0] < 0.55, f"played too long after stop: {played[0]:.3f}s"
    assert stop_latency < 500, f"stop too slow: {stop_latency:.0f}ms"
