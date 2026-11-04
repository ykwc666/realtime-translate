"""A/B: CT2(+postedit) vs exact TM on data/mt_tm.jsonl.

Reports hit rate, median ms, and whether TM tgt differs from raw CT2.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.mt_postedit import postedit_en_zh, postedit_zh_en  # noqa: E402
from app.mt_tm import lookup_tm, normalize_tm_key, reload_tm  # noqa: E402
from app.translate import Translator  # noqa: E402


def _load_rows() -> list[dict]:
    path = config.MT_TM_PATH
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def main() -> None:
    reload_tm()
    rows = _load_rows()
    mt = Translator()
    # warm CT2 both directions (non-TM)
    config.MT_TM_ENABLED = False
    _ = mt.translate("你好，世界", src_lang="zh")
    _ = mt.translate("hello world", src_lang="en")

    ct2_ms: list[float] = []
    tm_ms: list[float] = []
    same = 0
    differ = 0
    miss = 0
    out_path = config.ROOT / "docs" / "mt_tm_ab.jsonl"
    out_lines: list[str] = []

    print(f"entries={len(rows)}  path={config.MT_TM_PATH}")
    print(f"{'src':<42} | {'ct2_ms':>7} | {'tm_ms':>6} | note")
    print("-" * 90)

    for row in rows:
        src = row["src"]
        tgt = row["tgt"]
        direction = row["dir"]
        src_lang = "zh" if direction == "zh-en" else "en"

        config.MT_TM_ENABLED = False
        t0 = time.perf_counter()
        raw = mt._translate_ct2(src, src_lang)  # noqa: SLF001
        if src_lang == "zh":
            pe = postedit_zh_en(src, raw)
        else:
            pe = postedit_en_zh(src, raw)
        c_ms = (time.perf_counter() - t0) * 1000
        ct2_ms.append(c_ms)

        config.MT_TM_ENABLED = True
        t1 = time.perf_counter()
        hit = lookup_tm(src, src_lang)
        t_ms = (time.perf_counter() - t1) * 1000
        tm_ms.append(t_ms)

        if hit is None:
            miss += 1
            note = "MISS"
        elif normalize_tm_key(hit, "en" if src_lang == "zh" else "zh") == normalize_tm_key(
            pe, "en" if src_lang == "zh" else "zh"
        ):
            same += 1
            note = "same"
        else:
            differ += 1
            note = "FIX" if pe != hit else "diff"

        line = {
            "dir": direction,
            "src": src,
            "tm": tgt,
            "ct2_pe": pe,
            "ct2_ms": round(c_ms, 2),
            "tm_ms": round(t_ms, 3),
            "note": note,
        }
        out_lines.append(json.dumps(line, ensure_ascii=False))
        print(f"{src[:42]:<42} | {c_ms:7.1f} | {t_ms:6.3f} | {note}")

    out_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print("-" * 90)
    print(
        f"ct2+pe median={statistics.median(ct2_ms):.1f}ms  "
        f"tm median={statistics.median(tm_ms):.3f}ms  "
        f"same={same} FIX/diff={differ} miss={miss}"
    )
    print(f"wrote {out_path}")
    config.MT_TM_ENABLED = True


if __name__ == "__main__":
    main()
