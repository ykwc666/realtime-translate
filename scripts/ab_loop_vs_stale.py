"""A/B: loop soft (a) vs reject_stale (b) on AMI ledger (in-process config).

python scripts/ab_loop_vs_stale.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "eval_ledger", ROOT / "scripts" / "eval_ledger.py"
)
el = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(el)

OUT = ROOT / "docs" / "ab_loop_vs_stale.json"

CELLS = [
    {
        "name": "pre",
        "ASR_LOOP_GUARD_MODE": "legacy",
        "ASR_LOOP_KEEP_TAIL": False,
        "ASR_SPEC_REJECT_STALE": False,
        "ASR_SPEC_MISS_GROW_USE_P90": False,
    },
    {
        "name": "a_only",
        "ASR_LOOP_GUARD_MODE": "soft",
        "ASR_LOOP_KEEP_TAIL": True,
        "ASR_SPEC_REJECT_STALE": False,
        "ASR_SPEC_MISS_GROW_USE_P90": False,
    },
    {
        "name": "b_only",
        "ASR_LOOP_GUARD_MODE": "legacy",
        "ASR_LOOP_KEEP_TAIL": True,
        "ASR_SPEC_REJECT_STALE": True,
        "ASR_SPEC_MISS_GROW_USE_P90": True,
    },
    {
        "name": "both",
        "ASR_LOOP_GUARD_MODE": "soft",
        "ASR_LOOP_KEEP_TAIL": True,
        "ASR_SPEC_REJECT_STALE": True,
        "ASR_SPEC_MISS_GROW_USE_P90": True,
    },
]

FOCUS = {
    "ami_008",
    "ami_031",
    "ami_039",
    "ami_046",
    "ami_004",
    "ami_006",
    "ami_010",
    "ami_033",
    "ami_036",
    "ami_040",
    "ami_042",
}


def main() -> int:
    results = []
    for cell in CELLS:
        name = cell["name"]
        print(f"\n======== CELL {name} ========")
        for k, v in cell.items():
            if k == "name":
                continue
            setattr(config, k, v)
        # argv for eval_ledger.main
        sys.argv = ["eval_ledger.py", "--corpus", "ami", "--skip-a5"]
        rc = el.main()
        if rc != 0:
            results.append({"name": name, "error": rc})
            continue
        detail_src = ROOT / "docs" / "ledger_eval_detail.jsonl"
        summary_src = ROOT / "docs" / "ledger_eval_summary.json"
        detail = ROOT / "docs" / f"ab_loop_vs_stale_{name}_detail.jsonl"
        summary = ROOT / "docs" / f"ab_loop_vs_stale_{name}_summary.json"
        detail.write_text(detail_src.read_text(encoding="utf-8"), encoding="utf-8")
        summary.write_text(summary_src.read_text(encoding="utf-8"), encoding="utf-8")
        summ = json.loads(summary.read_text(encoding="utf-8"))
        pipe = summ["table_split_ledger"]["ami"]["pipeline"]
        rows = [
            json.loads(l)
            for l in detail.read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        by_id = {r["id"]: r for r in rows}
        focus_rows = {
            i: {
                "verdict": by_id[i]["pipeline_verdict"],
                "asr_wer": by_id[i].get("asr_wer"),
                "n_utts": by_id[i].get("n_utts"),
                "src": (by_id[i].get("pipeline_src") or "")[:80],
            }
            for i in FOCUS
            if i in by_id
        }
        row = {
            "name": name,
            "config": {k: v for k, v in cell.items() if k != "name"},
            "pipeline_ok": pipe["ok"],
            "pipeline_ok_rate": pipe["ok_rate"],
            "empty_n": sum(1 for r in rows if (r.get("n_utts") or 0) == 0),
            "wer_mean": summ["table_wer"]["ami"]["wer_mean"],
            "focus": focus_rows,
        }
        results.append(row)
        print(
            f"  ok={pipe['ok']}/48 empty={row['empty_n']} wer={row['wer_mean']}"
        )
        print("  008", focus_rows.get("ami_008"))
        print("  031", focus_rows.get("ami_031"))
        print("  039", focus_rows.get("ami_039"))
        print("  046", focus_rows.get("ami_046"))

    config.ASR_LOOP_GUARD_MODE = "soft"
    config.ASR_LOOP_KEEP_TAIL = True
    config.ASR_SPEC_REJECT_STALE = True
    config.ASR_SPEC_MISS_GROW_USE_P90 = True

    OUT.write_text(
        json.dumps({"results": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
