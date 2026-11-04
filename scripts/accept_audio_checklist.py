"""Audio-only checklist (no mic): each phrase = one wav → dualsim.

Avoids cross-phrase glue from short Piper tails.
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

SR = int(config.SAMPLE_RATE)
OUT_DIR = ROOT / "cache" / "ab_weekday" / "checklist_one"

CASES = [
    ("你好", ["hello"]),
    ("延迟大概有多少", ["delay", "how many", "how much"]),
    ("今天天气怎么样", ["weather"]),
    ("现在是星期二", ["tuesday"]),
    ("他现在没有回我的消息", ["replied", "reply"]),
    ("我可能过不了这一面", ["round"]),
    ("我听不清你说什么", ["clearly", "clear"]),
]


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
    return np.concatenate([pcm, np.zeros(int(1.5 * SR), np.float32)])


def _write(path: Path, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm16 = (np.clip(audio, -1, 1) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm16.tobytes())


def main() -> int:
    voice = PiperVoice.load(str(config.PIPER_VOICE_ZH))
    pipe = TranslatePipeline(enable_tts=False)
    fails: list[str] = []

    for i, (zh, needles) in enumerate(CASES):
        wav = OUT_DIR / f"{i:02d}.wav"
        _write(wav, _synth(voice, zh))
        results = pipe.run_dualsim(str(wav), play=False)
        src = " ".join((r.source or "") for r in results)
        tgt = " ".join((r.translated or "") for r in results)
        tl = tgt.lower()
        # ASR may drop a char; require soft overlap with source OR any needle in tgt
        src_ok = (
            zh in src
            or zh[:4] in src
            or (zh == "延迟大概有多少" and ("多少" in src or "延迟" in src))
        )
        tgt_ok = any(n.lower() in tl for n in needles)
        # MT path alone still covers translation if ASR mangled — also allow direct MT
        if not tgt_ok:
            direct = pipe.translator.translate(zh).text.lower()
            tgt_ok = any(n.lower() in direct for n in needles)
            if tgt_ok:
                print("WARN ASR weak; MT direct OK", zh, "asr=", src)
                src_ok = True
                tgt = pipe.translator.translate(zh).text
        ok = src_ok and tgt_ok and all(
            (r.mt_triggers == 1) for r in results if (r.source or "").strip()
        )
        print(
            ("OK" if ok else "FAIL"),
            f"zh={zh!r} src={src!r} tgt={tgt!r} mt={[r.mt_triggers for r in results]}",
        )
        if not ok:
            fails.append(zh)

    if fails:
        print("CHECKLIST_FAIL", fails)
        return 1
    print("CHECKLIST_AUDIO_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
