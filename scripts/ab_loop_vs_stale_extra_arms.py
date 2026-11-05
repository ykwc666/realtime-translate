"""Extra arms for deconfound: stale_only, tail_only; plus Q5 double-run.

Does NOT permanently change config defaults (restores after).
Writes docs/ab_loop_vs_stale_6arm.json (merges with existing 4-arm results).

python scripts/ab_loop_vs_stale_extra_arms.py
"""
from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env

setup_env()
from app import config

_spec = importlib.util.spec_from_file_location(
    "eval_ledger", ROOT / "scripts" / "eval_ledger.py"
)
el = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(el)

FOCUS = {
    "ami_008",
    "ami_031",
    "ami_039",
    "ami_046",
}

# snapshot current defaults for Q5 + restore
DEFAULTS = {
    "ASR_LOOP_GUARD_MODE": config.ASR_LOOP_GUARD_MODE,
    "ASR_LOOP_KEEP_TAIL": bool(config.ASR_LOOP_KEEP_TAIL),
    "ASR_SPEC_REJECT_STALE": bool(config.ASR_SPEC_REJECT_STALE),
    "ASR_SPEC_MISS_GROW_USE_P90": bool(config.ASR_SPEC_MISS_GROW_USE_P90),
}

CELLS = [
    {
        "name": "stale_only",
        "ASR_LOOP_GUARD_MODE": "legacy",
        "ASR_LOOP_KEEP_TAIL": False,
        "ASR_SPEC_REJECT_STALE": True,
        "ASR_SPEC_MISS_GROW_USE_P90": False,
    },
    {
        "name": "tail_only",
        "ASR_LOOP_GUARD_MODE": "legacy",
        "ASR_LOOP_KEEP_TAIL": True,
        "ASR_SPEC_REJECT_STALE": False,
        "ASR_SPEC_MISS_GROW_USE_P90": False,
    },
]


def _run_named(name: str) -> dict:
    detail_src = ROOT / "docs" / "ledger_eval_detail.jsonl"
    summary_src = ROOT / "docs" / "ledger_eval_summary.json"
    sys.argv = ["eval_ledger.py", "--corpus", "ami", "--skip-a5"]
    rc = el.main()
    if rc != 0:
        return {"name": name, "error": rc}
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
    focus = {
        i: {
            "verdict": by_id[i]["pipeline_verdict"],
            "asr_wer": by_id[i].get("asr_wer"),
            "n_utts": by_id[i].get("n_utts"),
            "src": by_id[i].get("pipeline_src") or "",
        }
        for i in FOCUS
        if i in by_id
    }
    return {
        "name": name,
        "pipeline_ok": pipe["ok"],
        "pipeline_ok_rate": pipe["ok_rate"],
        "empty_n": sum(1 for r in rows if (r.get("n_utts") or 0) == 0),
        "wer_mean": summ["table_wer"]["ami"]["wer_mean"],
        "focus": focus,
    }


def main() -> int:
    print("CURRENT DEFAULTS (Q5 baseline flags):", DEFAULTS)
    new_results = []

    # Q5: two runs with current defaults untouched
    for k, v in DEFAULTS.items():
        setattr(config, k, v)
    q5 = []
    for run_i in (1, 2):
        name = f"q5_default_run{run_i}"
        print(f"\n======== {name} ========")
        row = _run_named(name)
        q5.append(row)
        print(
            " ok",
            row.get("pipeline_ok"),
            "046",
            (row.get("focus") or {}).get("ami_046"),
        )

    for cell in CELLS:
        name = cell["name"]
        print(f"\n======== CELL {name} ========")
        for k, v in cell.items():
            if k == "name":
                continue
            setattr(config, k, v)
        row = _run_named(name)
        row["config"] = {k: v for k, v in cell.items() if k != "name"}
        new_results.append(row)
        print(" ok", row.get("pipeline_ok"))
        print(" 008", (row.get("focus") or {}).get("ami_008"))
        print(" 046", (row.get("focus") or {}).get("ami_046"))

    # restore defaults
    for k, v in DEFAULTS.items():
        setattr(config, k, v)

    old = {}
    old_path = ROOT / "docs" / "ab_loop_vs_stale.json"
    if old_path.exists():
        old = json.loads(old_path.read_text(encoding="utf-8"))

    out = {
        "defaults_at_q5": DEFAULTS,
        "q5_double_run": q5,
        "original_4arm": old.get("results", []),
        "extra_arms": new_results,
    }
    out_path = ROOT / "docs" / "ab_loop_vs_stale_6arm.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
