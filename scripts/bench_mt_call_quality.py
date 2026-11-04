"""Eval call-domain ZH→EN: base CT2 vs optional LoRA CT2 vs +postedit.

python scripts/bench_mt_call_quality.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402
from app.mt_postedit import postedit_zh_en  # noqa: E402


def _load_ct2(model_dir: Path):
    import ctranslate2
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(str(config.OPUS_ZH_EN))
    mt = ctranslate2.Translator(
        str(model_dir),
        device="cpu",
        compute_type="int8",
        inter_threads=1,
        intra_threads=max(1, int(config.MT_THREADS)),
    )
    return tok, mt


def _tr(tok, mt, text: str) -> tuple[str, float]:
    t0 = time.perf_counter()
    ids = tok.encode(text, truncation=True, max_length=256)
    tokens = tok.convert_ids_to_tokens(ids)
    out = mt.translate_batch([tokens], beam_size=1, max_decoding_length=128)
    hyp = tok.decode(
        tok.convert_tokens_to_ids(out[0].hypotheses[0]), skip_special_tokens=True
    ).strip()
    return hyp, (time.perf_counter() - t0) * 1000


def _score(hyp: str, ref: str) -> dict:
    h = re_norm(hyp)
    r = re_norm(ref)
    return {
        "exact": h == r,
        "ref_in_hyp": r in h or h in r,
        "hit_reply": ("reply" in h) if "reply" in r else None,
        "hit_round": ("round" in h) if "round" in r else None,
        "bad_oneside": "one side" in h,
        "bad_heard_back_me": "heard back from me" in h or "news of me" in h,
    }


def re_norm(s: str) -> str:
    return " ".join((s or "").lower().replace(".", " ").replace("?", " ").split())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--eval", default=str(ROOT / "data" / "mt_call_eval.jsonl")
    )
    parser.add_argument(
        "--lora",
        default=str(ROOT / "models" / "opus-mt-zh-en-lora-ct2"),
        help="optional LoRA CT2 dir; empty to skip",
    )
    args = parser.parse_args()

    rows = []
    with Path(args.eval).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    base_dir = getattr(config, "OPUS_ZH_EN_CT2_BASE", None) or config.OPUS_ZH_EN_CT2
    print("load stock CT2…", Path(base_dir).name)
    tok_b, mt_b = _load_ct2(Path(base_dir))
    tok_l = mt_l = None
    lora_dir = Path(args.lora) if args.lora else Path(config.OPUS_ZH_EN_CT2)
    if lora_dir.exists() and lora_dir.resolve() != Path(base_dir).resolve():
        print("load call LoRA CT2…", lora_dir.name)
        tok_l, mt_l = _load_ct2(lora_dir)
    elif lora_dir.resolve() == Path(base_dir).resolve():
        print("skip lora column (same as stock)")

    tallies = {
        "base": {"n": 0, "bad_oneside": 0, "bad_heard": 0, "hit_reply": 0, "hit_round": 0},
        "lora": {"n": 0, "bad_oneside": 0, "bad_heard": 0, "hit_reply": 0, "hit_round": 0},
        "base+pe": {"n": 0, "bad_oneside": 0, "bad_heard": 0, "hit_reply": 0, "hit_round": 0},
    }

    out_path = ROOT / "docs" / "mt_call_quality_ab.jsonl"
    with out_path.open("w", encoding="utf-8") as fout:
        for row in rows:
            zh, ref = row["zh"], row["en"]
            base, bms = _tr(tok_b, mt_b, zh)
            pe = postedit_zh_en(zh, base)
            rec = {
                "id": row.get("id"),
                "zh": zh,
                "ref": ref,
                "base": base,
                "base_ms": round(bms, 1),
                "base_pe": pe,
            }
            sb = _score(base, ref)
            sp = _score(pe, ref)
            for key, sc in (("base", sb), ("base+pe", sp)):
                tallies[key]["n"] += 1
                tallies[key]["bad_oneside"] += int(sc["bad_oneside"])
                tallies[key]["bad_heard"] += int(sc["bad_heard_back_me"])
                if sc["hit_reply"] is True:
                    tallies[key]["hit_reply"] += 1
                if sc["hit_round"] is True:
                    tallies[key]["hit_round"] += 1

            if tok_l is not None:
                lora, lms = _tr(tok_l, mt_l, zh)
                rec["lora"] = lora
                rec["lora_ms"] = round(lms, 1)
                sl = _score(lora, ref)
                tallies["lora"]["n"] += 1
                tallies["lora"]["bad_oneside"] += int(sl["bad_oneside"])
                tallies["lora"]["bad_heard"] += int(sl["bad_heard_back_me"])
                if sl["hit_reply"] is True:
                    tallies["lora"]["hit_reply"] += 1
                if sl["hit_round"] is True:
                    tallies["lora"]["hit_round"] += 1

            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"\n[{row.get('id')}] {zh}")
            print(f"  ref : {ref}")
            print(f"  base: {base}")
            if pe != base:
                print(f"  pe  : {pe}")
            if "lora" in rec:
                print(f"  lora: {rec['lora']}")

    print("\n=== tallies ===")
    print(json.dumps(tallies, ensure_ascii=False, indent=2))
    print("wrote", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
