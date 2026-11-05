"""A/B VAD_TEN_THRESHOLD under TEN (RMS floor left alone).

Same harness口径 as ab_vad_rms_floor.py (pad + force_hold_cut) — rescued is upper bound.

python scripts/ab_vad_ten_threshold.py
"""
from __future__ import annotations

import json
import sys
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
# Prefer AMI empty set from loop-fix stale_only detail (stable 16 empties)
DETAIL = ROOT / "docs" / "ab_loop_vs_stale_stale_only_detail.jsonl"
if not DETAIL.exists():
    DETAIL = ROOT / "docs" / "ledger_eval_detail_before_loop_fix.jsonl"
OUT = ROOT / "docs" / "vad_ten_threshold_ab.json"
OUT_MD = ROOT / "docs" / "VAD_TEN_THRESHOLD_AB.md"
SYNTH = [
    ROOT / "cache" / "ab_weekday" / "accept_phrases.wav",
    ROOT / "cache" / "ab_weekday" / "tail_duosimo.wav",
    ROOT / "cache" / "ab_weekday" / "pause_xingqi_er.wav",
]
THRESHOLDS = [0.5, 0.4, 0.3, 0.2]


def _pad(raw: np.ndarray) -> np.ndarray:
    return np.concatenate(
        [
            np.zeros(int(0.5 * SR), np.float32),
            np.asarray(raw, dtype=np.float32).reshape(-1),
            np.zeros(int(1.0 * SR), np.float32),
        ]
    )


def _noise_set() -> dict[str, np.ndarray]:
    n = int(3.0 * SR)
    rng = np.random.default_rng(0)
    silence = np.zeros(n, np.float32)
    white = (rng.normal(0, 0.008, n)).astype(np.float32)
    keys = np.zeros(n, np.float32)
    for t in rng.integers(0, n - 200, size=40):
        keys[t : t + 80] += rng.normal(0, 0.04, 80).astype(np.float32)
    return {"silence": silence, "white": white, "keyboard": keys}


def run_file(
    pipe: TranslatePipeline,
    audio: np.ndarray,
    *,
    force_src: str = "en",
) -> dict:
    engine = DualTrackEngine(
        pipe.asr,
        pipe.translator,
        None,
        play=False,
        force_src_lang=force_src,
        label="tenth",
    )
    opened = False
    utts = []
    for i in range(0, audio.size, BLOCK):
        if engine.endpoint.in_speech or engine.in_speech:
            opened = True
        fin = engine.feed_chunk(audio[i : i + BLOCK], allow_async_partial=False)
        if fin is not None:
            utts.append({"src": fin.source or "", "tgt": fin.translated or ""})
    if engine.in_speech and engine.buf.size or engine._holding_cut:
        pad = np.zeros(int(config.VAD_SILENCE_SEC * SR) + 1600, np.float32)
        for _ in range(40):
            fin = engine.feed_chunk(pad, allow_async_partial=False)
            if fin is not None:
                utts.append({"src": fin.source or "", "tgt": fin.translated or ""})
                break
        if engine._holding_cut:
            held = engine._force_hold_cut("tenth_flush")
            if held is not None:
                fin = engine._finalize_utt(held)
                if fin is not None:
                    utts.append({"src": fin.source or "", "tgt": fin.translated or ""})
    return {
        "opened": opened or bool(utts),
        "n_utts": len(utts),
        "srcs": [u["src"] for u in utts],
        "has_src": any((u["src"] or "").strip() for u in utts),
    }


