"""P1-8: sweep CT2 intra_threads × beam_size on call eval (zh→en + en→zh).

Usage:
  python scripts/bench_mt_threads.py
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402
from app.mt_postedit import postedit_en_zh, postedit_zh_en  # noqa: E402


def _load_pairs() -> list[tuple[str, str]]:
    """(src_lang, text) from eval sets."""
    out: list[tuple[str, str]] = []
    zh_path = ROOT / "data" / "mt_call_eval.jsonl"
    if zh_path.exists():
        for line in zh_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            out.append(("zh", row["zh"]))
    en_path = ROOT / "data" / "mt_call_eval_en_zh.jsonl"
    if en_path.exists():
        for line in en_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            out.append(("en", row["en"]))
    return out


def _make_mt(threads: int):
    import ctranslate2
    from transformers import AutoTokenizer

    tok_zh = AutoTokenizer.from_pretrained(str(config.OPUS_ZH_EN))
    tok_en = AutoTokenizer.from_pretrained(str(config.OPUS_EN_ZH))
    zh = ctranslate2.Translator(
        str(config.OPUS_ZH_EN_CT2),
        device="cpu",
        compute_type="int8",
        inter_threads=1,
        intra_threads=threads,
    )
    en = ctranslate2.Translator(
        str(config.OPUS_EN_ZH_CT2),
        device="cpu",
        compute_type="int8",
        inter_threads=1,
        intra_threads=threads,
    )
    return tok_zh, tok_en, zh, en


def _tr(tok, mt, text: str, beam: int) -> tuple[str, float]:
    t0 = time.perf_counter()
    ids = tok.encode(text, truncation=True, max_length=256)
    tokens = tok.convert_ids_to_tokens(ids)
    out = mt.translate_batch([tokens], beam_size=beam, max_decoding_length=128)
    hyp = tok.decode(
        tok.convert_tokens_to_ids(out[0].hypotheses[0]), skip_special_tokens=True
    ).strip()
    return hyp, (time.perf_counter() - t0) * 1000


def main() -> None:
    pairs = _load_pairs()
    print(f"pairs={len(pairs)}")
    thread_grid = [2, 4, 6]
    beam_grid = [1, 2]
    # warm with default
    tok_zh, tok_en, mt_zh, mt_en = _make_mt(4)
    for _ in range(2):
        _tr(tok_zh, mt_zh, "你好，世界", 1)
        _tr(tok_en, mt_en, "hello world", 1)

    rows_out: list[dict] = []
    print(f"{'threads':>7} {'beam':>4} | {'med':>7} {'mean':>7} {'p90':>7} | sample hyp")
    print("-" * 80)

    for threads in thread_grid:
        tok_zh, tok_en, mt_zh, mt_en = _make_mt(threads)
        # re-warm this instance
        _tr(tok_zh, mt_zh, "你好，世界", 1)
        _tr(tok_en, mt_en, "hello world", 1)
        for beam in beam_grid:
            times: list[float] = []
            hyps: list[str] = []
            for src_lang, text in pairs:
                if src_lang == "zh":
                    hyp, ms = _tr(tok_zh, mt_zh, text, beam)
                    hyp = postedit_zh_en(text, hyp)
                else:
                    hyp, ms = _tr(tok_en, mt_en, text, beam)
                    hyp = postedit_en_zh(text, hyp)
                times.append(ms)
                hyps.append(hyp)
            med = statistics.median(times)
            mean = statistics.mean(times)
            p90 = sorted(times)[int(0.9 * (len(times) - 1))]
            sample = hyps[0][:40] if hyps else ""
            print(
                f"{threads:7d} {beam:4d} | {med:7.1f} {mean:7.1f} {p90:7.1f} | {sample}"
            )
            rows_out.append(
                {
                    "threads": threads,
                    "beam": beam,
                    "n": len(times),
                    "median_ms": round(med, 2),
                    "mean_ms": round(mean, 2),
                    "p90_ms": round(p90, 2),
                    "hyps": hyps,
                }
            )

    # quality: beam1 vs beam2 exact-match rate vs postedit canonical (same text)
    b1 = next(r for r in rows_out if r["threads"] == 4 and r["beam"] == 1)
    b2 = next(r for r in rows_out if r["threads"] == 4 and r["beam"] == 2)
    same = sum(1 for a, b in zip(b1["hyps"], b2["hyps"]) if a == b)
    print("-" * 80)
    print(f"beam1 vs beam2 identical hyps @threads=4: {same}/{len(b1['hyps'])}")

    # pick fastest median among beam=1 (quality-preserving)
    candidates = [r for r in rows_out if r["beam"] == 1]
    best = min(candidates, key=lambda r: r["median_ms"])
    print(
        f"recommend beam=1 threads={best['threads']} "
        f"(median {best['median_ms']}ms; current MT_THREADS={config.MT_THREADS})"
    )

    out = ROOT / "docs" / "mt_threads_ab.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for r in rows_out:
            slim = {k: v for k, v in r.items() if k != "hyps"}
            slim["hyp0"] = r["hyps"][0] if r["hyps"] else ""
            f.write(json.dumps(slim, ensure_ascii=False) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
