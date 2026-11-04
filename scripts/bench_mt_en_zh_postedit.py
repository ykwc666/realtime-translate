"""A/B en→zh call phrases: raw CT2 vs +postedit (+ optional human judge).

Measures ct2 bare output (TM bypassed) then postedit_en_zh.

python scripts/bench_mt_en_zh_postedit.py
python scripts/bench_mt_en_zh_postedit.py --judge docs/mt_en_zh_judge.jsonl
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app.mt_postedit import postedit_en_zh  # noqa: E402
from app.translate import Translator  # noqa: E402

EVAL = ROOT / "data" / "mt_call_eval_en_zh.jsonl"
OUT = ROOT / "docs" / "mt_en_zh_postedit_ab.jsonl"
DUMP = ROOT / "docs" / "mt_en_zh_baseline_dump.jsonl"
JUDGE_DEFAULT = ROOT / "docs" / "mt_en_zh_judge.jsonl"


def _load_rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _tags(row: dict) -> list[str]:
    t = row.get("tags")
    if isinstance(t, list) and t:
        return [str(x) for x in t]
    if isinstance(t, str) and t:
        return [t]
    return ["legacy"]


def _needle_ok(row: dict, hyp: str, raw: str) -> bool:
    needles = [x.lower() for x in row.get("needles") or []]
    if row.get("untouched"):
        return hyp == raw
    h = hyp.lower()
    return all(n in h for n in needles) if needles else True


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "n/a"


def run_dump(tr: Translator) -> list[dict]:
    rows = _load_rows(EVAL)
    cases: list[dict] = []
    pe_ms: list[float] = []
    n_ok_raw = n_ok_pe = 0
    print(f"eval={EVAL.name} cases={len(rows)} (raw=ct2 bypass TM)")
    print(f"{'#':<3} {'en':<46} | raw | +postedit | needle")
    print("-" * 120)
    for i, row in enumerate(rows):
        en = row["en"]
        raw = tr._translate_ct2(en, "en")
        t0 = time.perf_counter()
        pe = postedit_en_zh(en, raw)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        pe_ms.append(dt_ms)
        ok_r = _needle_ok(row, raw, raw)
        ok_p = _needle_ok(row, pe, raw)
        n_ok_raw += int(ok_r)
        n_ok_pe += int(ok_p)
        flag = "FIX" if (not ok_r and ok_p) else ("OK" if ok_p else "MISS")
        if ok_r and not ok_p:
            flag = "BROKEN?"
        print(f"{i:<3} {en[:46]:<46} | {raw} | {pe} | {flag}")
        cases.append(
            {
                "i": i,
                "en": en,
                "zh_ref": row.get("zh", ""),
                "tags": _tags(row),
                "raw": raw,
                "postedit": pe,
                "pe_ms": round(dt_ms, 3),
                "needle_raw_ok": ok_r,
                "needle_postedit_ok": ok_p,
                "legacy": "legacy" in _tags(row) or i < 10,
            }
        )

    pe_ms_sorted = sorted(pe_ms)
    p50 = pe_ms_sorted[len(pe_ms_sorted) // 2]
    p95 = pe_ms_sorted[max(0, int(len(pe_ms_sorted) * 0.95) - 1)]
    print("-" * 120)
    print(
        f"needle_hit raw={n_ok_raw}/{len(rows)} ({_pct(n_ok_raw, len(rows))})  "
        f"postedit={n_ok_pe}/{len(rows)} ({_pct(n_ok_pe, len(rows))})  "
        f"delta=+{n_ok_pe - n_ok_raw}"
    )
    print(f"postedit_ms P50={p50:.3f} P95={p95:.3f} mean={statistics.mean(pe_ms):.3f}")

    DUMP.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "n": len(rows),
        "needle_raw_ok": n_ok_raw,
        "needle_postedit_ok": n_ok_pe,
        "pe_ms_p50": round(p50, 3),
        "pe_ms_p95": round(p95, 3),
        "cases": cases,
    }
    DUMP.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps({k: payload[k] for k in ("time", "n", "needle_raw_ok", "needle_postedit_ok", "pe_ms_p50", "pe_ms_p95")}, ensure_ascii=False) + "\n")
    print(f"wrote {DUMP}")
    print(f"appended summary → {OUT}")
    print(f"next: write human judgments to {JUDGE_DEFAULT} (verdict: ok|wrong|fixed|broken)")
    return cases


def summarize_judge(judge_path: Path) -> int:
    dump = json.loads(DUMP.read_text(encoding="utf-8"))
    by_en = {c["en"]: c for c in dump["cases"]}
    judgments = _load_rows(judge_path)
    if len(judgments) < len(by_en):
        print(f"WARN judge rows={len(judgments)} dump={len(by_en)}")

    totals = {"ok": 0, "wrong": 0, "fixed": 0, "broken": 0}
    by_tag: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    fail_classes: dict[str, list[dict]] = defaultdict(list)
    pe_ms = [c["pe_ms"] for c in dump["cases"]]

    print(f"\n{'en':<48} | verdict | fail_class | pe")
    print("-" * 110)
    for j in judgments:
        en = j["en"]
        c = by_en.get(en)
        if not c:
            print(f"SKIP unknown en: {en[:40]}")
            continue
        v = j["verdict"]
        totals[v] = totals.get(v, 0) + 1
        for tag in c["tags"]:
            by_tag[tag][v] += 1
            by_tag[tag]["n"] += 1
            if v in ("ok", "fixed"):
                by_tag[tag]["pass"] += 1
            if v == "broken":
                by_tag[tag]["broken"] += 1
            # raw_ok if ok or broken (raw was good); postedit_ok if ok or fixed
            if v in ("ok", "broken"):
                by_tag[tag]["raw_ok"] += 1
            if v in ("ok", "fixed"):
                by_tag[tag]["postedit_ok"] += 1
        if v == "wrong" or v == "broken":
            fc = j.get("fail_class") or ("postedit_regression" if v == "broken" else "unspecified")
            fail_classes[fc].append({"en": en, "raw": c["raw"], "pe": c["postedit"], "ref": c["zh_ref"], "verdict": v})
        print(f"{en[:48]:<48} | {v:<6} | {j.get('fail_class',''):<18} | {c['postedit'][:40]}")

    n = sum(totals.values())
    print("\n=== TOTALS ===")
    print(dict(totals), f"n={n}")
    print(f"postedit_pass={(totals.get('ok',0)+totals.get('fixed',0))}/{n} ({_pct(totals.get('ok',0)+totals.get('fixed',0), n)})")
    print(f"broken_rate={totals.get('broken',0)}/{n} ({_pct(totals.get('broken',0), n)})")
    pe_ms_sorted = sorted(pe_ms)
    p50 = pe_ms_sorted[len(pe_ms_sorted) // 2]
    p95 = pe_ms_sorted[max(0, int(len(pe_ms_sorted) * 0.95) - 1)]
    print(f"postedit_ms P50={p50:.3f} P95={p95:.3f}")

    print("\n=== BY TAG ===")
    print(f"{'tag':<12} n  raw_ok  pe_ok  fixed  broken  pass%")
    for tag in sorted(by_tag):
        b = by_tag[tag]
        nn = b["n"]
        print(
            f"{tag:<12} {nn:>2}  {b['raw_ok']:>6}  {b['postedit_ok']:>5}  {b.get('fixed',0):>5}  {b.get('broken',0):>6}  {_pct(b['pass'], nn)}"
        )

    print("\n=== FAIL CLASSES (wrong+broken) ===")
    for fc, items in sorted(fail_classes.items(), key=lambda x: -len(x[1])):
        print(f"\n[{fc}] count={len(items)}")
        for it in items[:2]:
            print(f"  EN: {it['en']}")
            print(f"  raw: {it['raw']} | pe: {it['pe']} | ref: {it['ref']}")

    out = ROOT / "docs" / "mt_en_zh_quality_summary.json"
    out.write_text(
        json.dumps(
            {
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "n": n,
                "totals": totals,
                "by_tag": {k: dict(v) for k, v in by_tag.items()},
                "fail_classes": {k: len(v) for k, v in fail_classes.items()},
                "fail_examples": {k: v[:2] for k, v in fail_classes.items()},
                "pe_ms_p50": p50,
                "pe_ms_p95": p95,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", type=Path, nargs="?", const=JUDGE_DEFAULT, default=None)
    ap.add_argument("--dump-only", action="store_true")
    args = ap.parse_args()
    if args.judge is not None:
        if not DUMP.exists():
            print(f"missing dump {DUMP}; run without --judge first")
            return 2
        return summarize_judge(args.judge)

    tr = Translator()
    run_dump(tr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
