"""Play an English wav on speakers while capturing WASAPI loopback → en→zh.

Usage:
  python scripts/probe_loopback.py
  python scripts/probe_loopback.py --wav cache/ab_weekday/loopback_en_probe.wav
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402
from app.asr_stream import StreamingAsr  # noqa: E402
from app.audio_io import LoopbackStream, load_wav  # noqa: E402
from app.dual_track import DualTrackEngine  # noqa: E402
from app.translate import Translator  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--wav",
        default=str(ROOT / "cache" / "ab_weekday" / "loopback_en_probe.wav"),
    )
    ap.add_argument("--listen-sec", type=float, default=8.0)
    args = ap.parse_args()
    wav = Path(args.wav)
    if not wav.exists():
        print(f"missing wav: {wav}")
        return 2

    audio = load_wav(str(wav))
    print(f"probe wav={wav} sec={audio.size / config.SAMPLE_RATE:.2f}")
    print("load ASR+MT…")
    asr = StreamingAsr()
    mt = Translator()
    engine = DualTrackEngine(
        asr, mt, tts=None, play=False, force_src_lang="en", label="loopback"
    )
    lb = LoopbackStream()
    results: list = []
    stop = threading.Event()

    def consume() -> None:
        for chunk in lb.chunks():
            if stop.is_set():
                break
            finished = engine.feed_chunk(chunk, mute_cb=None, allow_async_partial=False)
            if finished is not None:
                results.append(finished)

    lb.start()
    t = threading.Thread(target=consume, daemon=True)
    t.start()
    time.sleep(0.4)  # let capture warm
    print("playing English on default output (loopback should hear it)…")
    sd.play(audio, config.SAMPLE_RATE, blocking=True)
    # extra silence wall-time for VAD cut
    deadline = time.time() + max(2.0, args.listen_sec - audio.size / config.SAMPLE_RATE)
    while time.time() < deadline and not results:
        time.sleep(0.1)
    time.sleep(0.5)
    stop.set()
    lb.stop()
    t.join(timeout=2.0)

    if not results:
        print("LOOPBACK_PROBE_FAIL: no utterance (check default speaker=loopback device, volume)")
        return 1
    for i, r in enumerate(results, 1):
        print(f"utt{i}: src={r.source!r} tgt={r.translated!r} spec={r.speculate_hit}")
    ok = any(
        ("delay" in (r.source or "").lower())
        or ("多少" in (r.translated or ""))
        or ("延迟" in (r.translated or ""))
        or ("How" in (r.source or ""))
        for r in results
    )
    print("LOOPBACK_PROBE_OK" if ok else "LOOPBACK_PROBE_WEAK")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
