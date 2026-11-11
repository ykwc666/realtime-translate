"""从 Microsoft MSLT v1.1 抽样构建双人对话 KPI 集。

MSLT 布局（实测）：
  MSLT_Corpus/Data/MSLT_{Dev|Test}_{EN|ZH}_*/MSLT_*_NNNN.T0.{en|ch}.wav
  同 stem：.T2.{en|ch}.snt = 流畅转写；.T3.{zh|en}.snt = 互译参考
  注意：中文 wav/snt 扩展名是 ``ch``，不是 ``zh``；文件夹名仍是 ``_ZH_``。

用法：
  .venv\\Scripts\\python.exe scripts\\import_mslt_dialogue.py --zip data\\dialogue_corpus\\MSLT_Corpus.zip
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "dialogue_corpus"
RAW = OUT / "raw"
MANIFEST = OUT / "manifest.jsonl"

# 文件夹名含 EN/ZH；中文音频扩展名为 ch
_RE_WAV = re.compile(
    r"MSLT_Corpus/Data/(MSLT_(?:Dev|Test)_(?:EN|ZH)_[^/]+)/"
    r"(MSLT_[^/]+)\.T0\.(en|zh|ch)\.wav$",
    re.IGNORECASE,
)


def _read_member(zf: zipfile.ZipFile, name: str) -> str:
    """读完整 .snt（T2/T3 常为多行一句一段；禁止只取首行）。"""
    try:
        data = zf.read(name)
    except KeyError:
        return ""
    # MSLT .snt 实测多为 UTF-16 LE（含 BOM）；少数可能 UTF-8
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        text = data.decode("utf-16", errors="ignore")
    elif b"\x00" in data[:64]:
        text = data.decode("utf-16-le", errors="ignore")
    else:
        text = data.decode("utf-8", errors="ignore")
    # 去掉标注噪声标记，保留全文
    text = text.replace("<NON/>", " ").replace("<SPN/>", " ")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    # 中文行直接拼接；英文行用空格
    sample = "".join(lines)
    if any("\u4e00" <= ch <= "\u9fff" for ch in sample):
        return "".join(lines)
    return " ".join(lines)


def _collect_candidates(zf: zipfile.ZipFile) -> dict[str, list[dict]]:
    """返回 {'en_zh': [...], 'zh_en': [...]} 候选。"""
    buckets: dict[str, list[dict]] = {"en_zh": [], "zh_en": []}
    names = set(zf.namelist())
    for n in zf.namelist():
        m = _RE_WAV.search(n.replace("\\", "/"))
        if not m:
            continue
        folder, stem, src_ext = m.group(1), m.group(2), m.group(3).lower()
        base = f"MSLT_Corpus/Data/{folder}/{stem}"
        # 中文侧扩展名统一为 ch
        if src_ext in ("zh", "ch"):
            src_ext = "ch"
            direction = "zh_en"
            speaker = "A"
            tgt_snt = f"{base}.T3.en.snt"
        else:
            direction = "en_zh"
            speaker = "B"
            tgt_snt = f"{base}.T3.zh.snt"
        src_snt = f"{base}.T2.{src_ext}.snt"
        if src_snt not in names or tgt_snt not in names:
            src_snt = f"{base}.T1.{src_ext}.snt"
            if src_snt not in names or tgt_snt not in names:
                continue
        buckets[direction].append(
            {
                "wav_member": n,
                "src_member": src_snt,
                "tgt_member": tgt_snt,
                "direction": direction,
                "speaker": speaker,
                "stem": stem,
            }
        )
    return buckets


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--zip",
        type=str,
        default=str(OUT / "MSLT_Corpus.zip"),
    )
    ap.add_argument("--n-zh-en", type=int, default=30)
    ap.add_argument("--n-en-zh", type=int, default=30)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--prefer-split", choices=["Test", "Dev", "any"], default="Test")
    args = ap.parse_args()

    zpath = Path(args.zip)
    if not zpath.is_file():
        raise SystemExit(f"找不到 zip: {zpath}")

    OUT.mkdir(parents=True, exist_ok=True)
    if RAW.exists():
        shutil.rmtree(RAW)
    RAW.mkdir(parents=True)

    rnd = random.Random(args.seed)
    rows: list[dict] = []
    with zipfile.ZipFile(zpath, "r") as zf:
        buckets = _collect_candidates(zf)
        for direction, n_want in (("zh_en", args.n_zh_en), ("en_zh", args.n_en_zh)):
            cands = list(buckets.get(direction) or [])
            print(f"[mslt] {direction}: raw_pool={len(cands)}")
            if args.prefer_split != "any":
                pref = [c for c in cands if args.prefer_split in c["wav_member"]]
                if len(pref) >= n_want:
                    cands = pref
                print(f"[mslt] {direction}: after prefer={args.prefer_split} pool={len(cands)}")
            rnd.shuffle(cands)
            taken = 0
            for c in cands:
                if taken >= n_want:
                    break
                src_ref = _read_member(zf, c["src_member"])
                tgt_ref = _read_member(zf, c["tgt_member"])
                if not src_ref or not tgt_ref:
                    continue
                did = f"mslt_{direction}_{taken:03d}"
                dest = RAW / f"{did}.wav"
                with zf.open(c["wav_member"]) as src, dest.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                rows.append(
                    {
                        "id": did,
                        "wav": str(dest.relative_to(OUT)).replace("\\", "/"),
                        "speaker": c["speaker"],
                        "direction": direction,
                        "src_ref": src_ref,
                        "tgt_ref": tgt_ref,
                        "notes": f"MSLT {c['stem']}",
                        "source_corpus": "MSLT_v1.1",
                    }
                )
                taken += 1
            print(f"[mslt] {direction}: take={taken}")

    with MANIFEST.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    n_zh = sum(1 for r in rows if r["direction"] == "zh_en")
    n_en = sum(1 for r in rows if r["direction"] == "en_zh")
    missing = sum(1 for r in rows if not r["src_ref"] or not r["tgt_ref"])
    print(f"[mslt] wrote {MANIFEST}")
    print(f"[mslt] zh_en={n_zh} en_zh={n_en} total={len(rows)} missing_ref={missing}")
    if n_zh < args.n_zh_en or n_en < args.n_en_zh:
        print("[mslt] FAIL: not enough samples")
        return 1
    if missing:
        return 1
    for r in rows[:2] + rows[-2:]:
        print(f"  {r['id']} [{r['direction']}] {r['src_ref'][:40]!r} -> {r['tgt_ref'][:40]!r}")
    print("[mslt] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
