"""从 Microsoft MSLT v1.1 抽样构建双人对话 KPI 集。

MSLT 布局（实测）：
  MSLT_Corpus/Data/MSLT_{Dev|Test}_{EN|ZH}_*/MSLT_*_NNNN.T0.{en|ch}.wav
  同 stem：.T1 = 逐字；.T2 = 顺滑（src_ref）；.T3 = 互译（tgt_ref）
  注意：中文 wav/snt 扩展名是 ``ch``，不是 ``zh``；文件夹名仍是 ``_ZH_``。

音频对齐的是 T1。抽样前必须用 T1↔T2 pair_sim 过滤，否则中文近半是「另一句话」。

用法：
  .venv\\Scripts\\python.exe scripts\\import_mslt_dialogue.py --zip data\\dialogue_corpus\\MSLT_Corpus.zip
  .venv\\Scripts\\python.exe scripts\\import_mslt_dialogue.py --out data\\dialogue_corpus_v2 --prefer-split Test --n-zh-en 60 --n-en-zh 60
"""

from __future__ import annotations

import argparse
import difflib
import json
import random
import re
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "dialogue_corpus"

# 文件夹名含 EN/ZH；中文音频扩展名为 ch
_RE_WAV = re.compile(
    r"MSLT_Corpus/Data/(MSLT_(?:Dev|Test)_(?:EN|ZH)_[^/]+)/"
    r"(MSLT_[^/]+)\.T0\.(en|zh|ch)\.wav$",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def _read_member(zf: zipfile.ZipFile, name: str) -> str:
    """读完整 .snt（T1/T2/T3 常为多行；禁止只取首行）。"""
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
    sample = "".join(lines)
    if any("\u4e00" <= ch <= "\u9fff" for ch in sample):
        return "".join(lines)
    return " ".join(lines)


def _norm_pair_text(s: str) -> str:
    """去 MSLT 标注标签 → 去标点/空白 → 小写。"""
    t = _TAG_RE.sub(" ", s or "")
    t = _PUNCT_RE.sub("", t)
    return t.lower()


def _pair_sim(t1: str, t2: str) -> float:
    """空串返回 0.0；否则 SequenceMatcher.ratio()。"""
    a = _norm_pair_text(t1)
    b = _norm_pair_text(t2)
    if not a or not b:
        return 0.0
    return float(difflib.SequenceMatcher(None, a, b).ratio())


def _collect_candidates(zf: zipfile.ZipFile) -> dict[str, list[dict]]:
    """返回 {'en_zh': [...], 'zh_en': [...]}；每项含 t1/t2/t3 member。"""
    buckets: dict[str, list[dict]] = {"en_zh": [], "zh_en": []}
    names = set(zf.namelist())
    for n in zf.namelist():
        m = _RE_WAV.search(n.replace("\\", "/"))
        if not m:
            continue
        folder, stem, src_ext = m.group(1), m.group(2), m.group(3).lower()
        base = f"MSLT_Corpus/Data/{folder}/{stem}"
        if src_ext in ("zh", "ch"):
            src_ext = "ch"
            direction = "zh_en"
            speaker = "A"
            tgt_snt = f"{base}.T3.en.snt"
        else:
            direction = "en_zh"
            speaker = "B"
            tgt_snt = f"{base}.T3.zh.snt"
        t1_snt = f"{base}.T1.{src_ext}.snt"
        t2_snt = f"{base}.T2.{src_ext}.snt"
        if t1_snt not in names or t2_snt not in names or tgt_snt not in names:
            continue
        buckets[direction].append(
            {
                "wav_member": n,
                "t1_member": t1_snt,
                "t2_member": t2_snt,
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
        default=str(DEFAULT_OUT / "MSLT_Corpus.zip"),
    )
    ap.add_argument(
        "--out",
        type=str,
        default=str(DEFAULT_OUT),
        help="输出目录（默认 data/dialogue_corpus；v2 用独立目录）",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="目标目录已有 manifest.jsonl 时允许覆盖（会清空 raw/）",
    )
    ap.add_argument("--n-zh-en", type=int, default=30)
    ap.add_argument("--n-en-zh", type=int, default=30)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--prefer-split", choices=["Test", "Dev", "any"], default="Test")
    ap.add_argument(
        "--min-pair-sim",
        type=float,
        default=0.5,
        help="T1↔T2 相似度下限（默认 0.5；本轮禁止下调）",
    )
    args = ap.parse_args()

    zpath = Path(args.zip)
    if not zpath.is_file():
        raise SystemExit(f"找不到 zip: {zpath}")

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    raw = out / "raw"
    manifest = out / "manifest.jsonl"
    rejected_path = out / "rejected.jsonl"

    if manifest.is_file() and not args.force:
        raise SystemExit(
            f"[mslt] 拒绝覆盖：已存在 {manifest}。"
            f" 换 --out 或显式传 --force（会清空 {raw}）。"
        )

    out.mkdir(parents=True, exist_ok=True)
    if raw.exists():
        shutil.rmtree(raw)
    raw.mkdir(parents=True)

    min_sim = float(args.min_pair_sim)
    rnd = random.Random(args.seed)
    rows: list[dict] = []
    rejected: list[dict] = []
    filter_stats: dict[str, dict] = {}

    with zipfile.ZipFile(zpath, "r") as zf:
        buckets = _collect_candidates(zf)
        for direction, n_want in (("zh_en", args.n_zh_en), ("en_zh", args.n_en_zh)):
            cands = list(buckets.get(direction) or [])
            raw_pool = len(cands)
            print(f"[mslt] {direction}: raw_pool={raw_pool}")
            if args.prefer_split != "any":
                cands = [c for c in cands if args.prefer_split in c["wav_member"]]
                print(
                    f"[mslt] {direction}: after prefer={args.prefer_split} pool={len(cands)}"
                )

            # 先过滤再抽样（顺序不能反）
            valid: list[dict] = []
            n_empty = 0
            n_mismatch = 0
            for c in cands:
                t1 = _read_member(zf, c["t1_member"])
                t2 = _read_member(zf, c["t2_member"])
                tgt = _read_member(zf, c["tgt_member"])
                if not t1 or not t2 or not tgt:
                    n_empty += 1
                    rejected.append(
                        {
                            "id": None,
                            "notes": f"MSLT {c['stem']}",
                            "reason": "empty_tier",
                            "pair_sim": _pair_sim(t1, t2),
                            "t1": t1,
                            "t2": t2,
                            "direction": direction,
                        }
                    )
                    continue
                sim = _pair_sim(t1, t2)
                if sim < min_sim:
                    n_mismatch += 1
                    rejected.append(
                        {
                            "id": None,
                            "notes": f"MSLT {c['stem']}",
                            "reason": "pair_mismatch",
                            "pair_sim": round(sim, 4),
                            "t1": t1,
                            "t2": t2,
                            "direction": direction,
                        }
                    )
                    continue
                valid.append({**c, "t1": t1, "t2": t2, "tgt": tgt, "pair_sim": sim})

            print(
                f"[mslt] {direction}: valid={len(valid)} "
                f"drop_empty={n_empty} drop_mismatch={n_mismatch}"
            )
            filter_stats[direction] = {
                "raw_pool": raw_pool,
                "after_prefer": len(cands),
                "drop_empty_tier": n_empty,
                "drop_pair_mismatch": n_mismatch,
                "valid": len(valid),
                "need": n_want,
            }
            if len(valid) < n_want:
                print(
                    f"[mslt] FAIL: valid={len(valid)} need={n_want} ({direction})"
                )
                return 2

            rnd.shuffle(valid)
            taken = 0
            for c in valid:
                if taken >= n_want:
                    break
                did = f"mslt_{direction}_{taken:03d}"
                dest = raw / f"{did}.wav"
                with zf.open(c["wav_member"]) as src, dest.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                rows.append(
                    {
                        "id": did,
                        "wav": str(dest.relative_to(out)).replace("\\", "/"),
                        "speaker": c["speaker"],
                        "direction": direction,
                        "src_ref": c["t2"],
                        "tgt_ref": c["tgt"],
                        "src_verbatim": c["t1"],
                        "src_tier": "T2",
                        "pair_sim": round(float(c["pair_sim"]), 4),
                        "notes": f"MSLT {c['stem']}",
                        "source_corpus": "MSLT_v1.1",
                        "prefer_split": args.prefer_split,
                    }
                )
                taken += 1
            filter_stats[direction]["taken"] = taken
            print(f"[mslt] {direction}: take={taken}")

    with rejected_path.open("w", encoding="utf-8") as f:
        for r in rejected:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    with manifest.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    n_zh = sum(1 for r in rows if r["direction"] == "zh_en")
    n_en = sum(1 for r in rows if r["direction"] == "en_zh")
    missing = sum(1 for r in rows if not r["src_ref"] or not r["tgt_ref"])
    sims = [float(r["pair_sim"]) for r in rows]
    print(f"[mslt] wrote {manifest}")
    print(f"[mslt] wrote {rejected_path} n_rejected={len(rejected)}")
    print(f"[mslt] zh_en={n_zh} en_zh={n_en} total={len(rows)} missing_ref={missing}")
    print(f"[mslt] pair_sim min={min(sims):.4f} p50={sorted(sims)[len(sims)//2]:.4f}")
    print(f"[mslt] filter_stats={json.dumps(filter_stats, ensure_ascii=False)}")
    if n_zh < args.n_zh_en or n_en < args.n_en_zh:
        print("[mslt] FAIL: not enough samples")
        return 1
    if missing:
        return 1
    if min(sims) < min_sim:
        print("[mslt] FAIL: pair_sim below threshold in manifest")
        return 1
    for r in rows[:2] + rows[-2:]:
        print(
            f"  {r['id']} [{r['direction']}] sim={r['pair_sim']:.2f} "
            f"{r['src_ref'][:40]!r} -> {r['tgt_ref'][:40]!r}"
        )
    print("[mslt] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
