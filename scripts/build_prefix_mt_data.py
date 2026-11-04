"""Expand full ZH-EN pairs into truncated-prefix -> full-EN training rows."""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "data" / "prefix_mt_seed.jsonl"
OUT = ROOT / "data" / "prefix_mt_train.jsonl"

# ratios of Chinese characters kept as source prefix
RATIOS = (0.35, 0.5, 0.65, 0.8, 1.0)


def prefixes_for(zh: str) -> list[str]:
    zh = zh.strip()
    chars = list(zh)
    if not chars:
        return []
    out: list[str] = []
    n = len(chars)
    for r in RATIOS:
        k = max(2, min(n, int(round(n * r))))
        # snap to nearby punctuation when possible
        cand = "".join(chars[:k])
        for j in range(k, min(n, k + 3)):
            if chars[j - 1] in "，。！？、；":
                cand = "".join(chars[:j])
                break
        if cand and cand not in out:
            out.append(cand)
    # always keep full sentence
    if zh not in out:
        out.append(zh)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", default=str(SEED))
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--seed-rng", type=int, default=7)
    args = parser.parse_args()

    random.seed(args.seed_rng)
    rows_in = []
    with Path(args.seed).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows_in.append(json.loads(line))

    rows_out = []
    for row in rows_in:
        zh, en = row["zh"].strip(), row["en"].strip()
        for pref in prefixes_for(zh):
            rows_out.append({"zh": pref, "en": en, "full_zh": zh})

    random.shuffle(rows_out)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for r in rows_out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"seed={len(rows_in)} expanded={len(rows_out)} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
