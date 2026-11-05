"""Split-ledger eval: WER / fragment / pipeline vs MT-only / A5 ASR modes.

python scripts/eval_ledger.py
python scripts/eval_ledger.py --limit 20   # smoke
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402
from app.audio_io import load_wav  # noqa: E402
from app.dual_track import DualTrackEngine  # noqa: E402
from app.pipeline import TranslatePipeline  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from scan_repetition import find_repetitions  # noqa: E402

SR = int(config.SAMPLE_RATE)
BLOCK = int(SR * 0.1)
FUNC_ENDS = {
    "the",
    "a",
    "an",
    "to",
    "of",
    "and",
    "that",
    "he",
    "she",
    "was",
    "is",
}
MANIFEST = ROOT / "data" / "ledger_corpus" / "manifest.jsonl"
OUT_DIR = ROOT / "docs"


def _norm_words(text: str) -> list[str]:
    t = (text or "").lower()
    t = re.sub(r"[^a-z0-9'\s]+", " ", t)
    return [w for w in t.split() if w]


def wer(ref: str, hyp: str) -> float:
    r = _norm_words(ref)
    h = _norm_words(hyp)
    if not r:
        return 0.0 if not h else 1.0
    # classic DP Levenshtein on words
    n, m = len(r), len(h)
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, m + 1):
            cur = dp[j]
            if r[i - 1] == h[j - 1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j - 1])
            prev = cur
    return dp[m] / float(n)


# EN stopwords for content-word coverage (en→zh model-judge).
_EN_STOP = {
    "the",
    "a",
    "an",
    "to",
    "of",
    "and",
    "or",
    "but",
    "that",
    "this",
    "these",
    "those",
    "it",
    "its",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "am",
    "i",
    "you",
    "he",
    "she",
    "we",
    "they",
    "me",
    "my",
    "your",
    "our",
    "their",
    "in",
    "on",
    "at",
    "for",
    "with",
    "as",
    "by",
    "from",
    "if",
    "so",
    "not",
    "no",
    "do",
    "does",
    "did",
    "can",
    "could",
    "would",
    "should",
    "will",
    "just",
    "about",
    "into",
    "than",
    "then",
    "there",
    "here",
    "up",
    "out",
    "what",
    "when",
    "which",
    "who",
    "how",
    "um",
    "uh",
    "oh",
    "yeah",
    "yes",
    "ok",
    "okay",
}

def content_words(ref_en: str) -> list[str]:
    return [
        w
        for w in _norm_words(ref_en)
        if w not in _EN_STOP and len(w) >= 3 and not w.isdigit()
    ]


def content_density(ref_en: str, tgt_zh: str) -> float:
    """CJK chars per EN content word — proxy for content coverage (no bilingual dict)."""
    words = content_words(ref_en)
    if not words:
        return 99.0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", tgt_zh or ""))
    return cjk / float(len(words))


# Calibrated: mt_only density p10≈1.8 on ledger; garbage/lang-flip pipeline <<1.
# See docs/LEDGER_GATE_TIGHTEN.md.
CONTENT_DENSITY_MIN = 1.5
# Pipeline-only: if ASR src WER vs ref exceeds this, cannot be mt_faithful ok.
ASR_WER_OK_MAX = 0.45


def model_judge_mt(
    ref_en: str,
    tgt_zh: str,
    *,
    src: str | None = None,
    content_density_min: float = CONTENT_DENSITY_MIN,
    asr_wer_ok_max: float = ASR_WER_OK_MAX,
) -> tuple[str, str]:
    """Honest model-judge of zh fidelity to English ref (not ASR src)."""
    ref = (ref_en or "").strip()
    tgt = (tgt_zh or "").strip()
    # Pipeline en→zh: ASR src must not be CJK (lang flip).
    if src is not None:
        src_s = (src or "").strip()
        if re.search(r"[\u4e00-\u9fff]", src_s):
            return "wrong", "lang_flip"
        # Content alignment via ASR fidelity: bad ASR ⇒ cannot claim ok vs ref.
        if src_s:
            w = wer(ref, src_s)
            if w > float(asr_wer_ok_max):
                return "wrong", f"asr_wer={w:.2f}>{asr_wer_ok_max}"
    if not tgt:
        return "wrong", "empty tgt"
    reps = find_repetitions(tgt)
    if reps and reps[0]["times"] >= 3:
        return "wrong", f"repetition {reps[0]['span']!r}"
    # must be mostly CJK for en→zh
    cjk = len(re.findall(r"[\u4e00-\u9fff]", tgt))
    if cjk < max(2, len(ref.split()) // 4):
        return "wrong", f"too little CJK ({cjk}) for ref_words={len(ref.split())}"
    # length sanity vs ref word count
    ratio = cjk / max(len(ref.split()), 1)
    if ratio < 0.4:
        return "wrong", f"under-translation ratio={ratio:.2f}"
    if ratio > 6.0:
        return "awkward", f"over-long ratio={ratio:.2f}"
    # mid-quality: if ref has clear content words and tgt is short relative
    if len(ref.split()) >= 8 and cjk < 6:
        return "wrong", "long ref but short zh"
    dens = content_density(ref, tgt)
    if content_words(ref) and dens < float(content_density_min):
        return "wrong", f"content_density={dens:.2f}<{content_density_min}"
    if ratio < 0.8:
        return "awkward", f"thin coverage ratio={ratio:.2f}"
    return "ok", f"gates pass dens={dens:.2f}"


def run_pipeline_file(
    pipe: TranslatePipeline,
    wav: Path,
    *,
    asr_mode: str = "merged",
) -> list[dict]:
    """Feed wav through DualTrack; asr_mode in {merged, online, offline}."""
    raw = load_wav(str(wav))
    # Pad so Silero VAD can open/close on short isolated clips.
    pad_n = int(0.5 * SR)
    audio = np.concatenate(
        [
            np.zeros(pad_n, np.float32),
            np.asarray(raw, dtype=np.float32).reshape(-1),
            np.zeros(int(1.0 * SR), np.float32),
        ]
    )
    asr = pipe.asr
    prev_2pass = bool(getattr(asr, "_2pass", False))
    orig_finalize = asr.finalize_stream

    if asr_mode == "online":
        asr._2pass = False
    elif asr_mode == "offline":

        def _off_only(full_audio, state):  # noqa: ANN001
            return asr.transcribe_offline_only(full_audio)

        asr.finalize_stream = _off_only  # type: ignore[method-assign]
        asr._2pass = True
    else:
        asr._2pass = prev_2pass or True

    engine = DualTrackEngine(
        asr, pipe.translator, None, play=False, force_src_lang="en", label="ledger"
    )

    def _pack(finished) -> dict:  # noqa: ANN001
        return {
            "src": finished.source or "",
            "tgt": finished.translated or "",
            "mt_raw": getattr(finished, "mt_raw", None) or finished.translated or "",
            "audio_sec": float(finished.audio_sec_final or 0),
            "asr_ms": float(sum(getattr(finished, "asr_ms", None) or []) or 0),
            "mt_ms": float(sum(getattr(finished, "mt_ms", None) or []) or 0),
        }

    utts: list[dict] = []
    for i in range(0, audio.size, BLOCK):
        finished = engine.feed_chunk(audio[i : i + BLOCK], allow_async_partial=False)
        if finished is not None:
            utts.append(_pack(finished))
    if engine.in_speech and engine.buf.size:
        pad = np.zeros(int(config.VAD_SILENCE_SEC * SR) + 1600, np.float32)
        for _ in range(32):
            finished = engine.feed_chunk(pad, allow_async_partial=False)
            if finished is not None:
                utts.append(_pack(finished))
                break

    asr.finalize_stream = orig_finalize  # type: ignore[method-assign]
    asr._2pass = prev_2pass
    return utts


def join_utts(utts: list[dict]) -> dict:
    src = " ".join(u["src"] for u in utts if u.get("src")).strip()
    tgt = "".join(u["tgt"] for u in utts if u.get("tgt")).strip()
    dur = sum(float(u.get("audio_sec") or 0) for u in utts)
    return {
        "src": src,
        "tgt": tgt,
        "n_utts": len(utts),
        "audio_sec_sum": round(dur, 3),
        "utts": utts,
    }


def fragment_stats(utts: list[dict]) -> dict:
    if not utts:
        return {
            "n": 0,
            "short_lt_1s": 0,
            "short_rate": 0.0,
            "func_end": 0,
            "func_end_rate": 0.0,
        }
    short = sum(1 for u in utts if float(u.get("audio_sec") or 0) < 1.0)
    func = 0
    for u in utts:
        words = _norm_words(u.get("src") or "")
        if words and words[-1] in FUNC_ENDS:
            func += 1
    n = len(utts)
    return {
        "n": n,
        "short_lt_1s": short,
        "short_rate": round(short / n, 4),
        "func_end": func,
        "func_end_rate": round(func / n, 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip-a5", action="store_true")
    ap.add_argument("--corpus", default="", help="filter: ami,fleurs,libri")
    args = ap.parse_args()

    rows = [
        json.loads(l)
        for l in args.manifest.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    if args.corpus:
        allow = {c.strip() for c in args.corpus.split(",") if c.strip()}
        rows = [r for r in rows if r.get("corpus") in allow]
    if args.limit:
        rows = rows[: args.limit]

    print(f"loading pipeline… n_items={len(rows)}")
    pipe = TranslatePipeline(enable_tts=False)
    translator = pipe.translator

    detail: list[dict] = []
    frag_all: list[dict] = []

    for i, item in enumerate(rows):
        wav = ROOT / item["path"]
        ref = (item.get("ref") or "").strip()
        corpus = item.get("corpus")
        print(f"[{i+1}/{len(rows)}] {item['id']} {corpus} {wav.name}")

        # pipeline (merged)
        utts = run_pipeline_file(pipe, wav, asr_mode="merged")
        joined = join_utts(utts)
        frag = fragment_stats(utts)
        frag_all.extend(utts)

        asr_wer = wer(ref, joined["src"]) if ref else None
        pipe_verdict, pipe_reason = (
            model_judge_mt(ref, joined["tgt"], src=joined["src"])
            if ref
            else ("n/a", "no ref")
        )

        # MT-only on ref
        mt_only_tgt = ""
        mt_only_ms = None
        mt_verdict, mt_reason = "n/a", "no ref"
        if ref:
            t0 = time.perf_counter()
            res = translator.translate(ref, "en")
            mt_only_ms = round((time.perf_counter() - t0) * 1000, 1)
            mt_only_tgt = (getattr(res, "text", None) or str(res) or "").strip()
            mt_verdict, mt_reason = model_judge_mt(ref, mt_only_tgt)

        a5 = {}
        if not args.skip_a5 and ref and corpus == "ami":
            for mode in ("online", "offline", "merged"):
                if mode == "merged":
                    a5[mode] = {
                        "src": joined["src"],
                        "wer": asr_wer,
                        "verdict": pipe_verdict,
                    }
                    continue
                u2 = run_pipeline_file(pipe, wav, asr_mode=mode)
                j2 = join_utts(u2)
                w2 = wer(ref, j2["src"])
                v2, _ = model_judge_mt(ref, j2["tgt"], src=j2["src"])
                a5[mode] = {"src": j2["src"], "wer": round(w2, 4), "verdict": v2}

        rec = {
            "id": item["id"],
            "corpus": corpus,
            "path": item["path"],
            "ref": ref or None,
            "ref_source": item.get("ref_source"),
            "ood": bool(item.get("ood")),
            "pipeline_src": joined["src"],
            "pipeline_tgt": joined["tgt"],
            "n_utts": joined["n_utts"],
            "asr_wer": round(asr_wer, 4) if asr_wer is not None else None,
            "fragment": frag,
            "pipeline_verdict": pipe_verdict,
            "pipeline_reason": pipe_reason,
            "mt_only_tgt": mt_only_tgt,
            "mt_only_ms": mt_only_ms,
            "mt_only_verdict": mt_verdict,
            "mt_only_reason": mt_reason,
            "judge_kind": "model-judge",
            "a5": a5,
        }
        detail.append(rec)

    # aggregates
    by_corp: dict[str, list] = defaultdict(list)
    for r in detail:
        by_corp[r["corpus"]].append(r)

    def _mean(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 4) if xs else None

    table_wer = {}
    table_pass = {}
    for c, rs in by_corp.items():
        wers = [r["asr_wer"] for r in rs if r["asr_wer"] is not None]
        cov = len(wers)
        table_wer[c] = {
            "n": len(rs),
            "wer_coverage": cov,
            "wer_mean": _mean(wers),
        }
        for kind in ("pipeline", "mt_only"):
            key = f"{kind}_verdict"
            labeled = [r[key] for r in rs if r[key] in ("ok", "awkward", "wrong")]
            ok = sum(1 for v in labeled if v == "ok")
            table_pass.setdefault(c, {})[kind] = {
                "n": len(labeled),
                "ok": ok,
                "ok_rate": round(ok / len(labeled), 4) if labeled else None,
                "counts": {
                    k: sum(1 for v in labeled if v == k)
                    for k in ("ok", "awkward", "wrong")
                },
            }

    frag_global = fragment_stats(frag_all)

    # A5 summary on ami
    a5_summary: dict[str, dict] = {}
    for mode in ("online", "offline", "merged"):
        wers = []
        oks = []
        for r in detail:
            a = (r.get("a5") or {}).get(mode)
            if not a:
                continue
            if a.get("wer") is not None:
                wers.append(float(a["wer"]))
            if a.get("verdict") in ("ok", "awkward", "wrong"):
                oks.append(a["verdict"] == "ok")
        a5_summary[mode] = {
            "n": len(wers),
            "wer_mean": _mean(wers),
            "ok_rate": round(sum(oks) / len(oks), 4) if oks else None,
        }

    summary = {
        "n_items": len(detail),
        "table_wer": table_wer,
        "table_fragment": frag_global,
        "table_split_ledger": table_pass,
        "table_a5": a5_summary,
        "main_corpora": ["ami", "fleurs"],
        "ood_corpora": ["libri"],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    detail_path = OUT_DIR / "ledger_eval_detail.jsonl"
    with detail_path.open("w", encoding="utf-8") as f:
        for r in detail:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    sum_path = OUT_DIR / "ledger_eval_summary.json"
    sum_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"wrote {detail_path}")
    print(f"wrote {sum_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
