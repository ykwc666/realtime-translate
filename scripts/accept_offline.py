"""One-shot offline acceptance for frozen baseline (no mic).

Runs: pytest endpoint/mt → MT smoke → dualsim phrase wav → latency A/B summary.
"""
from __future__ import annotations

import subprocess
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
from app.translate import Translator  # noqa: E402
from piper import PiperVoice  # noqa: E402

SR = int(config.SAMPLE_RATE)
PHRASE_WAV = ROOT / "cache" / "ab_weekday" / "accept_phrases.wav"


def _run(cmd: list[str]) -> None:
    print("\n>>", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))


def _synth_phrases() -> Path:
    voice = PiperVoice.load(str(config.PIPER_VOICE_ZH))
    phrases = [
        "你好",
        "延迟大概有多少",
        "今天天气怎么样",
        "现在是星期二",
        "他现在没有回我的消息",
    ]

    def one(text: str) -> np.ndarray:
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
        return np.concatenate([pcm, np.zeros(int(1.3 * SR), np.float32)])

    audio = np.concatenate([one(p) for p in phrases])
    PHRASE_WAV.parent.mkdir(parents=True, exist_ok=True)
    pcm16 = (np.clip(audio, -1, 1) * 32767.0).astype(np.int16)
    with wave.open(str(PHRASE_WAV), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm16.tobytes())
    return PHRASE_WAV


def main() -> int:
    py = sys.executable
    _run(
        [
            py,
            "-m",
            "pytest",
            "tests/test_weekday_whitelist.py",
            "tests/test_endpoint_regression.py",
            "tests/test_mt_postedit.py",
            "-q",
        ]
    )

    print("\n>> MT smoke")
    tr = Translator()
    expect = {
        "他现在没有回我的消息": "replied",
        "我可能过不了这一面": "round",
        "我听不清你说什么": "clearly",
        "今天天气怎么样": "weather",
        "延迟大概有多少": "delay",
    }
    for zh, needle in expect.items():
        hyp = tr.translate(zh).text
        ok = needle.lower() in hyp.lower()
        print(("OK" if ok else "FAIL"), zh, "=>", hyp)
        if not ok:
            return 1

    print("\n>> dualsim phrases")
    wav = _synth_phrases()
    pipe = TranslatePipeline(enable_tts=False)
    # reuse already-loaded translator/asr from new pipeline — fine
    results = pipe.run_dualsim(str(wav), play=False)
    for r in results:
        print("utt", r.source, "|", r.translated)
    if len(results) < 3:
        print("FAIL too few utts", len(results))
        return 1

    print("\nACCEPT_OFFLINE_OK")
    print("mt_ct2=", Path(config.OPUS_ZH_EN_CT2).name)
    print("silence=", config.VAD_SILENCE_SEC, "pad=", config.ONLINE_TAIL_PAD_SEC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
