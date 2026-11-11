"""Build GTCRN-denoised copies of ledger ami/fleurs (+ optional dialogue RMS).

Must set config.MIC_DENOISE=True before MicDenoiser(); abort if not enabled.
Does not change production default (caller restores / process exits).
"""

from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402
from app.audio_io import load_wav  # noqa: E402
from app.denoise import MicDenoiser  # noqa: E402

SRC_MANIFEST = ROOT / "data" / "ledger_corpus" / "manifest.jsonl"
OUT_ROOT = ROOT / "data" / "eval" / "denoise_ab"
DN_DIR = OUT_ROOT / "dn"
OUT_MANIFEST = OUT_ROOT / "manifest.jsonl"
DIALOGUE_MANIFEST = ROOT / "data" / "dialogue_corpus" / "manifest.jsonl"
CHUNK = 1600  # 100ms @16k


def _rms(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=np.float64).reshape(-1)
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(x))))


def _write_wav(path: Path, audio: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm16.tobytes())


def _denoise_file(dn: MicDenoiser, wav: Path) -> np.ndarray:
    raw = load_wav(str(wav), target_sr=int(config.SAMPLE_RATE))
    raw = np.asarray(raw, dtype=np.float32).reshape(-1)
    out: list[np.ndarray] = []
    for i in range(0, raw.size, CHUNK):
        piece = dn.process(raw[i : i + CHUNK])
        if piece.size:
            out.append(np.asarray(piece, dtype=np.float32).reshape(-1))
    tail = dn.flush()
    if tail.size:
        out.append(np.asarray(tail, dtype=np.float32).reshape(-1))
    if not out:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(out)


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    arr = sorted(xs)
    if len(arr) == 1:
        return arr[0]
    idx = (p / 100.0) * (len(arr) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(arr) - 1)
    frac = idx - lo
    return arr[lo] * (1.0 - frac) + arr[hi] * frac


def main() -> int:
    rows = [
        json.loads(l)
        for l in SRC_MANIFEST.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    rows = [r for r in rows if r.get("corpus") in {"ami", "fleurs"}]
    if len(rows) != 60:
        print(f"[abort] expected 60 ami+fleurs rows, got {len(rows)}")
        return 1

    # 必须在 MicDenoiser() 之前打开
    config.MIC_DENOISE = True
    dn = MicDenoiser()
    if not dn.enabled:
        print("[abort] denoiser not enabled — 检查 models/gtcrn_simple.onnx")
        return 1

    DN_DIR.mkdir(parents=True, exist_ok=True)
    ratios: list[float] = []
    out_rows: list[dict] = []
    sr = int(config.SAMPLE_RATE)

    for i, item in enumerate(rows):
        src = ROOT / item["path"]
        raw = load_wav(str(src), target_sr=sr)
        raw = np.asarray(raw, dtype=np.float32).reshape(-1)
        denoised = _denoise_file(dn, src)
        dur_raw = raw.size / float(sr)
        dur_dn = denoised.size / float(sr)
        if abs(dur_dn - dur_raw) > 0.05:
            print(
                f"[abort] duration drift id={item['id']} "
                f"raw={dur_raw:.3f}s dn={dur_dn:.3f}s"
            )
            return 1
        dest = DN_DIR / f"{item['id']}.wav"
        _write_wav(dest, denoised, sr)
        r_raw = _rms(raw)
        r_dn = _rms(denoised)
        ratio = (r_dn / r_raw) if r_raw > 1e-8 else 0.0
        ratios.append(ratio)
        row = dict(item)
        row["path"] = str(dest.relative_to(ROOT)).replace("\\", "/")
        out_rows.append(row)
        print(f"[{i+1}/{len(rows)}] {item['id']} rms_ratio={ratio:.3f}")

    with OUT_MANIFEST.open("w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    low = sum(1 for x in ratios if x < 0.3)
    print(
        f"[rms] p10={_pct(ratios, 10):.3f} p50={_pct(ratios, 50):.3f} "
        f"p90={_pct(ratios, 90):.3f}  n_lt_0.3={low}/{len(ratios)}"
    )
    print(f"[ok] wrote {len(out_rows)} wavs → {DN_DIR}")
    print(f"[ok] manifest → {OUT_MANIFEST}")

    # 对话 KPI 集：只报音量比（非 ledger ok/WER）
    if DIALOGUE_MANIFEST.is_file():
        drows = [
            json.loads(l)
            for l in DIALOGUE_MANIFEST.read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        d_ratios: list[float] = []
        d_out = OUT_ROOT / "dialogue_rms.jsonl"
        with d_out.open("w", encoding="utf-8") as f:
            for item in drows:
                wav = ROOT / "data" / "dialogue_corpus" / item["wav"]
                if not wav.is_file():
                    continue
                raw = load_wav(str(wav), target_sr=sr)
                raw = np.asarray(raw, dtype=np.float32).reshape(-1)
                denoised = _denoise_file(dn, wav)
                r_raw = _rms(raw)
                r_dn = _rms(denoised)
                ratio = (r_dn / r_raw) if r_raw > 1e-8 else 0.0
                d_ratios.append(ratio)
                f.write(
                    json.dumps(
                        {
                            "id": item["id"],
                            "direction": item.get("direction"),
                            "rms_ratio": round(ratio, 4),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        if d_ratios:
            d_low = sum(1 for x in d_ratios if x < 0.3)
            print(
                f"[dialogue_rms] n={len(d_ratios)} "
                f"p10={_pct(d_ratios, 10):.3f} p50={_pct(d_ratios, 50):.3f} "
                f"p90={_pct(d_ratios, 90):.3f}  n_lt_0.3={d_low}/{len(d_ratios)}"
            )
            print(f"[dialogue_rms] wrote {d_out}")

    # 恢复进程内开关（避免误开）
    config.MIC_DENOISE = False
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
