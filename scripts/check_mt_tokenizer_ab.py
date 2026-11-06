"""Compare CT2 outputs: SpmTokenizer vs transformers AutoTokenizer. DIFF must be 0."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ctranslate2
from transformers import AutoTokenizer

from app import config
from app.mt_spm import SpmTokenizer


def _load_lines(path: Path, field: str) -> list[str]:
    out: list[str] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            text = (row.get(field) or "").strip()
            if text:
                out.append(text)
    return out


def _ct2_once(mt, tok, text: str) -> str:
    ids = tok.encode(text, truncation=True, max_length=256)
    tokens = tok.convert_ids_to_tokens(ids)
    results = mt.translate_batch(
        [tokens],
        beam_size=int(getattr(config, "MT_DECODE_BEAM", 1)),
        no_repeat_ngram_size=int(getattr(config, "MT_NO_REPEAT_NGRAM", 0)),
        repetition_penalty=float(getattr(config, "MT_REPETITION_PENALTY", 1.0)),
        max_decoding_length=128,
    )
    out_tokens = results[0].hypotheses[0]
    return tok.decode(
        tok.convert_tokens_to_ids(out_tokens), skip_special_tokens=True
    ).strip()


def _run_dir(label: str, texts: list[str], mt, tok_spm, tok_hf) -> tuple[int, int]:
    same = 0
    diff = 0
    for i, text in enumerate(texts):
        a = _ct2_once(mt, tok_spm, text)
        b = _ct2_once(mt, tok_hf, text)
        if a == b:
            same += 1
        else:
            diff += 1
            if diff <= 5:
                print(f"[{label}] DIFF#{diff} i={i}")
                print(f"  src: {text[:120]}")
                print(f"  spm: {a[:120]}")
                print(f"  hf : {b[:120]}")
    print(f"{label}: SAME={same} DIFF={diff}")
    return same, diff


def main() -> int:
    threads = max(1, int(config.MT_THREADS))
    mt_zh_en = ctranslate2.Translator(
        str(config.OPUS_ZH_EN_CT2),
        device="cpu",
        compute_type="int8",
        inter_threads=1,
        intra_threads=threads,
    )
    mt_en_zh = ctranslate2.Translator(
        str(config.OPUS_EN_ZH_CT2),
        device="cpu",
        compute_type="int8",
        inter_threads=1,
        intra_threads=threads,
    )

    spm_zh_en = SpmTokenizer(
        config.OPUS_ZH_EN / "source.spm",
        config.OPUS_ZH_EN / "target.spm",
    )
    spm_en_zh = SpmTokenizer(
        config.OPUS_EN_ZH / "source.spm",
        config.OPUS_EN_ZH / "target.spm",
    )
    hf_zh_en = AutoTokenizer.from_pretrained(str(config.OPUS_ZH_EN))
    hf_en_zh = AutoTokenizer.from_pretrained(str(config.OPUS_EN_ZH))

    zh_texts = _load_lines(ROOT / "data" / "mt_call_train.jsonl", "zh")
    zh_texts += _load_lines(ROOT / "data" / "mt_call_eval.jsonl", "zh")
    en_texts = _load_lines(ROOT / "data" / "mt_call_eval_en_zh.jsonl", "en")

    _, d_zh = _run_dir("zh→en", zh_texts, mt_zh_en, spm_zh_en, hf_zh_en)
    _, d_en = _run_dir("en→zh", en_texts, mt_en_zh, spm_en_zh, hf_en_zh)
    return 0 if d_zh == 0 and d_en == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
