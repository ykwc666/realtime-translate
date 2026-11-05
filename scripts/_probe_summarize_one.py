import json
import sys
from collections import Counter
from pathlib import Path


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    i = (p / 100) * (len(xs) - 1)
    lo = int(i)
    hi = min(lo + 1, len(xs) - 1)
    f = i - lo
    return xs[lo] * (1 - f) + xs[hi] * f


def main() -> int:
    p = Path(sys.argv[1] if len(sys.argv) > 1 else "data/probe/2026-11-05-1546/2026-11-05-1546.jsonl")
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    peaks = [r["open_peak"] for r in rows if r.get("open_peak") is not None]
    rmss = [r["open_rms"] for r in rows if r.get("open_rms") is not None]
    reasons = Counter(r.get("cut_reason") for r in rows)
    paths = Counter(r.get("emit_path") for r in rows)
    empty = sum(1 for r in rows if r.get("cut_reason") == "empty_abort")
    trails = [float(r.get("trailing_silence_sec") or 0) for r in rows]
    print(
        f"utts={len(rows)} empty_abort={empty} "
        f"emit_model={paths.get('model_endpoint', 0)} "
        f"emit_silence_forced={paths.get('silence_forced', 0)} "
        f"flush={paths.get('flush', 0)}"
    )
    print(
        f"open_peak  p10={pct(peaks, 10):.3f} p50={pct(peaks, 50):.3f} p90={pct(peaks, 90):.3f}"
    )
    print(
        f"open_rms   p10={pct(rmss, 10):.3f} p50={pct(rmss, 50):.3f} p90={pct(rmss, 90):.3f}"
    )
    print(
        "cut_reason",
        " ".join(f"{k}={v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])),
    )
    print("trailing_silence 全为 0:", "yes" if trails and all(t == 0 for t in trails) else "no")
    print("--- 末 8 句 ---")
    for r in rows[-8:]:
        print(
            f"#{r['utt_idx']} {r['audio_sec']}s peak={r.get('open_peak')} "
            f"path={r.get('emit_path')} reason={r.get('cut_reason')} src={r.get('src')!r}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
