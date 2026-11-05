"""G1/G2：ledger 上会后重译 vs oneshot / 空输出。"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.asr_stream import StreamingAsr  # noqa: E402
from app.audio_io import load_wav  # noqa: E402
from app.config import setup_env  # noqa: E402
from app.retranscribe import retranscribe_audio  # noqa: E402
from app.translate import Translator  # noqa: E402

# 与 scripts/eval_asr_oneshot / eval_ledger 同口径的简易 WER
def _tok(s: str) -> list[str]:
    s = (s or "").strip().upper()
    s = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", s)
    # 英文按词，中文按字混排：先拆空白，CJK 再拆开
    out: list[str] = []
    for w in s.split():
        if re.fullmatch(r"[\u4e00-\u9fff]+", w):
            out.extend(list(w))
        else:
            out.append(w)
    return out


def wer(ref: str, hyp: str) -> float:
    r = _tok(ref)
    h = _tok(hyp)
    if not r:
        return 0.0 if not h else 1.0
    # classic DP
    dp = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
    for i in range(len(r) + 1):
        dp[i][0] = i
    for j in range(len(h) + 1):
        dp[0][j] = j
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            cost = 0 if r[i - 1] == h[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost,
            )
    return dp[-1][-1] / len(r)


def main() -> int:
    setup_env()
    manifest = ROOT / "data" / "ledger_corpus" / "manifest.jsonl"
    rows = [
        json.loads(l)
        for l in manifest.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    rows = [r for r in rows if r.get("corpus") in ("ami", "fleurs")]
    asr = StreamingAsr()
    # G1/G2 只看 ASR 层：跳过 MT 加速
    class _NoMT:
        def translate(self, text, src_lang=None):  # noqa: ANN001
            from app.translate import TranslateResult

            return TranslateResult(src_lang or "en", "zh", text, 0.0)

    mt = _NoMT()
    empty = 0
    ratios = []
    wers = []
    oneshot_wers = []
    t0 = time.perf_counter()
    for i, item in enumerate(rows):
        wav = ROOT / item["path"]
        ref = (item.get("ref") or "").strip()
        audio = load_wav(str(wav))
        segs, _rep = retranscribe_audio(audio, asr=asr, translator=mt, force_src_lang="en")
        hyp = " ".join(s.src for s in segs).strip()
        if not hyp:
            empty += 1
            ratios.append(0.0)
            wers.append(1.0)
        else:
            ref_n = len(re.sub(r"\s+", "", ref))
            hyp_n = len(re.sub(r"\s+", "", hyp))
            ratios.append(hyp_n / ref_n if ref_n else 1.0)
            wers.append(wer(ref, hyp))
        # oneshot baseline same model
        off = asr.transcribe_offline_only(audio)
        oneshot_wers.append(wer(ref, off.text or ""))
        if (i + 1) % 10 == 0:
            print(f"… {i+1}/{len(rows)}")
    wall = time.perf_counter() - t0
    mean_wer = sum(wers) / len(wers)
    mean_one = sum(oneshot_wers) / len(oneshot_wers)
    low_ratio = sum(1 for r in ratios if r < 0.85)
    print(
        json.dumps(
            {
                "n": len(rows),
                "empty": empty,
                "char_ratio_lt_085": low_ratio,
                "wer_post": round(mean_wer, 4),
                "wer_oneshot": round(mean_one, 4),
                "delta": round(mean_wer - mean_one, 4),
                "wall_sec": round(wall, 1),
                "g1_empty_ok": empty == 0,
                "g2_ok": mean_wer <= mean_one + 0.02,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if empty == 0 and mean_wer <= mean_one + 0.02 else 1


if __name__ == "__main__":
    raise SystemExit(main())
