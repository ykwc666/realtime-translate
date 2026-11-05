"""Phase C: VAD weak-signal candidates A/B (one at a time).

Candidates:
  baseline          silero thr=0.85
  silero_ thr0.5
  silero_thr0.3
  silero_thr0.2
  ten               VAD_BACKEND=ten
  gain_x3_vad_only  amplify chunk into VAD only (ASR sees original)
  gain_x4_vad_only

python scripts/ab_vad_weak_signal.py
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
DECOMP = ROOT / "docs" / "empty_utt_decompose.jsonl"
MANIFEST = ROOT / "data" / "ledger_corpus" / "manifest.jsonl"
OUT = ROOT / "docs" / "vad_weak_signal_ab.json"
SYNTH = [
    ROOT / "cache" / "ab_weekday" / "accept_phrases.wav",
    ROOT / "cache" / "ab_weekday" / "tail_duosimo.wav",
    ROOT / "cache" / "ab_weekday" / "pause_xingqi_er.wav",
]


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
    # keyboard-ish sparse clicks
    keys = np.zeros(n, np.float32)
    for t in rng.integers(0, n - 200, size=40):
        keys[t : t + 80] += rng.normal(0, 0.04, 80).astype(np.float32)
    return {"silence": silence, "white": white, "keyboard": keys}


def run_file(
    pipe: TranslatePipeline,
    audio: np.ndarray,
    *,
    force_src: str = "en",
    vad_gain: float = 1.0,
) -> dict:
    engine = DualTrackEngine(
        pipe.asr, pipe.translator, None, play=False, force_src_lang=force_src, label="wvad"
    )
    # Optional: gain only into VAD by wrapping endpoint.feed
    if vad_gain != 1.0:
        ep = engine.endpoint
        orig = ep.feed

        def feed_gained(chunk):  # noqa: ANN001
            c = np.asarray(chunk, dtype=np.float32).reshape(-1) * float(vad_gain)
            c = np.clip(c, -1.0, 1.0)
            return orig(c)

        ep.feed = feed_gained  # type: ignore

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
            held = engine._force_hold_cut("wvad_flush")
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


def apply_candidate(name: str) -> float:
    """Mutate config; return vad_gain for VAD-only amplify."""
    config.VAD_BACKEND = "silero"
    config.VAD_SILERO_THRESHOLD = 0.85
    gain = 1.0
    if name == "baseline":
        pass
    elif name == "silero_thr0.5":
        config.VAD_SILERO_THRESHOLD = 0.5
    elif name == "silero_thr0.3":
        config.VAD_SILERO_THRESHOLD = 0.3
    elif name == "silero_thr0.2":
        config.VAD_SILERO_THRESHOLD = 0.2
    elif name == "ten":
        config.VAD_BACKEND = "ten"
    elif name == "gain_x3_vad_only":
        gain = 3.0
    elif name == "gain_x4_vad_only":
        gain = 4.0
    else:
        raise ValueError(name)
    return gain


def main() -> int:
    decomp = [
        json.loads(l)
        for l in DECOMP.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    empty_never = [r for r in decomp if r["drop_reason"] == "vad_never_open"]
    # After Phase B, re-classify "normal" as previously produced
    normal = [r for r in decomp if r["drop_reason"] == "produced"][:25]
    man = {
        json.loads(l)["id"]: json.loads(l)
        for l in MANIFEST.read_text(encoding="utf-8").splitlines()
        if l.strip()
    }

    print("loading pipeline…")
    pipe = TranslatePipeline(enable_tts=False)
    noises = _noise_set()

    # Baseline synth texts for regression compare
    config.VAD_BACKEND = "silero"
    config.VAD_SILERO_THRESHOLD = 0.85
    synth_base = {}
    for w in SYNTH:
        r = run_file(pipe, load_wav(str(w)), force_src="zh", vad_gain=1.0)
        synth_base[w.name] = r["srcs"]

    candidates = [
        "baseline",
        "silero_thr0.5",
        "silero_thr0.3",
        "silero_thr0.2",
        "ten",
        "gain_x3_vad_only",
        "gain_x4_vad_only",
    ]
    results = []
    for name in candidates:
        print(f"\n=== {name} ===")
        gain = apply_candidate(name)
        # recreate is via DualTrackEngine each run_file (create_endpoint reads config)

        rescued = 0
        for r in empty_never:
            wav = ROOT / r["path"]
            out = run_file(pipe, _pad(load_wav(str(wav))), force_src="en", vad_gain=gain)
            if out["has_src"]:
                rescued += 1
        # normal: still produce something
        normal_ok = 0
        for r in normal:
            wav = ROOT / r["path"]
            out = run_file(pipe, _pad(load_wav(str(wav))), force_src="en", vad_gain=gain)
            if out["has_src"] or out["n_utts"] > 0:
                normal_ok += 1
        # false opens on noise
        false_open = 0
        for nk, audio in noises.items():
            out = run_file(pipe, audio, force_src="en", vad_gain=gain)
            if out["opened"] or out["n_utts"] > 0:
                false_open += 1
                print(f"  FALSE OPEN on {nk}")
        # synth text
        synth_same = 0
        synth_detail = {}
        for w in SYNTH:
            out = run_file(pipe, load_wav(str(w)), force_src="zh", vad_gain=gain)
            same = out["srcs"] == synth_base[w.name]
            synth_detail[w.name] = {"srcs": out["srcs"], "same_as_baseline": same}
            if same:
                synth_same += 1

        row = {
            "candidate": name,
            "vad_gain": gain,
            "VAD_BACKEND": config.VAD_BACKEND,
            "VAD_SILERO_THRESHOLD": config.VAD_SILERO_THRESHOLD,
            "empty_never_n": len(empty_never),
            "rescued": rescued,
            "rescue_rate": round(rescued / len(empty_never), 4) if empty_never else 0,
            "normal_n": len(normal),
            "normal_ok": normal_ok,
            "false_open_noise": false_open,
            "synth_same": synth_same,
            "synth_n": len(SYNTH),
            "synth_detail": synth_detail,
        }
        results.append(row)
        print(
            f"  rescued={rescued}/{len(empty_never)} normal_ok={normal_ok}/{len(normal)} "
            f"false_open={false_open} synth_same={synth_same}/{len(SYNTH)}"
        )

    # Decision: require rescue>0, false_open==0, synth_same==3, normal_ok>=normal_n
    best = None
    for r in results:
        if r["candidate"] == "baseline":
            continue
        if (
            r["rescued"] > 0
            and r["false_open_noise"] == 0
            and r["synth_same"] == len(SYNTH)
            and r["normal_ok"] >= len(normal)
        ):
            if best is None or r["rescued"] > best["rescued"]:
                best = r

    # restore defaults
    config.VAD_BACKEND = "silero"
    config.VAD_SILERO_THRESHOLD = 0.85

    out = {
        "results": results,
        "recommend": best["candidate"] if best else None,
        "recommend_reason": (
            f"best rescue={best['rescued']} with zero false_open + synth intact"
            if best
            else "no candidate met rescue>0 & false_open=0 & synth/normal intact — keep defaults"
        ),
        "keep_defaults": {
            "VAD_BACKEND": "silero",
            "VAD_SILERO_THRESHOLD": 0.85,
        },
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("recommend", "recommend_reason")}, indent=2))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
