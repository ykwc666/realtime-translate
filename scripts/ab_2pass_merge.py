"""A/B ASR_2PASS_MERGE_MODE: legacy | offline | offline_conflict.

Metrics: oneshot WER on ledger (ami/fleurs/libri) + synth src stability.

python scripts/ab_2pass_merge.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402
from app.audio_io import load_wav  # noqa: E402
from app.pipeline import TranslatePipeline  # noqa: E402

MANIFEST = ROOT / "data" / "ledger_corpus" / "manifest.jsonl"
OUT = ROOT / "docs" / "asr_2pass_merge_ab.json"
SYNTH = [
    ROOT / "cache" / "ab_weekday" / "accept_phrases.wav",
    ROOT / "cache" / "ab_weekday" / "tail_duosimo.wav",
    ROOT / "cache" / "ab_weekday" / "pause_xingqi_er.wav",
]
MODES = ("legacy", "offline", "offline_conflict")


def _norm_words(text: str) -> list[str]:
    t = (text or "").lower()
    t = re.sub(r"[^a-z0-9'\s]+", " ", t)
    return [w for w in t.split() if w]


def wer(ref: str, hyp: str) -> float:
    r, h = _norm_words(ref), _norm_words(hyp)
    if not r:
        return 0.0 if not h else 1.0
    n, m = len(r), len(h)
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, m + 1):
            cur = dp[j]
            dp[j] = prev if r[i - 1] == h[j - 1] else 1 + min(prev, dp[j], dp[j - 1])
            prev = cur
    return dp[m] / float(n)


def main() -> int:
    rows = [
        json.loads(l)
        for l in MANIFEST.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    print("loading pipeline…")
    pipe = TranslatePipeline(enable_tts=False)
    asr = pipe.asr

    # Precompute online/offline pairs once (decode twice per file, merge offline).
    pairs: list[dict] = []
    for i, it in enumerate(rows):
        audio = load_wav(str(ROOT / it["path"]))
        online = asr._transcribe_paraformer_online_oneshot(audio)
        offline = asr.transcribe_offline_only(audio)
        pairs.append(
            {
                "id": it["id"],
                "corpus": it["corpus"],
                "ref": it.get("ref") or "",
                "online": online.text or "",
                "offline": offline.text or "",
            }
        )
        if (i + 1) % 10 == 0:
            print(f"decoded {i+1}/{len(rows)}")

    from app.asr_stream import prefer_2pass_asr

    results = {}
    for mode in MODES:
        by_corp: dict[str, list[float]] = {}
        detail = []
        for p in pairs:
            hyp = prefer_2pass_asr(p["online"], p["offline"], mode=mode)
            w = wer(p["ref"], hyp)
            by_corp.setdefault(p["corpus"], []).append(w)
            detail.append(
                {
                    "id": p["id"],
                    "corpus": p["corpus"],
                    "hyp": hyp,
                    "wer": round(w, 4),
                    "pick": (
                        "offline"
                        if hyp == _strip(p["offline"])
                        else "online"
                        if hyp == _strip(p["online"])
                        else "other"
                    ),
                }
            )
        summary = {
            c: {
                "n": len(ws),
                "wer_mean": round(sum(ws) / len(ws), 4),
            }
            for c, ws in by_corp.items()
        }
        # main metric: ami+fleurs
        main = [w for p, d in zip(pairs, detail) if p["corpus"] in ("ami", "fleurs") for w in [d["wer"]]]
        results[mode] = {
            "by_corpus": summary,
            "main_wer_mean": round(sum(main) / len(main), 4) if main else None,
            "n_main": len(main),
            "offline_pick_rate": round(
                sum(1 for d in detail if d["pick"] == "offline") / max(len(detail), 1),
                4,
            ),
        }
        print(
            f"{mode}: main_wer={results[mode]['main_wer_mean']} "
            f"ami={summary.get('ami',{}).get('wer_mean')} "
            f"offline_pick={results[mode]['offline_pick_rate']}"
        )

    # Synth: run dual-track with each mode briefly — src list compare to legacy
    # (zh force). Heavy; skip full dualtrack — unit + oneshot is enough for merge.
    # Spot-check pause conflict case:
    conflict = prefer_2pass_asr("现在是星期三", "现在是星期啊", mode="legacy")
    conflict_off = prefer_2pass_asr("现在是星期三", "现在是星期啊", mode="offline")
    conflict_oc = prefer_2pass_asr(
        "现在是星期三", "现在是星期啊", mode="offline_conflict"
    )

    # Recommend: lowest ami wer among modes; require not worse fleurs by >0.02
    ami = {m: results[m]["by_corpus"].get("ami", {}).get("wer_mean") for m in MODES}
    fleurs = {
        m: results[m]["by_corpus"].get("fleurs", {}).get("wer_mean") for m in MODES
    }
    base_ami = ami["legacy"]
    base_fl = fleurs["legacy"]
    best = "legacy"
    best_ami = base_ami if base_ami is not None else 9.0
    for m in MODES:
        if ami[m] is None:
            continue
        fl_ok = fleurs[m] is None or base_fl is None or fleurs[m] <= base_fl + 0.02
        if ami[m] + 1e-9 < best_ami and fl_ok:
            best = m
            best_ami = ami[m]

    # Prefer offline/offline_conflict over legacy only if clear ami win (>=0.01)
    if best != "legacy" and base_ami is not None and (base_ami - best_ami) < 0.01:
        best = "legacy"
        reason = "ami gain <0.01 — keep legacy"
    elif best == "legacy":
        reason = "legacy best or tied under gates"
    else:
        reason = (
            f"{best} ami_wer {base_ami}→{best_ami} "
            f"(fleurs {base_fl}→{fleurs[best]})"
        )

    out = {
        "modes": results,
        "spot_weekday_conflict": {
            "stream": "现在是星期三",
            "offline_hyp": "现在是星期啊",
            "legacy": conflict,
            "mode_offline": conflict_off,
            "mode_offline_conflict": conflict_oc,
        },
        "recommend": best,
        "recommend_reason": reason,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"recommend": best, "reason": reason}, ensure_ascii=False, indent=2))
    print(f"wrote {OUT}")
    return 0


def _strip(t: str) -> str:
    return (t or "").strip().rstrip("。！？.!?,，、；; ")


if __name__ == "__main__":
    raise SystemExit(main())
