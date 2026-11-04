"""P0-3 A/B: FINALIZE_WAIT_TTS on vs off — time until finalize returns (with TTS).

Measures feed→finished return wall while Piper actually plays. With wait=on,
utt_wall / return includes playback; with wait=off, return after MT enqueue.
"""
from __future__ import annotations

import json
import statistics
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
from app.pipeline import TranslatePipeline  # noqa: E402

SR = int(config.SAMPLE_RATE)
BLOCK = int(SR * 0.1)
WAV = ROOT / "cache" / "ab_weekday" / "tail_duosimo.wav"


def _run(wait_tts: bool) -> list[dict]:
    config.FINALIZE_WAIT_TTS = wait_tts
    config.ASR_2PASS_PARALLEL = True
    config.ASR_THREADS_ONLINE = 3
    config.ASR_THREADS_OFFLINE = 2
    config.ASR_SPECULATE_ADAPTIVE_LEAD = True
    config.VAD_SILENCE_SEC = 0.45
    pipe = TranslatePipeline(enable_tts=True)
    engine = DualTrackEngine(pipe.asr, pipe.translator, pipe.tts, play=True)
    audio = load_wav(str(WAV))
    rows: list[dict] = []
    t0 = time.perf_counter()
    for i in range(0, audio.size, BLOCK):
        chunk = audio[i : i + BLOCK]
        c0 = time.perf_counter()
        finished = engine.feed_chunk(chunk, mute_cb=None, allow_async_partial=False)
        feed_ms = (time.perf_counter() - c0) * 1000
        if finished is not None:
            rows.append(
                {
                    "wait_tts": wait_tts,
                    "src": finished.source,
                    "tgt": finished.translated,
                    "utt_wall_ms": finished.utt_wall_ms,
                    "perceived_ms": finished.latency_perceived_ms,
                    "feed_return_ms": round(feed_ms, 1),
                    "t_mt_done_ms": finished.t_mt_done_ms,
                }
            )
            print(
                f"  wait={wait_tts} src={finished.source!r} "
                f"utt_wall={finished.utt_wall_ms and round(finished.utt_wall_ms)} "
                f"feed_ret={feed_ms:.0f} perceived={finished.latency_perceived_ms and round(finished.latency_perceived_ms)}"
            )
    # drain
    if engine.in_speech and engine.buf.size:
        pad = np.zeros(int(config.VAD_SILENCE_SEC * SR) + 1600, np.float32)
        for _ in range(20):
            finished = engine.feed_chunk(pad, allow_async_partial=False)
            if finished is not None:
                rows.append(
                    {
                        "wait_tts": wait_tts,
                        "src": finished.source,
                        "tgt": finished.translated,
                        "utt_wall_ms": finished.utt_wall_ms,
                        "perceived_ms": finished.latency_perceived_ms,
                        "feed_return_ms": None,
                        "t_mt_done_ms": finished.t_mt_done_ms,
                    }
                )
                break
    # let async TTS finish so next mode is clean
    engine.tts_q.wait_idle(timeout=30)
    print(f"  elapsed_wall={(time.perf_counter()-t0):.1f}s n={len(rows)}")
    return rows


def main() -> int:
    if not WAV.exists():
        print(f"missing {WAV}")
        return 2
    print("warmup pipeline once…")
    # two separate runs (ASR init each time — thread pool / wait flag at construct)
    all_rows: list[dict] = []
    for wait in (True, False):
        print(f"\n===== FINALIZE_WAIT_TTS={wait} =====")
        all_rows.extend(_run(wait))

    def _summ(flag: bool) -> dict:
        xs = [r for r in all_rows if r["wait_tts"] is flag and r.get("utt_wall_ms")]
        walls = [float(r["utt_wall_ms"]) for r in xs]
        feeds = [
            float(r["feed_return_ms"])
            for r in xs
            if r.get("feed_return_ms") is not None
        ]
        return {
            "n": len(xs),
            "utt_wall_median": round(statistics.median(walls), 1) if walls else None,
            "utt_wall_mean": round(statistics.mean(walls), 1) if walls else None,
            "feed_ret_median": round(statistics.median(feeds), 1) if feeds else None,
            "srcs": [r["src"] for r in xs],
        }

    on = _summ(True)
    off = _summ(False)
    print("\n## summary")
    print(f"wait=on  {on}")
    print(f"wait=off {off}")
    if on["utt_wall_median"] and off["utt_wall_median"]:
        print(
            f"utt_wall median delta (on-off) = "
            f"{on['utt_wall_median'] - off['utt_wall_median']:+.0f} ms"
        )
    out = ROOT / "docs" / "finalize_wait_ab.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for r in all_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        f.write(
            json.dumps({"summary_on": on, "summary_off": off}, ensure_ascii=False)
            + "\n"
        )
    print(f"wrote {out}")
    config.FINALIZE_WAIT_TTS = True
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
