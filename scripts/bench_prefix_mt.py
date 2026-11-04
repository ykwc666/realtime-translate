"""A/B baseline CT2 vs LoRA-CT2 on truncated ZH prefixes."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ctranslate2  # noqa: E402
from transformers import AutoTokenizer  # noqa: E402

SEED = ROOT / "data" / "prefix_mt_seed.jsonl"
LOG_MD = ROOT / "docs" / "OPTIMIZATION_LOG.md"
LOG_JSONL = ROOT / "docs" / "bench_history.jsonl"


def load_translator(model_dir: Path, tok_dir: Path, threads: int = 4):
    tok = AutoTokenizer.from_pretrained(str(tok_dir))
    mt = ctranslate2.Translator(
        str(model_dir),
        device="cpu",
        compute_type="int8",
        inter_threads=1,
        intra_threads=threads,
    )
    return tok, mt


def translate(tok, mt, text: str) -> tuple[str, float]:
    ids = tok.encode(text, truncation=True, max_length=256)
    tokens = tok.convert_ids_to_tokens(ids)
    t0 = time.perf_counter()
    out = mt.translate_batch([tokens], beam_size=1, max_decoding_length=128)
    ms = (time.perf_counter() - t0) * 1000
    hyp = out[0].hypotheses[0]
    text_out = tok.decode(tok.convert_tokens_to_ids(hyp), skip_special_tokens=True).strip()
    return text_out, ms


def make_cases() -> list[dict]:
    rows = []
    with SEED.open(encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    cases = []
    # fixed eval prefixes (not only training ratios)
    for row in rows[:12]:
        zh = row["zh"]
        en = row["en"]
        n = len(zh)
        for r, tag in ((0.4, "p40"), (0.6, "p60"), (1.0, "full")):
            k = max(2, int(n * r))
            cases.append(
                {
                    "tag": tag,
                    "zh": zh[:k],
                    "full_zh": zh,
                    "ref_en": en,
                }
            )
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-ct2", default=str(ROOT / "models" / "opus-mt-zh-en-ct2")
    )
    parser.add_argument(
        "--lora-ct2", default=str(ROOT / "models" / "opus-mt-zh-en-lora-ct2")
    )
    parser.add_argument("--tok", default=str(ROOT / "models" / "opus-mt-zh-en"))
    args = parser.parse_args()

    lora_ct2 = Path(args.lora_ct2)
    if not lora_ct2.exists():
        print("missing lora ct2", lora_ct2)
        return 2

    cases = make_cases()
    base_tok, base_mt = load_translator(Path(args.base_ct2), Path(args.tok))
    lora_tok, lora_mt = load_translator(lora_ct2, Path(args.tok))

    # warmup
    translate(base_tok, base_mt, "测试")
    translate(lora_tok, lora_mt, "测试")

    results = []
    base_ms = []
    lora_ms = []
    for c in cases:
        b_txt, b_t = translate(base_tok, base_mt, c["zh"])
        l_txt, l_t = translate(lora_tok, lora_mt, c["zh"])
        base_ms.append(b_t)
        lora_ms.append(l_t)
        results.append(
            {
                **c,
                "base": b_txt,
                "lora": l_txt,
                "base_ms": round(b_t, 1),
                "lora_ms": round(l_t, 1),
            }
        )
        print(f"[{c['tag']}] {c['zh']}")
        print(f"  base: {b_txt}")
        print(f"  lora: {l_txt}")
        print(f"  ref : {c['ref_en']}")

    def med(xs: list[float]) -> float:
        s = sorted(xs)
        return s[len(s) // 2]

    out = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tag": "prefix-mt-lora-ab",
        "note": "Opus-MT LoRA prefix vs baseline CT2",
        "mode": "prefix_mt_ab",
        "n_cases": len(cases),
        "base_ms_median": round(med(base_ms), 1),
        "lora_ms_median": round(med(lora_ms), 1),
        "samples": results[:8],
    }
    LOG_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with LOG_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False) + "\n")
    md = (
        f"| {out['time']} | {out['tag']} | base_ms={out['base_ms_median']} "
        f"lora_ms={out['lora_ms_median']} n={out['n_cases']} | prefix LoRA A/B |"
    )
    if LOG_MD.exists():
        text = LOG_MD.read_text(encoding="utf-8")
        LOG_MD.write_text(text.rstrip() + "\n" + md + "\n", encoding="utf-8")

    # also dump full side-by-side for inspection
    dump = ROOT / "docs" / "prefix_mt_lora_ab.json"
    dump.write_text(json.dumps(out | {"all": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: out[k] for k in out if k != "samples"}, ensure_ascii=False, indent=2))
    print("PREFIX_MT_AB_OK", dump)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
