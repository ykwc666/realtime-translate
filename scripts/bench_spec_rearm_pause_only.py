"""Quick pause_xingqi_er A/B for rearm modes (2 reps each)."""
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

WAV = ROOT / "cache" / "ab_weekday" / "pause_xingqi_er.wav"


def main() -> int:
    print("load…")
    pipe = TranslatePipeline(enable_tts=False)
    config.VAD_SILENCE_SEC = 0.45
    config.ENDPOINT_EARLY_CUT_PROBE = False
    out = []
    for mode in ("offline", "full", "off"):
        config.ASR_SPECULATE_REARM_MODE = mode
        for rep in (1, 2):
            print(f"\n## {mode} r{rep}")
            rows = _bp._run_paced(
                pipe,
                f"pause-{mode}-r{rep}",
                WAV,
                pace=1.0,
                note=mode,
                play=False,
                close_tts=False,
            )
            r = rows[0]
            sc = r.get("spec_counters") or {}
            wait = r.get("asr_wait_after_cut_ms")
            mt = r.get("latency_perceived_ms")
            ath = r.get("audio_thread") or {}
            rec = {
                "mode": mode,
                "rep": rep,
                "src": r.get("src"),
                "spec_counters": sc,
                "asr_wait": wait,
                "mt_ready": mt,
                "ath_max": ath.get("max_ms"),
                "over_100": ath.get("over_100ms"),
                "hit": r.get("speculate_hit"),
            }
            out.append(rec)
            print(
                f"  wait={wait} mt={mt} cpu={sc.get('cpu_ms')} "
                f"hit={rec['hit']} ath_max={rec['ath_max']} src={rec['src']!r}"
            )
    path = ROOT / "docs" / "spec_rearm_pause_modes.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
