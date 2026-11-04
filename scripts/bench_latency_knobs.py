"""Realtime-paced dualsim A/B for silence/pad (no mic).

Feeds 100ms chunks every 100ms wall-clock so VAD_SILENCE_SEC counts like live.
"""
from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402
from app.dual_track import DualTrackEngine  # noqa: E402
from app.pipeline import TranslatePipeline  # noqa: E402
from app.audio_io import load_wav  # noqa: E402
from piper import PiperVoice  # noqa: E402

SR = int(config.SAMPLE_RATE)
OUT = ROOT / "cache" / "ab_weekday" / "latency_phrases_rt.wav"
BLOCK = int(SR * 0.1)


def _synth(voice: PiperVoice, text: str, tail_sil: float = 1.5) -> np.ndarray:
    chunks: list[np.ndarray] = []
    src = int(voice.config.sample_rate)
    for ch in voice.synthesize(text):
        chunks.append(np.asarray(ch.audio_float_array, dtype=np.float32))
    pcm = np.concatenate(chunks)
    if src != SR:
        x = np.arange(pcm.size, dtype=np.float64)
        n = int(round(pcm.size * SR / float(src)))
        xp = np.linspace(0, pcm.size - 1, max(n, 1))
        pcm = np.interp(xp, x, pcm).astype(np.float32)
    return np.concatenate([pcm, np.zeros(int(tail_sil * SR), np.float32)])


def _ensure_wav(voice: PiperVoice) -> Path:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    phrases = ["你好", "延迟大概有多少", "今天天气怎么样"]
    audio = np.concatenate([_synth(voice, p, 1.5) for p in phrases])
    pcm16 = (np.clip(audio, -1, 1) * 32767.0).astype(np.int16)
    with wave.open(str(OUT), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm16.tobytes())
    return OUT


def _run_paced(pipe: TranslatePipeline, label: str, silence: float, pad: float, complete_n: int):
    config.VAD_SILENCE_SEC = silence
    config.ONLINE_TAIL_PAD_SEC = pad
    config.VAD_FORCE_END_UNCHANGED_COMPLETE = complete_n

    audio = load_wav(str(OUT))
    engine = DualTrackEngine(pipe.asr, pipe.translator, pipe.tts, play=False)
    results = []
    t_wall0 = time.perf_counter()
    for i in range(0, audio.size, BLOCK):
        chunk = audio[i : i + BLOCK]
        if chunk.size == 0:
            break
        t_slot = t_wall0 + (i / SR)
        delay = t_slot - time.perf_counter()
        if delay > 0.001:
            time.sleep(delay)
        finished = engine.feed_chunk(chunk, mute_cb=None, allow_async_partial=False)
        if finished is not None:
            results.append(finished)

    # drain like dualsim
    if engine.in_speech and engine.buf.size:
        pad_audio = np.zeros(int(config.VAD_SILENCE_SEC * SR) + 1600, np.float32)
        for _ in range(12):
            finished = engine.feed_chunk(pad_audio, allow_async_partial=False)
            if finished is not None:
                results.append(finished)
                break

    print(f"\n##### {label} silence={silence} pad={pad} complete_n={complete_n} #####")
    walls, mts = [], []
    for i, r in enumerate(results, 1):
        print(
            f"  utt{i}: src={r.source!r} "
            f"first_mt={None if r.first_stable_mt_ms is None else round(r.first_stable_mt_ms)} "
            f"wall={None if r.utt_wall_ms is None else round(r.utt_wall_ms)}"
        )
        if r.utt_wall_ms is not None:
            walls.append(r.utt_wall_ms)
        if r.first_stable_mt_ms is not None:
            mts.append(r.first_stable_mt_ms)
    if walls:
        print(
            f"  mean_wall={round(sum(walls)/len(walls))} "
            f"mean_first_mt={round(sum(mts)/len(mts)) if mts else None} "
            f"utts={len(results)}"
        )
    # theoretical stop→cut floor
    print(f"  theory_silence_floor_ms={int(silence * 1000)}")


def main() -> int:
    print("build wav + load models…")
    voice = PiperVoice.load(str(config.PIPER_VOICE_ZH))
    _ensure_wav(voice)
    pipe = TranslatePipeline(enable_tts=False)
    _run_paced(pipe, "A_old", 0.80, 0.80, 3)
    _run_paced(pipe, "B_now", 0.60, 0.55, 2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
