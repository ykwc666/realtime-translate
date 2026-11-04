"""Acceptance for speculate re-arm modes (silence=0.45, no TTS).

Records asr_wait, mt_ready, spec cpu_ms. Supports ASR_SPECULATE_REARM_MODE sweep.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402
from app.pipeline import TranslatePipeline  # noqa: E402

_bp_path = ROOT / "scripts" / "bench_paced.py"
_spec = importlib.util.spec_from_file_location("bench_paced", _bp_path)
assert _spec and _spec.loader
_bp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bp)

WAVS = [
    "pause_xingqi_er.wav",
    "stop_zheshi_xingqi.wav",
    "cont_tuesday.wav",
    "accept_phrases.wav",
    "tail_duosimo.wav",
]


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[len(s) // 2]


def run_mode(pipe: TranslatePipeline, mode: str, reps: tuple[int, ...] = (1, 2)) -> list[dict]:
    config.ASR_SPECULATE_REARM_MODE = mode
    config.VAD_SILENCE_SEC = 0.45
    config.ENDPOINT_EARLY_CUT_PROBE = False
    out: list[dict] = []
    for name in WAVS:
        wav = ROOT / "cache" / "ab_weekday" / name
        if not wav.exists():
            print(f"missing {wav}")
            continue
        for rep in reps:
            print(f"\n## mode={mode} {name} rep={rep}")
            rows = _bp._run_paced(
                pipe,
                f"rearm-{mode}-{Path(name).stem}-r{rep}",
                wav,
                pace=1.0,
                note=f"rearm {mode} r{rep}",
                play=False,
                close_tts=False,
            )
            sc = rows[0].get("spec_counters") if rows else None
            waits = [
                float(r["asr_wait_after_cut_ms"])
                for r in rows
                if r.get("asr_wait_after_cut_ms") is not None
            ]
            mt_ready = [
                float(r["latency_perceived_ms"])
                for r in rows
                if r.get("latency_perceived_ms") is not None
            ]
            hits = sum(1 for r in rows if r.get("speculate_hit"))
            srcs = [r.get("src") for r in rows]
            rec = {
                "mode": mode,
                "wav": name,
                "rep": rep,
                "n": len(rows),
                "srcs": srcs,
                "hits": hits,
                "spec_counters": sc,
                "asr_wait_med": _median(waits),
                "asr_waits": waits,
                "mt_ready_med": _median(mt_ready),
                "mt_ready": mt_ready,
                "audio_thread": rows[0].get("audio_thread") if rows else None,
            }
            out.append(rec)
            print(
                f"  counters={sc} hits={hits}/{len(rows)} "
                f"asr_wait_med={rec['asr_wait_med']} "
                f"mt_ready_med={rec['mt_ready_med']} srcs={srcs}"
            )
    return out


def main() -> int:
    modes = ["offline", "full", "off"]
    if len(sys.argv) > 1:
        modes = [sys.argv[1]]
    print("load…")
    pipe = TranslatePipeline(enable_tts=False)
    all_rows: list[dict] = []
    for mode in modes:
        all_rows.extend(run_mode(pipe, mode))

    print("\n=== SUMMARY ===")
    for r in all_rows:
        sc = r["spec_counters"] or {}
        ath = r.get("audio_thread") or {}
        print(
            f"{r['mode']:7} {r['wav']:24} r{r['rep']} "
            f"starts={sc.get('starts')} restarts={sc.get('restarts')} "
            f"hits={sc.get('hits')} cancel={sc.get('cancel_speech')} "
            f"miss_grow={sc.get('miss_grow')} cpu_ms={sc.get('cpu_ms')} "
            f"asr_wait_med={r['asr_wait_med']} mt_ready_med={r['mt_ready_med']} "
            f"ath_max={ath.get('max_ms')} over100={ath.get('over_100ms')} "
            f"srcs={r['srcs']}"
        )
    path = ROOT / "docs" / "spec_rearm_accept.json"
    path.write_text(json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
