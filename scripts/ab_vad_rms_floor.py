"""A/B VAD_SPEECH_RMS_MIN under TEN (current default).

Empty clips are blocked by the soft RMS gate even when TEN detects speech.
Candidates keep VAD_BACKEND=ten; only vary VAD_SPEECH_RMS_MIN.

python scripts/ab_vad_rms_floor.py
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
DETAIL = ROOT / "docs" / "ledger_eval_detail.jsonl"
OUT = ROOT / "docs" / "vad_rms_floor_ab.json"
OUT_MD = ROOT / "docs" / "VAD_RMS_FLOOR_AB.md"
SYNTH = [
    ROOT / "cache" / "ab_weekday" / "accept_phrases.wav",
    ROOT / "cache" / "ab_weekday" / "tail_duosimo.wav",
    ROOT / "cache" / "ab_weekday" / "pause_xingqi_er.wav",
]
# TEN default; floors to try (0.035 = current)
FLOORS = [0.035, 0.02, 0.015, 0.01, 0.005, 0.0]


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
        label="rms",
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
            held = engine._force_hold_cut("rms_flush")
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
    # Restore production TEN defaults as AB baseline
    config.VAD_BACKEND = "ten"
    config.VAD_SPEECH_RMS_MIN = 0.035
    pipe = TranslatePipeline(enable_tts=False)
    noises = _noise_set()

    synth_base = {}
    for w in SYNTH:
        synth_base[w.name] = run_file(pipe, load_wav(str(w)), force_src="zh")["srcs"]

    results = []
    for floor in FLOORS:
        name = f"rms_{floor}"
        print(f"\n=== {name} ===")
        config.VAD_BACKEND = "ten"
        config.VAD_SPEECH_RMS_MIN = float(floor)

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
        synth_detail = {}
        for w in SYNTH:
            out = run_file(pipe, load_wav(str(w)), force_src="zh")
            same = out["srcs"] == synth_base[w.name]
            synth_detail[w.name] = {"srcs": out["srcs"], "same_as_baseline": same}
            if same:
                synth_same += 1

        row = {
            "candidate": name,
            "VAD_BACKEND": "ten",
            "VAD_SPEECH_RMS_MIN": floor,
            "empty_n": len(empty),
            "rescued": rescued,
            "rescue_rate": round(rescued / len(empty), 4) if empty else 0,
            "rescue_ids": rescue_ids,
            "normal_n": len(normal),
            "normal_ok": normal_ok,
            "false_open_noise": false_open,
            "false_open_detail": false_detail,
            "synth_same": synth_same,
            "synth_n": len(SYNTH),
            "synth_detail": synth_detail,
        }
        results.append(row)
        print(
            f"  rescued={rescued}/{len(empty)} normal_ok={normal_ok}/{len(normal)} "
            f"false_open={false_open} synth_same={synth_same}/{len(SYNTH)}"
        )

    # Prefer max rescue with false_open==0, synth intact, normal_ok full
    best = None
    for r in results:
        if r["VAD_SPEECH_RMS_MIN"] == 0.035:
            continue
        if (
            r["rescued"] > 0
            and r["false_open_noise"] == 0
            and r["synth_same"] == len(SYNTH)
            and r["normal_ok"] >= len(normal)
        ):
            if best is None or r["rescued"] > best["rescued"]:
                best = r
            elif best is not None and r["rescued"] == best["rescued"]:
                # prefer higher floor (safer) when tie
                if r["VAD_SPEECH_RMS_MIN"] > best["VAD_SPEECH_RMS_MIN"]:
                    best = r

    # restore production config values used for this session baseline
    config.VAD_BACKEND = "ten"
    config.VAD_SPEECH_RMS_MIN = (
        float(best["VAD_SPEECH_RMS_MIN"]) if best else 0.035
    )

    out = {
        "results": results,
        "recommend": best["candidate"] if best else None,
        "recommend_rms": best["VAD_SPEECH_RMS_MIN"] if best else None,
        "recommend_reason": (
            f"best rescue={best['rescued']}/{best['empty_n']} "
            f"false_open=0 synth_same={best['synth_same']} "
            f"normal_ok={best['normal_ok']}/{best['normal_n']}"
            if best
            else "no candidate met gates — keep VAD_SPEECH_RMS_MIN=0.035"
        ),
        "default_changed": (
            {
                "VAD_SPEECH_RMS_MIN": best["VAD_SPEECH_RMS_MIN"],
                "evidence": "docs/vad_rms_floor_ab.json",
            }
            if best
            else None
        ),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# VAD_SPEECH_RMS_MIN A/B（TEN 后端）",
        "",
        "对照集：当前 `ledger_eval_detail.jsonl` 中 `n_utts=0` 空检 + pipeline ok 子集 + 噪声/合成。",
        "",
        "| floor | rescued | normal_ok | false_open | synth_same |",
        "|-------|---------|-----------|------------|------------|",
    ]
    for r in results:
        lines.append(
            f"| {r['VAD_SPEECH_RMS_MIN']} | {r['rescued']}/{r['empty_n']} | "
            f"{r['normal_ok']}/{r['normal_n']} | {r['false_open_noise']} | "
            f"{r['synth_same']}/{r['synth_n']} |"
        )
    lines += [
        "",
        f"**推荐**：`{out['recommend']}` — {out['recommend_reason']}",
        "",
        f"落盘：`{OUT.as_posix()}`",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("recommend", "recommend_rms", "recommend_reason")}, indent=2))
    print(f"wrote {OUT}")
    print(f"wrote {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
