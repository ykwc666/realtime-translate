"""A/B ENDPOINT_COMPLETION_MAX_ROUNDS=1 vs 2 on synthetic weekday wavs (no mic).

Usage:
  .\\.venv\\Scripts\\python.exe scripts\\ab_completion_rounds.py
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
from app.pipeline import TranslatePipeline  # noqa: E402
from piper import PiperVoice  # noqa: E402

OUT_DIR = ROOT / "cache" / "ab_weekday"
SR = int(config.SAMPLE_RATE)


def _synth_zh(voice: PiperVoice, text: str) -> np.ndarray:
    chunks: list[np.ndarray] = []
    src_sr = int(voice.config.sample_rate)
    for ch in voice.synthesize(text):
        chunks.append(np.asarray(ch.audio_float_array, dtype=np.float32))
    if not chunks:
        raise RuntimeError(f"empty synth: {text!r}")
    pcm = np.concatenate(chunks)
    if src_sr != SR:
        x = np.arange(pcm.size, dtype=np.float64)
        n = int(round(pcm.size * SR / float(src_sr)))
        xp = np.linspace(0, pcm.size - 1, max(n, 1))
        pcm = np.interp(xp, x, pcm).astype(np.float32)
    return pcm


def _write_wav(path: Path, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm16 = (np.clip(audio, -1, 1) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm16.tobytes())


def _build_fixtures(voice: PiperVoice) -> dict[str, Path]:
    a = _synth_zh(voice, "现在是星期")
    b = _synth_zh(voice, "二")
    pause = np.concatenate(
        [a, np.zeros(int(0.6 * SR), np.float32), b, np.zeros(int(1.2 * SR), np.float32)]
    )
    stop = np.concatenate(
        [_synth_zh(voice, "这是星期"), np.zeros(int(1.5 * SR), np.float32)]
    )
    cont = np.concatenate(
        [_synth_zh(voice, "现在是星期二"), np.zeros(int(1.0 * SR), np.float32)]
    )
    paths = {
        "pause_xingqi": OUT_DIR / "pause_xingqi_er.wav",
        "stop_xingqi": OUT_DIR / "stop_zheshi_xingqi.wav",
        "cont_tuesday": OUT_DIR / "cont_tuesday.wav",
    }
    _write_wav(paths["pause_xingqi"], pause)
    _write_wav(paths["stop_xingqi"], stop)
    _write_wav(paths["cont_tuesday"], cont)
    return paths


def _summarize(name: str, rounds: int, results: list) -> None:
    print(f"\n=== {name} rounds={rounds} utts={len(results)} ===")
    for i, r in enumerate(results, 1):
        wall = None if r.utt_wall_ms is None else round(r.utt_wall_ms)
        print(
            f"  utt{i}: src={r.source!r} tgt={r.translated!r} "
            f"mt={r.mt_triggers} wall={wall} reasons={r.mt_reasons}"
        )


def main() -> int:
    print("load Piper + translate pipeline…")
    voice = PiperVoice.load(str(config.PIPER_VOICE_ZH))
    paths = _build_fixtures(voice)
    print("fixtures →", OUT_DIR)
    pipe = TranslatePipeline(enable_tts=False)

    scores = {1: 0, 2: 0}
    walls: dict[int, list[float]] = {1: [], 2: []}

    for rounds in (1, 2):
        config.ENDPOINT_COMPLETION_MAX_ROUNDS = rounds
        for key, wav in paths.items():
            results = pipe.run_dualsim(str(wav), play=False)
            _summarize(key, rounds, results)
            joined = " ".join((r.source or "") for r in results)
            for r in results:
                if r.utt_wall_ms is not None:
                    walls[rounds].append(float(r.utt_wall_ms))
            if key == "pause_xingqi":
                if "星期二" in joined or joined.rstrip().endswith("二"):
                    scores[rounds] += 3
                elif "星期" in joined:
                    scores[rounds] += 0
            elif key == "cont_tuesday":
                if "星期二" in joined:
                    scores[rounds] += 2
                elif any(r.mt_triggers for r in results):
                    scores[rounds] += 1
            elif key == "stop_xingqi":
                if any(r.mt_triggers for r in results):
                    scores[rounds] += 1

    print("\n--- score (higher better) ---", scores)
    print(
        "--- mean wall ms ---",
        {k: (round(sum(v) / len(v), 1) if v else None) for k, v in walls.items()},
    )
    if scores[1] > scores[2]:
        pick = 1
    elif scores[2] > scores[1]:
        pick = 2
    else:
        # tie → prefer fewer rounds (shorter worst-case wait)
        pick = 1
    print(f"PICK MAX_ROUNDS={pick}")
    # write choice into a small stamp file for humans
    stamp = OUT_DIR / "pick.txt"
    stamp.write_text(f"ENDPOINT_COMPLETION_MAX_ROUNDS={pick}\nscore={scores}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
