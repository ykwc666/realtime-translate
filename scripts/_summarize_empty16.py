import json
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
empty = [r for r in rows if (r.get("n_utts") or 0) == 0]
print("n_empty", len(empty))
print("ids", [r["id"] for r in empty])

ten = json.loads((ROOT / "docs" / "vad_ten_threshold_ab.json").read_text(encoding="utf-8"))
by_thr = {r["VAD_TEN_THRESHOLD"]: set(r["rescue_ids"]) for r in ten["results"]}
print("ten0.4", sorted(by_thr.get(0.4, [])))
print("ten0.3", sorted(by_thr.get(0.3, [])))

for r in empty:
    audio, sr = sf.read(str(ROOT / r["path"]), dtype="float32", always_2d=False)
    if getattr(audio, "ndim", 1) > 1:
        audio = audio.mean(axis=1)
    x = np.asarray(audio, dtype=np.float32)
    win = max(1, int(sr * 0.03))
    fr = []
    for i in range(0, max(1, len(x) - win + 1), win):
        w = x[i : i + win]
        fr.append(float(np.sqrt(np.mean(np.square(w)))))
    fa = np.asarray(fr)
    ref = (r.get("ref") or "")[:48]
    print(
        f"{r['id']:10} dur={len(x)/sr:.2f}s peak={float(np.max(np.abs(x))):.4f} "
        f"mean={float(np.mean(fa)):.4f} p95={float(np.percentile(fa,95)):.4f} | {ref}"
    )
