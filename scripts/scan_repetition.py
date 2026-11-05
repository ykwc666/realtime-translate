"""Scan harvest/judge jsonl for ≥3 consecutive repeated fragments in tgt/mt_raw.

python scripts/scan_repetition.py docs/harvest_judge_2026-11-04-public-en.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def find_repetitions(text: str, min_times: int = 3) -> list[dict]:
    """Return list of {unit, times, span} for consecutive repeats ≥ min_times."""
    t = text or ""
    hits: list[dict] = []
    for m in re.finditer(r"([\u4e00-\u9fff])\1{2,}", t):
        unit = m.group(1)
        times = len(m.group(0))
        if times >= min_times:
            hits.append({"unit": unit, "times": times, "span": m.group(0)})
    for n in range(2, 7):
        i = 0
        while i + n * min_times <= len(t):
            unit = t[i : i + n]
            if not re.fullmatch(r"[\u4e00-\u9fff]+", unit):
                i += 1
                continue
            times = 1
            j = i + n
            while t[j : j + n] == unit:
                times += 1
                j += n
            if times >= min_times:
                hits.append({"unit": unit, "times": times, "span": unit * times})
                i = j
            else:
                i += 1
    for m in re.finditer(r"\b(\w+)(?:\s+\1){2,}\b", t, flags=re.I):
        hits.append(
            {
                "unit": m.group(1),
                "times": m.group(0).count(m.group(1)),
                "span": m.group(0),
            }
        )
    seen: set[str] = set()
    out: list[dict] = []
    for h in hits:
        if h["span"] in seen:
            continue
        seen.add(h["span"])
        out.append(h)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl", type=Path)
    ap.add_argument("--field", default="tgt")
    args = ap.parse_args()
    rows = [
        json.loads(l)
        for l in args.jsonl.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    n_hit = 0
    print(f"file={args.jsonl} n={len(rows)} field={args.field}")
    for i, r in enumerate(rows):
        text = r.get(args.field) or r.get("tgt") or r.get("postedit") or ""
        hits = find_repetitions(text)
        if hits:
            n_hit += 1
            print(f"[{i}] HIT {hits[0]} | {text[:80]}")
    rate = n_hit / len(rows) if rows else 0.0
    print(f"repetition_rate={n_hit}/{len(rows)} ({100 * rate:.1f}%)")
    out = {
        "file": str(args.jsonl),
        "n": len(rows),
        "n_hit": n_hit,
        "repetition_rate": round(rate, 4),
    }
    out_path = ROOT / "docs" / f"repetition_scan_{args.jsonl.stem}.json"
    out_path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
