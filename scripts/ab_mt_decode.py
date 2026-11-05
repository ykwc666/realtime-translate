"""Phase B A/B: greedy / beam3 / beam3+norepeat3 / beam3+norepeat3+penalty1.1.

Uses ledger manifest refs (MT-only) + scan_repetition.
Latency gate: final MT P95 ≤ 120ms.

python scripts/ab_mt_decode.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402
from app.pipeline import TranslatePipeline  # noqa: E402
from scan_repetition import find_repetitions  # noqa: E402

MANIFEST = ROOT / "data" / "ledger_corpus" / "manifest.jsonl"
OUT = ROOT / "docs" / "mt_decode_ab.jsonl"
SUM = ROOT / "docs" / "mt_decode_ab_summary.json"

VARIANTS = [
    {"name": "greedy", "beam": 1, "ngram": 0, "penalty": 1.0},
    {"name": "beam3", "beam": 3, "ngram": 0, "penalty": 1.0},
    {"name": "beam3_norepeat3", "beam": 3, "ngram": 3, "penalty": 1.0},
    {
        "name": "beam3_norepeat3_pen1.1",
        "beam": 3,
        "ngram": 3,
        "penalty": 1.1,
    },
]


def _p95(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[max(0, int(0.95 * (len(s) - 1)))], 2)


def main() -> int:
    rows = [
        json.loads(l)
        for l in MANIFEST.read_text(encoding="utf-8").splitlines()
        if l.strip() and json.loads(l).get("ref")
    ]
    # Prefer in-domain for A/B; include a few libri stress
    ami = [r for r in rows if r["corpus"] == "ami"][:30]
    fleurs = [r for r in rows if r["corpus"] == "fleurs"]
    libri = [r for r in rows if r["corpus"] == "libri"]
    items = ami + fleurs + libri
    print(f"items={len(items)} loading MT…")
    pipe = TranslatePipeline(enable_tts=False)
    tr = pipe.translator

    detail: list[dict] = []
    summary: dict[str, dict] = {}

    for var in VARIANTS:
        config.MT_DECODE_BEAM = var["beam"]
        config.MT_NO_REPEAT_NGRAM = var["ngram"]
        config.MT_REPETITION_PENALTY = var["penalty"]
        config.MT_BEAM_MIN_SRC_CHARS = 0  # force this variant for all
        times: list[float] = []
        hits = 0
        for it in items:
            ref = it["ref"]
            t0 = time.perf_counter()
            res = tr.translate(ref, "en")
            ms = (time.perf_counter() - t0) * 1000
            times.append(ms)
            tgt = (res.text or "").strip()
            reps = find_repetitions(tgt)
            hit = bool(reps)
            if hit:
                hits += 1
            detail.append(
                {
                    "variant": var["name"],
                    "id": it["id"],
                    "corpus": it["corpus"],
                    "ref": ref,
                    "tgt": tgt,
                    "mt_ms": round(ms, 2),
                    "repetition": reps[0] if reps else None,
                }
            )
        summary[var["name"]] = {
            "n": len(items),
            "repetition_rate": round(hits / len(items), 4) if items else None,
            "mt_ms_p50": round(sorted(times)[len(times) // 2], 2) if times else None,
            "mt_ms_p95": _p95(times),
            "mt_ms_mean": round(sum(times) / len(times), 2) if times else None,
            "p95_gate_120ms": bool(_p95(times) is not None and _p95(times) <= 120),
            "knobs": var,
        }
        print(
            f"{var['name']}: rep={summary[var['name']]['repetition_rate']} "
            f"p95={summary[var['name']]['mt_ms_p95']}ms"
        )

    # restore legacy defaults
    config.MT_DECODE_BEAM = 1
    config.MT_NO_REPEAT_NGRAM = 0
    config.MT_REPETITION_PENALTY = 1.0

    with OUT.open("w", encoding="utf-8") as f:
        for r in detail:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    SUM.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUT}")
    print(f"wrote {SUM}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
