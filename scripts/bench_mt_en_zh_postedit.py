"""A/B en→zh call phrases: raw CT2 vs +postedit_en_zh.

python scripts/bench_mt_en_zh_postedit.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402
from app.mt_postedit import postedit_en_zh  # noqa: E402
from app.translate import Translator  # noqa: E402

EVAL = ROOT / "data" / "mt_call_eval_en_zh.jsonl"
OUT = ROOT / "docs" / "mt_en_zh_postedit_ab.jsonl"


def main() -> int:
    rows = [
        json.loads(line)
        for line in EVAL.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    tr = Translator()
    n_ok_raw = n_ok_pe = 0
    n = 0
    print(f"eval={EVAL.name} cases={len(rows)}")
    print(f"{'en':<42} | raw | +postedit | needles")
    print("-" * 100)
    out_rows = []
    for row in rows:
        en = row["en"]
        needles = [x.lower() for x in row.get("needles") or []]
        raw = tr._translate_ct2(en, "en")
        pe = postedit_en_zh(en, raw)
        # also check pipeline path
        piped = tr.translate(en, src_lang="en").text
        assert piped == pe, (piped, pe)

        def hit(hyp: str) -> bool:
            h = hyp.lower()
            if row.get("untouched"):
                return hyp == raw
            return all(n in h for n in needles) if needles else True

        ok_r, ok_p = hit(raw), hit(pe)
        n += 1
        n_ok_raw += int(ok_r)
        n_ok_pe += int(ok_p)
        flag = "FIX" if (not ok_r and ok_p) else ("OK" if ok_p else "MISS")
        print(f"{en[:42]:<42} | {raw} | {pe} | {flag}")
        out_rows.append(
            {
                "en": en,
                "raw": raw,
                "postedit": pe,
                "raw_ok": ok_r,
                "postedit_ok": ok_p,
                "flag": flag,
            }
        )

    print("-" * 100)
    print(
        f"needle_hit raw={n_ok_raw}/{n} ({100 * n_ok_raw / n:.0f}%)  "
        f"postedit={n_ok_pe}/{n} ({100 * n_ok_pe / n:.0f}%)  "
        f"delta=+{n_ok_pe - n_ok_raw}"
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "n": n,
                    "raw_ok": n_ok_raw,
                    "postedit_ok": n_ok_pe,
                    "cases": out_rows,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    print(f"wrote {OUT}")
    return 0 if n_ok_pe >= n_ok_raw else 1


if __name__ == "__main__":
    raise SystemExit(main())
