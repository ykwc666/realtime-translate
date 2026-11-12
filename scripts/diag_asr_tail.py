"""zh_en ASR 长尾诊断：用 asr_err_core（非 KPI CER）判 offline_helps。

产品行为零改动。只读 detail.jsonl，写出 zh_en_asr_tail.json。

用法：
  .venv\\Scripts\\python.exe scripts\\diag_asr_tail.py
  .venv\\Scripts\\python.exe scripts\\diag_asr_tail.py ^
    --detail data\\eval\\dialogue_dev_v2\\detail.jsonl ^
    --out data\\eval\\dialogue_dev_v2\\zh_en_asr_tail.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.eval_dialogue_mslt import asr_err_core, asr_error, _core_zh  # noqa: E402

DEFAULT_DETAIL = ROOT / "data" / "eval" / "dialogue_v2" / "detail.jsonl"
DEFAULT_OUT = ROOT / "data" / "eval" / "dialogue_v2" / "zh_en_asr_tail.json"
THR = 0.35


def _chars(t: str) -> int:
    return len(re.sub(r"[^\w\u4e00-\u9fff]+", "", t or "", flags=re.UNICODE))


def _kind(ref: str, core_err: float) -> str:
    if not _core_zh(ref):
        return "empty_ref"
    if core_err == 0.0:
        return "redundant"
    if core_err <= THR:
        return "partial"
    return "misheard"


def _who(on_ok: bool, off_ok: bool) -> str:
    if off_ok and not on_ok:
        return "offline"
    if on_ok and not off_ok:
        return "online"
    if on_ok and off_ok:
        return "both_ok"
    return "both_wrong"


def diagnose(detail_path: Path) -> dict:
    rows = [
        json.loads(line)
        for line in detail_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    zh = [r for r in rows if r.get("direction") == "zh_en"]

    kpi_fails: list[dict] = []
    core_fails: list[dict] = []
    offline_helps = 0
    offline_helps_kpi = 0
    both_wrong = 0
    table: list[dict] = []

    for r in zh:
        ref = r.get("src_ref") or ""
        final = r.get("asr_hyp") or ""
        on = r.get("asr_online")
        off = r.get("asr_offline")

        kpi_err = float(r.get("asr_err") if r.get("asr_err") is not None else asr_error("zh_en", ref, final))
        core_err = asr_err_core("zh_en", ref, final)

        on_kpi = asr_error("zh_en", ref, on or "") if on is not None else 1.0
        off_kpi = asr_error("zh_en", ref, off or "") if off is not None else 1.0
        on_core = asr_err_core("zh_en", ref, on or "") if on is not None else 1.0
        off_core = asr_err_core("zh_en", ref, off or "") if off is not None else 1.0

        on_ok_kpi = on_kpi <= THR
        off_ok_kpi = off_kpi <= THR
        on_ok = on_core <= THR
        off_ok = off_core <= THR

        if kpi_err > THR:
            kpi_fails.append(r)
            if off_ok_kpi and not on_ok_kpi:
                offline_helps_kpi += 1

        if core_err > THR:
            core_fails.append(r)
            if off_ok and not on_ok:
                offline_helps += 1
                who = "offline"
            else:
                who = _who(on_ok, off_ok)
                if who == "both_wrong":
                    both_wrong += 1
            table.append(
                {
                    "id": r["id"],
                    "n_chars": _chars(ref),
                    "ref": ref,
                    "online": on,
                    "offline": off,
                    "final": final,
                    "asr_err": round(kpi_err, 4),
                    "core_err": core_err,
                    "on_core": on_core if on is not None else None,
                    "off_core": off_core if off is not None else None,
                    "on_ok": on_ok,
                    "off_ok": off_ok,
                    "who": who,
                    "kind": _kind(ref, core_err),
                    "pick": r.get("arbiter_pick"),
                }
            )

    table.sort(key=lambda x: x["n_chars"])
    verdict = (
        "ALLOW_OFFLINE_AB" if offline_helps >= 2 else "STOP_MODEL_CEILING"
    )
    return {
        "n_asr_fail": len(core_fails),
        "n_asr_fail_kpi": len(kpi_fails),
        "offline_helps": offline_helps,
        "offline_helps_kpi": offline_helps_kpi,
        "both_wrong": both_wrong,
        "verdict": verdict,
        "rule": (
            "offline_helps / n_asr_fail 用 asr_err_core（阈值 0.35），"
            "不是 asr_err；>=2 → ALLOW_OFFLINE_AB，否则 STOP_MODEL_CEILING"
        ),
        "rows": table,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="zh_en ASR tail diag (asr_err_core)")
    ap.add_argument("--detail", type=Path, default=DEFAULT_DETAIL)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    detail = args.detail if args.detail.is_absolute() else ROOT / args.detail
    out = args.out if args.out.is_absolute() else ROOT / args.out
    if not detail.is_file():
        print(f"missing detail: {detail}", file=sys.stderr)
        return 1

    result = diagnose(detail)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"n_asr_fail={result['n_asr_fail']} "
        f"(kpi={result['n_asr_fail_kpi']}) "
        f"offline_helps={result['offline_helps']} "
        f"(kpi={result['offline_helps_kpi']}) "
        f"verdict={result['verdict']}"
    )
    for row in result["rows"]:
        print(
            f"  {row['id']} kind={row['kind']} core={row['core_err']} "
            f"who={row['who']} pick={row['pick']}"
        )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
