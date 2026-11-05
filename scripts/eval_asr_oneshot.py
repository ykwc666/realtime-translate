"""Oneshot ASR WER (no VAD) for cleaner A5-style comparison.

python scripts/eval_asr_oneshot.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app.audio_io import load_wav  # noqa: E402
from app.pipeline import TranslatePipeline  # noqa: E402

MANIFEST = ROOT / "data" / "ledger_corpus" / "manifest.jsonl"


def norm(t: str) -> list[str]:
    t = (t or "").lower()
    t = re.sub(r"[^a-z0-9'\s]+", " ", t)
    return [w for w in t.split() if w]


def wer(ref: str, hyp: str) -> float:
    r, h = norm(ref), norm(hyp)
    if not r:
        return 0.0 if not h else 1.0
    n, m = len(r), len(h)
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, m + 1):
            cur = dp[j]
            dp[j] = prev if r[i - 1] == h[j - 1] else 1 + min(prev, dp[j], dp[j - 1])
            prev = cur
    return dp[m] / float(n)


def main() -> int:
    rows = [
        json.loads(l)
        for l in MANIFEST.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    pipe = TranslatePipeline(enable_tts=False)
    asr = pipe.asr
    out: list[dict] = []
    by: dict[str, list] = {}
    for i, it in enumerate(rows):
        audio = load_wav(str(ROOT / it["path"]))
        r_m = asr.transcribe_with_2pass(audio)
        r_o = asr._transcribe_paraformer_online_oneshot(audio)
        r_f = asr.transcribe_offline_only(audio)
        ref = it["ref"]
        rec = {
            "id": it["id"],
            "corpus": it["corpus"],
            "ref": ref,
            "merged": r_m.text,
            "merged_wer": round(wer(ref, r_m.text), 4),
            "online": r_o.text,
            "online_wer": round(wer(ref, r_o.text), 4),
            "offline": r_f.text,
            "offline_wer": round(wer(ref, r_f.text), 4),
        }
        out.append(rec)
        by.setdefault(it["corpus"], []).append(rec)
        if (i + 1) % 10 == 0:
            print(f"done {i+1}/{len(rows)}")

    summary = {}
    for c, rs in by.items():
        summary[c] = {
            "n": len(rs),
            "merged_wer": round(sum(r["merged_wer"] for r in rs) / len(rs), 4),
            "online_wer": round(sum(r["online_wer"] for r in rs) / len(rs), 4),
            "offline_wer": round(sum(r["offline_wer"] for r in rs) / len(rs), 4),
        }
    print(json.dumps(summary, indent=2))
    (ROOT / "docs" / "ledger_asr_oneshot.json").write_text(
        json.dumps({"summary": summary, "n": len(out)}, indent=2) + "\n",
        encoding="utf-8",
    )
    with (ROOT / "docs" / "ledger_asr_oneshot_detail.jsonl").open(
        "w", encoding="utf-8"
    ) as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("wrote oneshot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
