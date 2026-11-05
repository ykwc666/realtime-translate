"""会后重译闸门：G1 / G2_single_chunk / G6 多块。"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.asr_stream import StreamingAsr  # noqa: E402
from app.audio_io import load_wav  # noqa: E402
from app.config import setup_env  # noqa: E402
from app.retranscribe import retranscribe_audio, split_chunks  # noqa: E402


def _tok(s: str) -> list[str]:
    s = (s or "").strip().upper()
    s = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", s)
    out: list[str] = []
    for w in s.split():
        if re.fullmatch(r"[\u4e00-\u9fff]+", w):
            out.extend(list(w))
        else:
            out.append(w)
    return out


def wer(ref: str, hyp: str) -> float:
    r = _tok(ref)
    h = _tok(hyp)
    if not r:
        return 0.0 if not h else 1.0
    dp = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
    for i in range(len(r) + 1):
        dp[i][0] = i
    for j in range(len(h) + 1):
        dp[0][j] = j
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            cost = 0 if r[i - 1] == h[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost,
            )
    return dp[-1][-1] / len(r)


class _NoMT:
    def translate(self, text, src_lang=None):  # noqa: ANN001
        from app.translate import TranslateResult

        return TranslateResult(src_lang or "en", "zh", text, 0.0)


def _char_ratio(ref: str, hyp: str) -> float:
    ref_n = len(re.sub(r"\s+", "", ref))
    hyp_n = len(re.sub(r"\s+", "", hyp))
    if ref_n <= 0:
        return 1.0 if hyp_n == 0 else 0.0
    return hyp_n / ref_n


def main() -> int:
    setup_env()
    manifest = ROOT / "data" / "ledger_corpus" / "manifest.jsonl"
    rows = [
        json.loads(l)
        for l in manifest.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    rows = [r for r in rows if r.get("corpus") in ("ami", "fleurs")]
    asr = StreamingAsr()
    mt = _NoMT()
    sr = 16000

    empty = 0
    low_rows: list[dict] = []
    wers = []
    oneshot_wers = []
    t0 = time.perf_counter()
    for i, item in enumerate(rows):
        wav = ROOT / item["path"]
        ref = (item.get("ref") or "").strip()
        audio = load_wav(str(wav))
        segs, _rep = retranscribe_audio(
            audio, asr=asr, translator=mt, force_src_lang="en"
        )
        hyp = " ".join(s.src for s in segs).strip()
        if not hyp:
            empty += 1
            ratio = 0.0
            wers.append(1.0)
        else:
            ratio = _char_ratio(ref, hyp)
            wers.append(wer(ref, hyp))
        if ratio < 0.85:
            low_rows.append(
                {
                    "id": item.get("id"),
                    "ratio": round(ratio, 4),
                    "ref": ref,
                    "hyp": hyp,
                }
            )
        off = asr.transcribe_offline_only(audio)
        oneshot_wers.append(wer(ref, off.text or ""))
        if (i + 1) % 10 == 0:
            print(f"… {i+1}/{len(rows)}")

    mean_wer = sum(wers) / len(wers)
    mean_one = sum(oneshot_wers) / len(oneshot_wers)
    n_low = len(low_rows)
    g1_ok = empty == 0 and n_low <= 2
    g2_ok = mean_wer <= mean_one + 0.02

    print("=== G1 low char_ratio (<0.85) ===")
    for row in low_rows:
        print(json.dumps(row, ensure_ascii=False))

    # --- G6 multi-chunk ---
    stitch: list[np.ndarray] = []
    refs_join: list[str] = []
    gap = np.zeros(int(0.6 * sr), dtype=np.float32)
    for item in rows[:30]:
        a = load_wav(str(ROOT / item["path"]))
        stitch.append(np.asarray(a, dtype=np.float32).reshape(-1))
        stitch.append(gap)
        refs_join.append((item.get("ref") or "").strip())
    multi = np.concatenate(stitch) if stitch else np.zeros(0, np.float32)
    chunks, _dropped = split_chunks(multi, sr=sr)
    # 边界 RMS
    boundaries = []
    for _a, b in chunks[:-1]:
        lo = max(0, b - int(0.1 * sr))
        hi = min(multi.size, b + int(0.1 * sr))
        edge = multi[lo:hi]
        rms = float(np.sqrt(np.mean(np.square(edge)))) if edge.size else 0.0
        boundaries.append({"sec": round(b / sr, 3), "rms_pm100ms": round(rms, 6)})

    segs_m, rep_m = retranscribe_audio(
        multi, asr=asr, translator=mt, force_src_lang="en"
    )
    hyp_m = " ".join(s.src for s in segs_m).strip()
    ref_m = " ".join(refs_join)
    # per-file oneshot WER on same 30 clips (concat refs / concat hyps)
    one_hyps = []
    for item in rows[:30]:
        a = load_wav(str(ROOT / item["path"]))
        one_hyps.append((asr.transcribe_offline_only(a).text or "").strip())
    wer_one_30 = wer(ref_m, " ".join(one_hyps))
    wer_multi = wer(ref_m, hyp_m)
    g6_ok = len(chunks) >= 5 and wer_multi <= wer_one_30 + 0.06

    out = {
        "n": len(rows),
        "empty": empty,
        "char_ratio_lt_085": n_low,
        "g1_ok": g1_ok,
        "G2_single_chunk": {
            "note": "只对单块成立（语料 max≈11.5s < 25s 块长）",
            "wer_post": round(mean_wer, 4),
            "wer_oneshot": round(mean_one, 4),
            "delta": round(mean_wer - mean_one, 4),
            "ok": g2_ok,
        },
        "G6_multichunk": {
            "duration_sec": round(multi.size / sr, 2),
            "chunks": len(chunks),
            "report_chunks": rep_m.get("chunks"),
            "wer_multichunk": round(wer_multi, 4),
            "wer_oneshot_30": round(wer_one_30, 4),
            "delta": round(wer_multi - wer_one_30, 4),
            "tolerance": 0.06,
            "boundaries": boundaries,
            "ok": g6_ok,
        },
        "wall_sec": round(time.perf_counter() - t0, 1),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if g1_ok and g2_ok and g6_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
