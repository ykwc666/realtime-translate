"""Phase A/B: VAD silence floor safety + read-only early-cut probe.

Does NOT change production defaults. Writes:
  docs/endpoint_silence_ab.jsonl
  docs/endpoint_early_cut_probe.jsonl

Usage:
  .venv\\Scripts\\python.exe scripts\\bench_endpoint_early_cut_ab.py
  .venv\\Scripts\\python.exe scripts\\bench_endpoint_early_cut_ab.py --phase a
  .venv\\Scripts\\python.exe scripts\\bench_endpoint_early_cut_ab.py --phase b
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402
from app.early_cut_probe import (  # noqa: E402
    classify_utt_bucket,
    compare_segmentations,
)
from app.pipeline import TranslatePipeline  # noqa: E402

import importlib.util  # noqa: E402

_bp_path = ROOT / "scripts" / "bench_paced.py"
_spec = importlib.util.spec_from_file_location("bench_paced", _bp_path)
assert _spec and _spec.loader
_bp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bp)
_agg = _bp._agg
_run_paced = _bp._run_paced

WAVS = [
    ROOT / "cache" / "ab_weekday" / "accept_phrases.wav",
    ROOT / "cache" / "ab_weekday" / "tail_duosimo.wav",
    ROOT / "cache" / "ab_weekday" / "pause_xingqi_er.wav",
    ROOT / "cache" / "ab_weekday" / "stop_zheshi_xingqi.wav",
    ROOT / "cache" / "ab_weekday" / "cont_tuesday.wav",
    ROOT / "cache" / "ab_weekday" / "latency_phrases_rt.wav",
]
BREATH = {
    "pause_xingqi_er.wav",
    "stop_zheshi_xingqi.wav",
    "cont_tuesday.wav",
}
TTS_SUBSET = {
    "accept_phrases.wav",
    "tail_duosimo.wav",
    "pause_xingqi_er.wav",
    "stop_zheshi_xingqi.wav",
}
SILENCES = (0.45, 0.40, 0.35)
OUT_AB = ROOT / "docs" / "endpoint_silence_ab.jsonl"
OUT_PROBE = ROOT / "docs" / "endpoint_early_cut_probe.jsonl"


def _srcs(rows: list[dict]) -> list[str]:
    return [str(r.get("src") or "") for r in rows]


def _metrics(rows: list[dict]) -> dict:
    def col(k: str) -> list[float]:
        return [float(r[k]) for r in rows if r.get(k) is not None]

    hits = sum(1 for r in rows if r.get("speculate_hit"))
    return {
        "n": len(rows),
        "srcs": _srcs(rows),
        "mt_ready": _agg(col("latency_perceived_ms")),
        "device_ttfa": _agg(col("latency_device_ttfa_ms")),
        "t_cut": _agg(col("t_cut_ms")),
        "asr_wait_after_cut": _agg(col("asr_wait_after_cut_ms")),
        "spec_hit": hits,
        "spec_hit_rate": round(hits / len(rows), 3) if rows else 0.0,
        "spec_counters": rows[0].get("spec_counters") if rows else None,
        "audio_thread": rows[0].get("audio_thread") if rows else None,
    }


def _run_one(
    pipe: TranslatePipeline,
    wav: Path,
    silence: float,
    *,
    play: bool,
    probe: bool,
    tag: str,
    rep: int,
) -> list[dict]:
    config.VAD_SILENCE_SEC = float(silence)
    config.ENDPOINT_EARLY_CUT_PROBE = bool(probe)
    # Restore defaults for everything else each run
    config.VAD_BACKEND = "silero"
    note = f"silence={silence} tts={'on' if play else 'off'} probe={'on' if probe else 'off'} rep={rep}"
    return _run_paced(
        pipe,
        f"{tag}-sil{silence}-r{rep}",
        wav,
        pace=1.0,
        note=note,
        play=play,
        close_tts=False,
    )


def _judge(wav_name: str, ref: list[str], cand: list[str], *, is_ref: bool) -> tuple[dict, bool]:
    if is_ref:
        cmp = compare_segmentations(cand, cand)
        cmp.update(
            {
                "merge": False,
                "split": False,
                "tail_loss": False,
                "text_mismatch": False,
                "count_delta": 0,
                "ref_srcs": list(cand),
                "cand_srcs": list(cand),
            }
        )
        return cmp, False
    cmp = compare_segmentations(ref, cand)
    unsafe = bool(
        cmp["merge"]
        or cmp["split"]
        or cmp["tail_loss"]
        or (
            wav_name in BREATH
            and (cmp["text_mismatch"] or cmp["count_delta"] != 0)
        )
    )
    return cmp, unsafe


def phase_a(pipe: TranslatePipeline, reps: int) -> list[dict]:
    """Fixed-threshold A/B; 0.45 is reference per wav. Interleaved silences."""
    records: list[dict] = []
    # heat-bias reduce: 0.45 → 0.40 → 0.35 → 0.35 → 0.40 → 0.45 when reps>=2
    sil_seq = list(SILENCES)
    if reps >= 2:
        sil_seq = list(SILENCES) + list(reversed(SILENCES))

    def _scan(*, play: bool, subset: set[str] | None) -> None:
        for wav in WAVS:
            if not wav.exists():
                print(f"skip missing {wav}")
                continue
            if subset is not None and wav.name not in subset:
                continue
            ref: list[str] | None = None
            # walk sil_seq; each element is one run (rep encoded by pass index)
            for idx, sil in enumerate(sil_seq):
                rep = 1 if idx < len(SILENCES) else 2
                if reps == 1 and idx >= len(SILENCES):
                    break
                mode = "on" if play else "off"
                print(f"\n## PhaseA tts={mode} {wav.name} sil={sil} rep={rep}")
                rows = _run_one(
                    pipe,
                    wav,
                    sil,
                    play=play,
                    probe=False,
                    tag=f"ab{'tts' if play else ''}-{wav.stem}",
                    rep=rep,
                )
                srcs = _srcs(rows)
                if sil == 0.45 and ref is None:
                    ref = list(srcs)
                    cmp, unsafe = _judge(wav.name, srcs, srcs, is_ref=True)
                else:
                    cmp, unsafe = _judge(
                        wav.name, ref or srcs, srcs, is_ref=False
                    )
                rec = {
                    "phase": "A",
                    "tts": play,
                    "wav": wav.name,
                    "silence": sil,
                    "rep": rep,
                    "unsafe": unsafe,
                    "cmp": cmp,
                    **_metrics(rows),
                }
                records.append(rec)
                print(
                    f"  => n={rec['n']} unsafe={unsafe} "
                    f"merge={cmp['merge']} split={cmp['split']} "
                    f"tail_loss={cmp['tail_loss']} mismatch={cmp['text_mismatch']} "
                    f"mt_med={(rec['mt_ready'] or {}).get('median')} "
                    f"dev_med={(rec['device_ttfa'] or {}).get('median')} "
                    f"srcs={rec['srcs']}"
                )

    _scan(play=False, subset=None)
    _scan(play=True, subset=TTS_SUBSET)
    return records


def phase_b(pipe: TranslatePipeline) -> tuple[list[dict], dict]:
    """Read-only probe at silence=0.45 (production floor)."""
    config.VAD_SILENCE_SEC = 0.45
    records: list[dict] = []
    # baseline thread cost without probe
    wav0 = next(w for w in WAVS if w.exists())
    config.ENDPOINT_EARLY_CUT_PROBE = False
    rows_off = _run_one(
        pipe, wav0, 0.45, play=False, probe=False, tag="probe-base-off", rep=1
    )
    thread_off = rows_off[0].get("audio_thread") if rows_off else None

    config.ENDPOINT_EARLY_CUT_PROBE = True
    rows_on = _run_one(
        pipe, wav0, 0.45, play=False, probe=True, tag="probe-base-on", rep=1
    )
    thread_on = rows_on[0].get("audio_thread") if rows_on else None

    buckets: dict[str, list[dict]] = defaultdict(list)

    for wav in WAVS:
        if not wav.exists():
            continue
        print(f"\n## PhaseB probe tts=off {wav.name}")
        rows = _run_one(
            pipe, wav, 0.45, play=False, probe=True, tag=f"probe-{wav.stem}", rep=1
        )
        for r in rows:
            probe = r.get("early_cut_probe") or {}
            audio_sec = float(r.get("audio_sec_final") or 0.0)
            bucket = classify_utt_bucket(audio_sec, wav.name)
            rec = {
                "phase": "B",
                "tts": False,
                "wav": wav.name,
                "utt": r.get("utt"),
                "src": r.get("src"),
                "audio_sec": audio_sec,
                "bucket": bucket,
                "t_cut_ms": r.get("t_cut_ms"),
                "t_asr_done_ms": r.get("t_asr_done_ms"),
                "asr_wait_after_cut_ms": r.get("asr_wait_after_cut_ms"),
                "speculate_hit": r.get("speculate_hit"),
                "mt_ready": r.get("latency_perceived_ms"),
                "device_ttfa": r.get("latency_device_ttfa_ms"),
                "probe": probe,
            }
            records.append(rec)
            buckets[bucket].append(probe)
            print(
                f"  utt{r.get('utt')} bucket={bucket} "
                f"safe350={probe.get('safe_350')} safe400={probe.get('safe_400')} "
                f"earliest={probe.get('earliest_safe')} src={r.get('src')!r}"
            )

    # TTS on for accept/tail/pause to attach device TTFA with probe
    for name in ("accept_phrases.wav", "tail_duosimo.wav", "pause_xingqi_er.wav"):
        wav = ROOT / "cache" / "ab_weekday" / name
        if not wav.exists():
            continue
        print(f"\n## PhaseB probe tts=on {wav.name}")
        rows = _run_one(
            pipe, wav, 0.45, play=True, probe=True, tag=f"probe-tts-{wav.stem}", rep=1
        )
        for r in rows:
            probe = r.get("early_cut_probe") or {}
            audio_sec = float(r.get("audio_sec_final") or 0.0)
            bucket = classify_utt_bucket(audio_sec, wav.name)
            rec = {
                "phase": "B",
                "tts": True,
                "wav": wav.name,
                "utt": r.get("utt"),
                "src": r.get("src"),
                "audio_sec": audio_sec,
                "bucket": bucket,
                "t_cut_ms": r.get("t_cut_ms"),
                "asr_wait_after_cut_ms": r.get("asr_wait_after_cut_ms"),
                "speculate_hit": r.get("speculate_hit"),
                "mt_ready": r.get("latency_perceived_ms"),
                "device_ttfa": r.get("latency_device_ttfa_ms"),
                "probe": probe,
            }
            records.append(rec)

    summary = {"thread_probe_off": thread_off, "thread_probe_on": thread_on, "groups": {}}
    for bucket, probes in buckets.items():
        n = len(probes)
        def rate(key: str) -> float:
            if n == 0:
                return 0.0
            return round(sum(1 for p in probes if p.get(key)) / n, 3)

        summary["groups"][bucket] = {
            "n": n,
            "ready_350": rate("ready_at_350"),
            "same_text_350": rate("same_text_at_350"),
            "resumed_350": rate("resumed_before_450"),
            "safe_350": rate("safe_350"),
            "safe_400": rate("safe_400"),
            "earliest_safe_hist": {
                str(k): sum(1 for p in probes if p.get("earliest_safe") == k)
                for k in (0.30, 0.35, 0.40, 0.45, None)
            },
        }
    return records, summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["a", "b", "all"], default="all")
    ap.add_argument("--reps", type=int, default=2)
    args = ap.parse_args()

    # Freeze defaults we must not leave dirty
    config.VAD_SILENCE_SEC = 0.45
    config.VAD_BACKEND = "silero"
    config.ENDPOINT_EARLY_CUT_PROBE = False
    config.TTS_PERSISTENT_OUTPUT = True
    config.FINALIZE_WAIT_TTS = False

    print("load models…")
    # Load once with TTS for both phases (play=False still ok if tts present)
    pipe = TranslatePipeline(enable_tts=True)

    ab_recs: list[dict] = []
    probe_recs: list[dict] = []
    probe_summary: dict = {}

    t0 = time.perf_counter()
    if args.phase in ("a", "all"):
        ab_recs = phase_a(pipe, reps=max(1, args.reps))
    if args.phase in ("b", "all"):
        probe_recs, probe_summary = phase_b(pipe)

    # restore defaults
    config.VAD_SILENCE_SEC = 0.45
    config.ENDPOINT_EARLY_CUT_PROBE = False

    if ab_recs:
        OUT_AB.parent.mkdir(parents=True, exist_ok=True)
        with OUT_AB.open("w", encoding="utf-8") as f:
            for r in ab_recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"wrote {len(ab_recs)} → {OUT_AB}")
    if probe_recs:
        with OUT_PROBE.open("w", encoding="utf-8") as f:
            for r in probe_recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.write(
                json.dumps(
                    {"summary": True, **probe_summary}, ensure_ascii=False
                )
                + "\n"
            )
        print(f"wrote {len(probe_recs)} → {OUT_PROBE}")
        print("probe groups:", json.dumps(probe_summary.get("groups", {}), ensure_ascii=False))
        print(
            "thread off/on:",
            probe_summary.get("thread_probe_off"),
            probe_summary.get("thread_probe_on"),
        )

    # Gate summary for Phase A
    if ab_recs:
        by_sil: dict[float, list] = defaultdict(list)
        for r in ab_recs:
            by_sil[float(r["silence"])].append(r)
        print("\n## Phase A unsafe counts")
        for sil in SILENCES:
            xs = by_sil[sil]
            bad = [x for x in xs if x.get("unsafe")]
            print(f"  sil={sil}: unsafe {len(bad)}/{len(xs)}")
            for x in bad[:8]:
                print(
                    f"    {x['wav']} tts={x['tts']} rep={x['rep']} "
                    f"cmp={ {k: x['cmp'][k] for k in ('merge','split','tail_loss','text_mismatch','count_delta')} }"
                )

    if pipe.tts is not None:
        try:
            pipe.tts.close()
        except Exception:  # noqa: BLE001
            pass
    print(f"\ndone in {(time.perf_counter() - t0) / 60:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