def main() -> int:
    rows = [
        json.loads(l)
        for l in DETAIL.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    empty = [r for r in rows if (r.get("n_utts") or 0) == 0]
    normal = [
        r
        for r in rows
        if (r.get("n_utts") or 0) > 0 and r.get("pipeline_verdict") == "ok"
    ][:25]

    print("loading pipeline…")
    config.VAD_BACKEND = "ten"
    config.VAD_SPEECH_RMS_MIN = 0.035
    config.VAD_TEN_THRESHOLD = 0.5
    pipe = TranslatePipeline(enable_tts=False)
    noises = _noise_set()

    synth_base = {}
    for w in SYNTH:
        synth_base[w.name] = run_file(pipe, load_wav(str(w)), force_src="zh")["srcs"]

    results = []
    for thr in THRESHOLDS:
        name = f"ten_thr_{thr}"
        print(f"\n=== {name} ===")
        config.VAD_BACKEND = "ten"
        config.VAD_SPEECH_RMS_MIN = 0.035
        config.VAD_TEN_THRESHOLD = float(thr)

        rescued = 0
        rescue_ids = []
        for r in empty:
            out = run_file(pipe, _pad(load_wav(str(ROOT / r["path"]))), force_src="en")
            if out["has_src"]:
                rescued += 1
                rescue_ids.append(r["id"])

        normal_ok = 0
        for r in normal:
            out = run_file(pipe, _pad(load_wav(str(ROOT / r["path"]))), force_src="en")
            if out["has_src"] or out["n_utts"] > 0:
                normal_ok += 1

        false_open = 0
        false_detail = []
        for nk, audio in noises.items():
            out = run_file(pipe, audio, force_src="en")
            if out["opened"] or out["n_utts"] > 0:
                false_open += 1
                false_detail.append(nk)
                print(f"  FALSE OPEN on {nk}")

        synth_same = 0
        synth_no_loss = 0
        synth_detail = {}
        for w in SYNTH:
            out = run_file(pipe, load_wav(str(w)), force_src="zh")
            same = out["srcs"] == synth_base[w.name]
            # A split (one merged line becomes two clean lines) is an improvement,
            # not a regression. Only losing a sentence counts as damage.
            no_loss = len(out["srcs"]) >= len(synth_base[w.name])
            synth_detail[w.name] = {
                "srcs": out["srcs"],
                "n_srcs": len(out["srcs"]),
                "n_base": len(synth_base[w.name]),
                "same_as_baseline": same,
                "no_sentence_loss": no_loss,
            }
            if same:
                synth_same += 1
            if no_loss:
                synth_no_loss += 1

        # gates: normal_n is the size of the baseline-ok set (22), so the
        # requirement must be relative to it — an absolute 24 can never pass.
        gate_rescue = rescued >= 8
        gate_normal = normal_ok >= len(normal)
        gate_noise = false_open == 0
        gate_synth = synth_no_loss == len(SYNTH)
        gates_ok = gate_rescue and gate_normal and gate_noise and gate_synth

        row = {
            "candidate": name,
            "VAD_BACKEND": "ten",
            "VAD_TEN_THRESHOLD": thr,
            "VAD_SPEECH_RMS_MIN": 0.035,
            "empty_n": len(empty),
            "rescued": rescued,
            "rescue_rate": round(rescued / len(empty), 4) if empty else 0,
            "rescue_ids": rescue_ids,
            "normal_n": len(normal),
            "normal_ok": normal_ok,
            "false_open_noise": false_open,
            "false_open_detail": false_detail,
            "synth_same": synth_same,
            "synth_no_loss": synth_no_loss,
            "synth_n": len(SYNTH),
            "synth_detail": synth_detail,
            "gates": {
                "rescued_ge_8": gate_rescue,
                "normal_ok_full": gate_normal,
                "false_open_0": gate_noise,
                "synth_no_sentence_loss": gate_synth,
                "all_pass": gates_ok,
            },
            "harness_note": (
                "_pad=0.5s lead + 1.0s trail silence; ending may _force_hold_cut — "
                "rescued is upper bound, not live-mic capability"
            ),
        }
        results.append(row)
        print(
            f"  rescued={rescued}/{len(empty)} normal_ok={normal_ok}/{len(normal)} "
            f"false_open={false_open} synth_same={synth_same}/{len(SYNTH)} "
            f"synth_no_loss={synth_no_loss}/{len(SYNTH)} "
            f"gates_ok={gates_ok}"
        )

    # Prefer highest threshold that passes all gates (more conservative)
    best = None
    for r in results:
        if r["VAD_TEN_THRESHOLD"] == 0.5:
            continue
        if r["gates"]["all_pass"]:
            if best is None or r["VAD_TEN_THRESHOLD"] > best["VAD_TEN_THRESHOLD"]:
                best = r

    config.VAD_BACKEND = "ten"
    config.VAD_TEN_THRESHOLD = 0.5  # do not auto-apply; report only

    out = {
        "results": results,
        "recommend": best["candidate"] if best else None,
        "recommend_threshold": best["VAD_TEN_THRESHOLD"] if best else None,
        "recommend_reason": (
            f"highest thr that passes all gates: {best['VAD_TEN_THRESHOLD']} "
            f"rescue={best['rescued']}/{best['empty_n']}"
            if best
            else "no threshold met all four gates — keep VAD_TEN_THRESHOLD=0.5"
        ),
        "default_changed": None,
        "harness_caveat": (
            "rescued uses _pad + optional _force_hold_cut flush; upper bound only"
        ),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# VAD_TEN_THRESHOLD A/B（TEN 后端；不改 RMS）",
        "",
        "> **口径**：harness `_pad()` 补 0.5s 前导 + 1.0s 尾静音，结尾可 `_force_hold_cut` 强制 flush。"
        "因此 **rescued 是上限，不是实麦表现**。",
        "",
        "对照集：`ab_loop_vs_stale_stale_only_detail.jsonl` 中 n_utts=0 + pipeline ok 子集。",
        "",
        "| thr | rescued | normal_ok | false_open | synth_same | gates |",
        "|-----|---------|-----------|------------|------------|-------|",
    ]
    for r in results:
        g = "PASS" if r["gates"]["all_pass"] else "FAIL"
        lines.append(
            f"| {r['VAD_TEN_THRESHOLD']} | {r['rescued']}/{r['empty_n']} | "
            f"{r['normal_ok']}/{r['normal_n']} | {r['false_open_noise']} | "
            f"{r['synth_same']}/{r['synth_n']} | {g} |"
        )
    lines += [
        "",
        f"**推荐**：`{out['recommend']}` — {out['recommend_reason']}",
        "",
        f"rescue_ids 见 `{OUT.name}`。",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(
        json.dumps(
            {k: out[k] for k in ("recommend", "recommend_threshold", "recommend_reason")},
            indent=2,
        )
    )
    print(f"wrote {OUT}")
    print(f"wrote {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
