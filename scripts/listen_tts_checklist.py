"""Human listening checklist for persistent TTS OutputStream.

Plays through the default/output device with clear prompts::

    .venv/Scripts/python.exe scripts/listen_tts_checklist.py
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402
from app.tts_play import TtsPlayer  # noqa: E402


def _pause(sec: float, label: str) -> None:
    print(f"  … {label} ({sec:.1f}s)")
    time.sleep(sec)


def main() -> int:
    config.TTS_PERSISTENT_OUTPUT = True
    config.TTS_STREAM_PLAY = True
    tts = TtsPlayer()
    print(f"device={tts.output_device}  persistent={config.TTS_PERSISTENT_OUTPUT}")
    print("warmup…")
    print(tts.warmup())
    _pause(0.8, "ready")

    print("\n=== 1/4 连续两句英文（听：无残留、无串句）===")
    for text in (
        "Hello, this is the first sentence.",
        "And this is the second sentence right after.",
    ):
        print(f"  >> {text}")
        syn, play = tts.speak(text, "en", save_wav=False)
        pt = syn.play_timing
        print(
            f"     pcm={syn.ttfa_pcm_ms:.0f} cb={getattr(pt, 'ttfa_callback_ms', 0):.0f} "
            f"open={getattr(pt, 'stream_open_ms', 0):.0f} "
            f"reused={getattr(pt, 'stream_reused', False)} play={play:.2f}s"
        )
        _pause(0.35, "gap")

    print("\n=== 2/4 尾音节 Tuesday（听：-day 是否被切）===")
    text = "See you on Tuesday."
    print(f"  >> {text}")
    syn, play = tts.speak(text, "en", save_wav=False)
    print(f"     play={play:.2f}s (expect full Tuesday)")
    _pause(0.6, "gap")

    print("\n=== 3/4 多子句长句（听：逗号后是否很快出声）===")
    text = "Hang on a second, I'll be right back after I check the meeting notes."
    print(f"  >> {text}")
    syn, play = tts.speak(text, "en", save_wav=False)
    pt = syn.play_timing
    print(
        f"     pcm={syn.ttfa_pcm_ms:.0f} cb={getattr(pt, 'ttfa_callback_ms', 0):.0f} "
        f"play={play:.2f}s"
    )
    _pause(0.6, "gap")

    print("\n=== 4/4 打断（听：约 0.3s 后应迅速静音）===")
    text = (
        "This is a long utterance that should be interrupted in the middle "
        "before you hear the ending."
    )
    print(f"  >> {text}")
    done = threading.Event()
    played = [0.0]

    def run() -> None:
        _syn, played[0] = tts.speak(text, "en", save_wav=False)
        done.set()

    th = threading.Thread(target=run, daemon=True)
    th.start()
    time.sleep(0.35)
    print("  !! stop()")
    t0 = time.perf_counter()
    tts.stop()
    done.wait(timeout=3.0)
    print(
        f"     stop_return={(time.perf_counter() - t0) * 1000:.0f}ms "
        f"played={played[0]:.2f}s (should be << full)"
    )

    _pause(0.4, "recover")
    print("\n=== 打断后恢复播一句 ===")
    text = "Playback recovered after interrupt."
    print(f"  >> {text}")
    tts.speak(text, "en", save_wav=False)

    print("\n=== 中文抽查 ===")
    for text in ("稍等一下，我马上回来。", "我可能过不了这一面"):
        print(f"  >> {text}")
        tts.speak(text, "zh", save_wav=False)
        _pause(0.3, "gap")

    tts.close()
    print("\nDone. Tell me: 串句? 切尾? 首音早晚? 打断是否干净?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
