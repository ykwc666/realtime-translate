"""Offline harvest review: table for human labels + ingest summary.

Step 1 — emit judge sheet:
  python scripts/judge_harvest.py data/harvest/2026-11-04-001.jsonl

Step 2 — fill verdict in the generated docs/harvest_judge_*.jsonl
  (ok | awkward | wrong), then:
  python scripts/judge_harvest.py --ingest docs/harvest_judge_2026-11-04-001.jsonl
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

JUDGE_RUBRIC = """\
## 判定标准（两次会话必须同一尺子）

| 档 | 含义 |
|----|------|
| **ok** | 与 src 语义等价，口语自然，可直接播给中文听众 |
| **awkward** | 能懂，但不自然 / 术语别扭 / 语序怪（听众要愣一下） |
| **wrong** | 语义错、极性反、关键信息丢、或听不懂 |

- 只看 **tgt**（发给 TTS 的最终译文）相对 **src**。
- `mt_raw` 仅用于分组：`mt_raw != tgt` → 规则层/TM 修过；相等 → 模型原样出。
- 不要用字符串相似度自动判；本文件人判列必须手填。
"""


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "n/a"


def _agg(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0, "p50": None, "p90": None, "max": None}
    s = sorted(xs)
    return {
        "n": len(s),
        "p50": round(s[len(s) // 2], 1),
        "p90": round(s[max(0, int(0.9 * (len(s) - 1)))], 1),
        "max": round(max(s), 1),
        "mean": round(statistics.mean(s), 1),
    }


def _rule_touched(row: dict) -> bool:
    raw = (row.get("mt_raw") or "").strip()
    tgt = (row.get("tgt") or "").strip()
    return bool(raw) and bool(tgt) and raw != tgt


def _asr_flags(src: str) -> dict:
    s = (src or "").strip()
    empty = not s
    # CJK+alnum content length
    import re

    content = re.sub(r"[^\w\u4e00-\u9fff]+", "", s, flags=re.UNICODE)
    short = (not empty) and len(content) < 2
    # crude truncation: ends mid-word Latin or trailing ellipsis / incomplete punct
    trunc = bool(
        (not empty)
        and (
            s.endswith("...")
            or s.endswith("…")
            or (len(s) >= 4 and s[-1].isalpha() and " " in s and not s.endswith((".", "?", "!", "。", "？", "！")))
        )
    )
    return {"empty": empty, "short": short, "trunc_suspect": trunc}


def emit_sheet(harvest_path: Path, out_md: Path | None, out_jsonl: Path | None) -> int:
    rows = _load_jsonl(harvest_path)
    day = datetime.now().strftime("%Y-%m-%d")
    stem = harvest_path.stem
    if out_md is None:
        out_md = ROOT / "docs" / f"harvest_judge_{stem}.md"
    if out_jsonl is None:
        out_jsonl = ROOT / "docs" / f"harvest_judge_{stem}.jsonl"
    out_md.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        f"# Harvest 人判表 — {stem}",
        "",
        f"来源采集：`{harvest_path.as_posix()}`",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        JUDGE_RUBRIC,
        "",
        "## 待判表",
        "",
        "| # | src | mt_raw | tgt | rule_touched | verdict |",
        "|---|-----|--------|-----|--------------|---------|",
    ]
    judge_rows = []
    for i, r in enumerate(rows):
        touched = _rule_touched(r)
        src = (r.get("src") or "").replace("|", "\\|")
        raw = (r.get("mt_raw") or "").replace("|", "\\|")
        tgt = (r.get("tgt") or "").replace("|", "\\|")
        lines.append(
            f"| {i} | {src} | {raw} | {tgt} | {'yes' if touched else 'no'} |  |"
        )
        judge_rows.append(
            {
                "i": i,
                "src": r.get("src", ""),
                "mt_raw": r.get("mt_raw", ""),
                "tgt": r.get("tgt", ""),
                "rule_touched": touched,
                "verdict": "",
                "asr_wait_ms": r.get("asr_wait_ms"),
                "latency_perceived_ms": r.get("latency_perceived_ms"),
                "latency_device_ttfa_ms": r.get("latency_device_ttfa_ms"),
                "spec_hit": r.get("spec_hit"),
                "mode": r.get("mode"),
                "source_desc": r.get("source_desc"),
                "harvest_path": str(harvest_path),
            }
        )
    lines.extend(
        [
            "",
            "## 填判方式",
            "",
            f"1. 编辑 `{out_jsonl.as_posix()}`，把每行 `verdict` 填成 `ok` / `awkward` / `wrong`。",
            f"2. 汇总：`python scripts/judge_harvest.py --ingest {out_jsonl.as_posix()}`",
            "",
        ]
    )
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with out_jsonl.open("w", encoding="utf-8") as f:
        for row in judge_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {out_md} ({len(rows)} rows)")
    print(f"wrote {out_jsonl} (fill verdict, then --ingest)")
    return 0


def ingest(path: Path) -> int:
    rows = _load_jsonl(path)
    missing = [r for r in rows if (r.get("verdict") or "").strip() not in ("ok", "awkward", "wrong")]
    if missing:
        print(f"WARN: {len(missing)}/{len(rows)} rows missing verdict (ok|awkward|wrong)")
    labeled = [r for r in rows if (r.get("verdict") or "").strip() in ("ok", "awkward", "wrong")]
    n = len(labeled)
    totals = Counter(r["verdict"].strip() for r in labeled)
    by_touch: dict[str, Counter] = {
        "rule_touched": Counter(),
        "model_passthrough": Counter(),
    }
    for r in labeled:
        key = "rule_touched" if r.get("rule_touched") else "model_passthrough"
        by_touch[key][r["verdict"].strip()] += 1

    perc = [
        float(r["latency_perceived_ms"])
        for r in rows
        if r.get("latency_perceived_ms") is not None
    ]
    device = [
        float(r["latency_device_ttfa_ms"])
        for r in rows
        if r.get("latency_device_ttfa_ms") is not None
    ]

    asr = Counter()
    for r in rows:
        fl = _asr_flags(r.get("src") or "")
        for k, v in fl.items():
            if v:
                asr[k] += 1
    asr["n"] = len(rows)

    print("\n=== MT quality ===")
    print(f"labeled={n}/{len(rows)}  {dict(totals)}")
    for k in ("ok", "awkward", "wrong"):
        print(f"  {k}: {totals.get(k, 0)} ({_pct(totals.get(k, 0), n)})")
    print("\n=== By rule layer (核心) ===")
    for group in ("rule_touched", "model_passthrough"):
        c = by_touch[group]
        nn = sum(c.values())
        print(f"[{group}] n={nn}")
        for k in ("ok", "awkward", "wrong"):
            print(f"  {k}: {c.get(k, 0)} ({_pct(c.get(k, 0), nn)})")

    print("\n=== Latency ===")
    print(f"perceived  {_agg(perc)}")
    print(f"device_ttfa {_agg(device)}")

    print("\n=== ASR side ===")
    print(
        f"empty={asr.get('empty', 0)}/{asr['n']} ({_pct(asr.get('empty', 0), asr['n'])})  "
        f"short={asr.get('short', 0)}/{asr['n']} ({_pct(asr.get('short', 0), asr['n'])})  "
        f"trunc_suspect={asr.get('trunc_suspect', 0)}/{asr['n']} ({_pct(asr.get('trunc_suspect', 0), asr['n'])})"
    )

    summary = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ingest": str(path),
        "n_rows": len(rows),
        "n_labeled": n,
        "totals": dict(totals),
        "by_rule_layer": {g: dict(c) for g, c in by_touch.items()},
        "latency_perceived": _agg(perc),
        "latency_device_ttfa": _agg(device),
        "asr": dict(asr),
    }
    out = ROOT / "docs" / f"harvest_summary_{path.stem.replace('harvest_judge_', '')}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {out}")
    return 0 if not missing else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("harvest", nargs="?", type=Path, help="data/harvest/*.jsonl")
    ap.add_argument("--ingest", type=Path, help="filled harvest_judge_*.jsonl")
    ap.add_argument("--md", type=Path, default=None)
    ap.add_argument("--jsonl", type=Path, default=None)
    args = ap.parse_args(argv)
    if args.ingest:
        return ingest(args.ingest)
    if not args.harvest:
        ap.error("provide harvest jsonl or --ingest")
    return emit_sheet(args.harvest, args.md, args.jsonl)


if __name__ == "__main__":
    raise SystemExit(main())
