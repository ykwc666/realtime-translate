"""Re-verify empty-16 cause: TEN frame hits + SenseVoice oneshot WER."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env

setup_env()
from app import config
from app.asr_stream import StreamingAsr


def wer(ref: str, hyp: str) -> float:
    rt = re.findall(r"[A-Za-z0-9']+", (ref or "").upper())
    ht = re.findall(r"[A-Za-z0-9']+", (hyp or "").upper())
    if not rt:
        return 0.0 if not ht else 1.0
    # Levenshtein
    n, m = len(rt), len(ht)
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, m + 1):
            cur = dp[j]
            if rt[i - 1] == ht[j - 1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j - 1])
            prev = cur
    return dp[m] / n


def ten_frame_hits(audio: np.ndarray, thr: float) -> int:
    """Count TEN windows marked speech (sherpa VoiceActivityDetector)."""
    import sherpa_onnx

    config.VAD_TEN_THRESHOLD = float(thr)
    vad_cfg = sherpa_onnx.VadModelConfig()
    vad_cfg.ten_vad.model = str(config.TEN_VAD_ONNX)
    vad_cfg.ten_vad.threshold = float(thr)
    vad_cfg.ten_vad.min_silence_duration = float(config.VAD_SILENCE_SEC)
    vad_cfg.ten_vad.min_speech_duration = float(config.MIN_SPEECH_SEC)
    vad_cfg.ten_vad.max_speech_duration = float(config.VAD_MAX_UTT_SEC)
    vad_cfg.sample_rate = config.SAMPLE_RATE
    vad_cfg.num_threads = 1
    vad_cfg.provider = "cpu"
    vad = sherpa_onnx.VoiceActivityDetector(vad_cfg, buffer_size_in_seconds=60)
    win = int(vad_cfg.ten_vad.window_size)
    hits = 0
    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    for i in range(0, len(x) - win + 1, win):
        vad.accept_waveform(x[i : i + win].tolist())
        # is_speech_detected is a method in some builds — call it
        det = vad.is_speech_detected
        if callable(det):
            det = det()
        if det:
            hits += 1
    return hits


def main() -> int:
    rows = [
        json.loads(l)
        for l in (ROOT / "docs" / "ab_loop_vs_stale_stale_only_detail.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if l.strip()
    ]
    empty = [r for r in rows if (r.get("n_utts") or 0) == 0]
    print("empty", len(empty), [r["id"] for r in empty])

    # 1) TEN frame hits at several thresholds
    print("\n=== TEN frame hits (raw wav, no pad) ===")
    thr_hits = {0.5: 0, 0.4: 0, 0.3: 0, 0.2: 0}
    per = []
    for r in empty:
        audio, sr = sf.read(str(ROOT / r["path"]), dtype="float32", always_2d=False)
        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)
        assert sr == config.SAMPLE_RATE
        row = {"id": r["id"]}
        for thr in thr_hits:
            h = ten_frame_hits(audio, thr)
            row[f"hits_{thr}"] = h
            if h > 0:
                thr_hits[thr] += 1
        per.append(row)
        print(row)
    print("files_with_any_hit", thr_hits)

    # 2) SenseVoice offline oneshot WER on full file
    print("\n=== SenseVoice oneshot on full empty files ===")
    asr = StreamingAsr()
    wers = []
    for r in empty:
        audio, sr = sf.read(str(ROOT / r["path"]), dtype="float32", always_2d=False)
        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)
        hyp = asr.transcribe_offline_only(np.asarray(audio, dtype=np.float32)).text
        w = wer(r.get("ref") or "", hyp or "")
        wers.append(w)
        print(
            f"{r['id']} wer={w:.3f} hyp={(hyp or '')[:60]!r}"
        )
    print(
        "oneshot_wer_mean",
        round(float(np.mean(wers)), 3),
        "le_0.45",
        sum(1 for w in wers if w <= 0.45),
        "/",
        len(wers),
    )

    out = {
        "n_empty": len(empty),
        "ten_files_with_hit": thr_hits,
        "ten_per_file": per,
        "oneshot_wer_mean": round(float(np.mean(wers)), 4),
        "oneshot_le_045": sum(1 for w in wers if w <= 0.45),
        "oneshot_wers": {empty[i]["id"]: round(wers[i], 4) for i in range(len(empty))},
    }
    path = ROOT / "docs" / "empty16_cause_verify.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
