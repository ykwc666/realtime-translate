"""Read-only diagnose: why pause_xingqi miss_grow without cancel_speech.

Does not change production defaults. Trace gated by this script only.

  .venv\\Scripts\\python.exe scripts\\diag_spec_rearm.py
"""
from __future__ import annotations

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
WAVS = [
    ROOT / "cache" / "ab_weekday" / "pause_xingqi_er.wav",
    ROOT / "cache" / "ab_weekday" / "cont_tuesday.wav",
]


def _rms(chunk: np.ndarray) -> float:
    c = np.asarray(chunk, dtype=np.float32).reshape(-1)
    if c.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(c), dtype=np.float64)))


def _instrument(engine: DualTrackEngine, rows: list[dict]) -> None:
    """Monkey-patch for one run; restores nothing (engine discarded after)."""
    t0 = time.perf_counter()
    orig_feed = engine.feed_chunk
    orig_maybe = engine._maybe_speculate
    orig_clear = engine._clear_speculate
    call_n = {"maybe": 0, "clear_speech": 0, "start": 0}

    def _spec_state() -> str:
        job = engine._spec
        if job is None:
            return "idle"
        if job.get("done") and job["done"].is_set():
            return "done"
        return "running"

    def clear_wrap(*, reason: str = "") -> None:
        if engine._spec is not None and reason == "speech":
            call_n["clear_speech"] += 1
            print(
                f"  !! CLEAR speech utt={engine._utt_id} "
                f"t={(time.perf_counter()-t0)*1000:.0f}ms "
                f"vad={getattr(engine.endpoint,'trailing_silence_sec',None)} "
                f"eng={engine._engine_silence_sec:.3f} "
                f"trail={engine._trailing_silence_sec():.3f}"
            )
        return orig_clear(reason=reason)

    def maybe_wrap() -> None:
        call_n["maybe"] += 1
        before = engine._spec
        before_n = int(before["n"]) if before else None
        sil = engine._trailing_silence_sec()
        min_sil = float(getattr(config, "ASR_SPECULATE_MIN_SILENCE_SEC", 0.08))
        would_clear = sil < min_sil and before is not None
        orig_maybe()
        after = engine._spec
        if before is None and after is not None:
            call_n["start"] += 1
            print(
                f"  !! SPEC_START utt={engine._utt_id} "
                f"t={(time.perf_counter()-t0)*1000:.0f}ms "
                f"sil={sil:.3f} n0={after.get('n')} "
                f"audio_sec={engine.audio_sec:.2f}"
            )
        elif would_clear and after is None:
            print(
                f"  !! SPEC_CLEARED_BY_MAYBE t={(time.perf_counter()-t0)*1000:.0f}ms "
                f"sil={sil:.3f} old_n={before_n}"
            )

    def feed_wrap(chunk, mute_cb=None, allow_async_partial=True):  # noqa: ANN001
        chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
        # Pre-state (before feed mutates)
        line = {
            "t_ms": round((time.perf_counter() - t0) * 1000, 1),
            "chunk_rms": round(_rms(chunk), 5),
            "in_speech": bool(engine.in_speech),
            "ep_in_speech": bool(getattr(engine.endpoint, "in_speech", False)),
            "holding_cut": bool(engine._holding_cut),
            "vad_sil": round(
                float(getattr(engine.endpoint, "trailing_silence_sec", 0.0) or 0.0), 3
            ),
            "engine_sil": round(float(engine._engine_silence_sec), 3),
            "trail_sil": round(float(engine._trailing_silence_sec()), 3)
            if engine.in_speech
            else 0.0,
            "spec_state": _spec_state(),
            "spec_n0": int(engine._spec["n"]) if engine._spec else None,
            "maybe_calls_before": call_n["maybe"],
            "buf_n": int(engine.buf.size),
        }
        finished = orig_feed(
            chunk, mute_cb=mute_cb, allow_async_partial=allow_async_partial
        )
        line["maybe_calls_delta"] = call_n["maybe"] - line["maybe_calls_before"]
        line["spec_state_after"] = _spec_state()
        line["holding_after"] = bool(engine._holding_cut)
        line["engine_sil_after"] = round(float(engine._engine_silence_sec), 3)
        line["vad_sil_after"] = round(
            float(getattr(engine.endpoint, "trailing_silence_sec", 0.0) or 0.0), 3
        )
        rows.append(line)
        # Print compact every chunk around interesting events, or always for short wavs
        print(
            f"utt={engine._utt_id} t={line['t_ms']:7.0f} rms={line['chunk_rms']:.4f} "
            f"in={int(line['in_speech'])} ep={int(line['ep_in_speech'])} "
            f"hold={int(line['holding_cut'])}->{int(line['holding_after'])} "
            f"vad={line['vad_sil']:.3f}->{line['vad_sil_after']:.3f} "
            f"eng={line['engine_sil']:.3f}->{line['engine_sil_after']:.3f} "
            f"spec={line['spec_state']}->{line['spec_state_after']} "
            f"n0={line['spec_n0']} maybe+{line['maybe_calls_delta']} buf={line['buf_n']}"
        )
        return finished

    engine._clear_speculate = clear_wrap  # type: ignore[method-assign]
    engine._maybe_speculate = maybe_wrap  # type: ignore[method-assign]
    engine.feed_chunk = feed_wrap  # type: ignore[method-assign]
    engine._diag_call_n = call_n  # type: ignore[attr-defined]


def run_wav(pipe: TranslatePipeline, wav: Path) -> None:
    print(f"\n======== {wav.name} ========")
    config.VAD_SILENCE_SEC = 0.45
    config.ENDPOINT_EARLY_CUT_PROBE = False
    audio = load_wav(str(wav))
    engine = DualTrackEngine(pipe.asr, pipe.translator, None, play=False)
    rows: list[dict] = []
    _instrument(engine, rows)
    for i in range(0, audio.size, BLOCK):
        chunk = audio[i : i + BLOCK]
        if chunk.size == 0:
            break
        finished = engine.feed_chunk(chunk, allow_async_partial=False)
        if finished is not None:
            print(
                f"  FINAL src={finished.source!r} hit={finished.speculate_hit} "
                f"counters={engine.spec_counters}"
            )
    if engine.in_speech and engine.buf.size:
        pad = np.zeros(int(config.VAD_SILENCE_SEC * SR) + 1600, np.float32)
        for _ in range(16):
            finished = engine.feed_chunk(pad, allow_async_partial=False)
            if finished is not None:
                print(
                    f"  FINAL src={finished.source!r} hit={finished.speculate_hit} "
                    f"counters={engine.spec_counters}"
                )
                break
    print(f"  diag_calls={getattr(engine, '_diag_call_n', None)}")
    # Highlight resume candidates: high rms while spec running
    print("  --- resume candidates (rms>=0.01 while spec running/done) ---")
    for r in rows:
        if r["spec_state"] in ("running", "done") and r["chunk_rms"] >= 0.01:
            print(
                f"  t={r['t_ms']} rms={r['chunk_rms']} hold={r['holding_cut']} "
                f"vad={r['vad_sil']} eng={r['engine_sil']} trail={r['trail_sil']} "
                f"maybe+{r['maybe_calls_delta']} after_eng={r['engine_sil_after']}"
            )


def main() -> int:
    print("load models…")
    pipe = TranslatePipeline(enable_tts=False)
    for wav in WAVS:
        if not wav.exists():
            print(f"missing {wav}")
            continue
        run_wav(pipe, wav)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
