"""word_count/audio_sec distribution on stale_only AMI (for conditional KEEP_TAIL)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
rows = [
    json.loads(l)
    for l in (ROOT / "docs" / "ab_loop_vs_stale_stale_only_detail.jsonl")
    .read_text(encoding="utf-8")
    .splitlines()
    if l.strip()
]
rates = []
for r in rows:
    src = (r.get("pipeline_src") or "").strip()
    words = re.findall(r"[A-Za-z0-9']+", src)
    audio, sr = sf.read(str(ROOT / r["path"]), dtype="float32", always_2d=False)
    if getattr(audio, "ndim", 1) > 1:
        audio = audio.mean(axis=1)
    dur = len(np.asarray(audio)) / float(sr)
    rate = (len(words) / dur) if dur > 0 else 0.0
    rates.append(
        {
            "id": r["id"],
            "words": len(words),
            "dur": round(dur, 3),
            "wps": round(rate, 3),
            "n_utts": r.get("n_utts"),
            "verdict": r.get("pipeline_verdict"),
        }
    )

vals = sorted(x["wps"] for x in rates if (x.get("words") or 0) > 0)


def pct(p: float) -> float:
    i = (len(vals) - 1) * p / 100.0
    lo = int(i)
    hi = min(lo + 1, len(vals) - 1)
    f = i - lo
    return vals[lo] * (1 - f) + vals[hi] * f


out = {
    "n_with_words": len(vals),
    "p10": round(pct(10), 3) if vals else None,
    "p50": round(pct(50), 3) if vals else None,
    "p90": round(pct(90), 3) if vals else None,
    "min": vals[0] if vals else None,
    "max": vals[-1] if vals else None,
    "ami_046": next(x for x in rates if x["id"] == "ami_046"),
    "note": "P10 candidate for conditional KEEP_TAIL if re-enabled; not written into config yet",
}
path = ROOT / "docs" / "ami_words_per_sec_stale_only.json"
path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(out, indent=2))
print("wrote", path)
