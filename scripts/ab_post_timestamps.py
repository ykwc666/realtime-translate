"""A/B: char-proportional vs energy-valley sentence timestamps.

Builds 3 phrases with ≥0.6s silence between them (known boundaries).
Gate: energy MAE ≤ 0.60 × char MAE  OR  energy MAE ≤ 0.25s.
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402
from app.retranscribe import (  # noqa: E402
    _alloc_timestamps,
    _alloc_timestamps_energy,
)
from piper import PiperVoice  # noqa: E402

SR = int(config.SAMPLE_RATE)
PHRASES = ["你好啊朋友", "今天天气不错", "我们明天见吧"]
SILENCE_SEC = 0.8
OUT_WAV = ROOT / "cache" / "ab_weekday" / "post_ts_ab.wav"


def _synth(voice: PiperVoice, text: str) -> np.ndarray:
    chunks = [
        np.asarray(ch.audio_float_array, dtype=np.float32) for ch in voice.synthesize(text)
    ]
    pcm = np.concatenate(chunks)
    src = int(voice.config.sample_rate)
    if src != SR:
        x = np.arange(pcm.size, dtype=np.float64)
        n = int(round(pcm.size * SR / float(src)))
        xp = np.linspace(0, pcm.size - 1, max(n, 1))
        pcm = np.interp(xp, x, pcm).astype(np.float32)
    return pcm.astype(np.float32)


def _build() -> tuple[np.ndarray, list[float]]:
    """Return audio and reference cut times (end of each phrase except last)."""
    voice = PiperVoice.load(str(config.PIPER_VOICE_ZH))
    parts: list[np.ndarray] = []
    cuts: list[float] = []
    t = 0.0
    for i, text in enumerate(PHRASES):
        pcm = _synth(voice, text)
        parts.append(pcm)
        t += pcm.size / float(SR)
        if i < len(PHRASES) - 1:
            cuts.append(t + SILENCE_SEC / 2.0)  # mid-silence as reference cut
            parts.append(np.zeros(int(SILENCE_SEC * SR), dtype=np.float32))
            t += SILENCE_SEC
    audio = np.concatenate(parts)
    OUT_WAV.parent.mkdir(parents=True, exist_ok=True)
    pcm16 = (np.clip(audio, -1, 1) * 32767.0).astype(np.int16)
    with wave.open(str(OUT_WAV), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm16.tobytes())
    return audio, cuts


def _mae(cuts: list[float], ref: list[float]) -> float:
    assert len(cuts) == len(ref)
    return float(np.mean([abs(a - b) for a, b in zip(cuts, ref)]))


def main() -> int:
    audio, ref_cuts = _build()
    t0, t1 = 0.0, audio.size / float(SR)
    # Use equal-ish sentence strings so char arm is not accidentally perfect
    sents = list(PHRASES)

    char = _alloc_timestamps(sents, t0, t1)
    char_cuts = [char[i][1] for i in range(len(sents) - 1)]
    energy, mode = _alloc_timestamps_energy(sents, audio, t0, t1, SR)
    energy_cuts = [energy[i][1] for i in range(len(sents) - 1)]

    mae_a = _mae(char_cuts, ref_cuts)
    mae_b = _mae(energy_cuts, ref_cuts)
    print(f"ref_cuts={[round(x, 3) for x in ref_cuts]}")
    print(f"char_cuts={[round(x, 3) for x in char_cuts]}  MAE={mae_a:.3f}s")
    print(f"energy_cuts={[round(x, 3) for x in energy_cuts]}  MAE={mae_b:.3f}s  mode={mode}")

    ok = mae_b <= 0.60 * mae_a or mae_b <= 0.25
    print(
        f"GATE: energy MAE ≤ 0.60×char ({0.60 * mae_a:.3f}) OR ≤0.25 → "
        f"{'PASS' if ok else 'FAIL'}"
    )
    # src set unchanged: same sentences in order
    assert [s for *_r, s in char] == [s for *_r, s in energy]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
