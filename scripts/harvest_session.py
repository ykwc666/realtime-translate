"""Harvest finalized utterances to data/harvest/<date>-NNN.jsonl.

Live (user):
  python scripts/harvest_session.py --mode loopback --source-desc "meet 10min youtube"
  python run.py --mode loopback --harvest --source-desc "meet 10min youtube"

Smoke (wav as capture, no live devices):
  python scripts/harvest_session.py --mode file --file cache/ab_weekday/loopback_en_probe.wav ^
    --source-desc "smoke ab_weekday/loopback_en_probe" --no-tts

Writer uses a background queue (see app/harvest.py) so finalize never sync-writes.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402
from app.audio_io import load_wav  # noqa: E402
from app.dual_track import DualTrackEngine  # noqa: E402
from app.harvest import HarvestWriter, next_harvest_path  # noqa: E402
from app.pipeline import TranslatePipeline  # noqa: E402

SR = int(config.SAMPLE_RATE)
BLOCK = int(SR * 0.1)


def _run_file_smoke(
    pipe: TranslatePipeline,
    writer: HarvestWriter,
    wav: Path,
    *,
    play: bool,
    force_src: str | None,
) -> int:
    audio = load_wav(str(wav))
    engine = DualTrackEngine(
        pipe.asr,
        pipe.translator,
        pipe.tts,
        play=play,
        force_src_lang=force_src,
        label="harvest-file",
    )
    writer.attach_engine(engine)
    n = 0
    for i in range(0, audio.size, BLOCK):
        chunk = audio[i : i + BLOCK]
        if chunk.size == 0:
            break
        finished = engine.feed_chunk(chunk, mute_cb=None, allow_async_partial=False)
        if finished is not None:
            n += 1
    if engine.in_speech and engine.buf.size:
        pad = np.zeros(int(config.VAD_SILENCE_SEC * SR) + 1600, np.float32)
        for _ in range(24):
            finished = engine.feed_chunk(pad, allow_async_partial=False)
            if finished is not None:
                n += 1
                break
    if play:
        engine.wait_tts_idle(timeout=120.0)
    # allow writer thread to drain
    time.sleep(0.15)
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="会话采集 → data/harvest/*.jsonl")
    ap.add_argument(
        "--mode",
        choices=["loopback", "vad", "duplex", "file"],
        default="loopback",
    )
    ap.add_argument(
        "--source-desc",
        required=True,
        help="音源描述（写入每行 source_desc）",
    )
    ap.add_argument("--file", type=str, default="", help="file 冒烟：wav 路径")
    ap.add_argument(
        "--force-src",
        choices=["en", "zh", "auto"],
        default="en",
        help="file 模式源语言（loopback 默认 en）",
    )
    ap.add_argument("--no-tts", action="store_true")
    ap.add_argument(
        "--out",
        type=str,
        default="",
        help="可选：指定 jsonl 路径（默认 data/harvest/<date>-NNN.jsonl）",
    )
    args = ap.parse_args(argv)

    out = Path(args.out) if args.out else next_harvest_path()
    writer = HarvestWriter(out, mode=args.mode, source_desc=args.source_desc)
    try:
        if args.mode == "file":
            if not args.file:
                print("file 模式需要 --file path.wav", file=sys.stderr)
                return 2
            pipe = TranslatePipeline(enable_tts=not args.no_tts, harvest=None)
            force = None if args.force_src == "auto" else args.force_src
            _run_file_smoke(
                pipe, writer, Path(args.file), play=not args.no_tts, force_src=force
            )
            return 0

        from app.single_instance import acquire_run_lock

        if not acquire_run_lock(f"harvest-{args.mode}"):
            return 3
        pipe = TranslatePipeline(enable_tts=not args.no_tts, harvest=writer)
        if args.mode == "loopback":
            pipe.run_loopback()
        elif args.mode == "vad":
            pipe.run_vad()
        else:
            pipe.run_duplex()
        return 0
    finally:
        writer.close()


if __name__ == "__main__":
    raise SystemExit(main())
