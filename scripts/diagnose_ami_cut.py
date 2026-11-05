"""Phase C: is 'vala' a VAD mid-word cut or ASR truncation?

Replays cache/harvest_public/ami_en2001b_1993_2020.wav through DualTrackEngine,
prints each utt cut time + trailing energy around the cut.
"""
from __future__ import annotations

import sys
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

SR = int(config.SAMPLE_RATE)
BLOCK = int(SR * 0.1)
WAV = ROOT / "cache" / "harvest_public" / "ami_en2001b_1993_2020.wav"


def rms(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(x), dtype=np.float64)))


def main() -> int:
    audio = load_wav(str(WAV))
    pipe = TranslatePipeline(enable_tts=False)
    engine = DualTrackEngine(
        pipe.asr, pipe.translator, None, play=False, force_src_lang="en", label="diag"
    )
    results = []
    samples_fed = 0
    for i in range(0, audio.size, BLOCK):
        chunk = audio[i : i + BLOCK]
        samples_fed += chunk.size
        finished = engine.feed_chunk(chunk, allow_async_partial=False)
        if finished is not None:
            cut_sample = samples_fed
            # energy 200ms before / after cut
            pre = audio[max(0, cut_sample - int(0.2 * SR)) : cut_sample]
            post = audio[cut_sample : min(audio.size, cut_sample + int(0.2 * SR))]
            results.append(
                {
                    "src": finished.source,
                    "t_end_sec": round(cut_sample / SR, 3),
                    "audio_sec_final": finished.audio_sec_final,
                    "rms_pre_200ms": round(rms(pre), 5),
                    "rms_post_200ms": round(rms(post), 5),
                    "endswith_func": (finished.source or "")
                    .rstrip(".?,!。？，")
                    .split()[-1:]
                    .__getitem__(0)
                    if (finished.source or "").split()
                    else "",
                }
            )
    if engine.in_speech and engine.buf.size:
        pad = np.zeros(int(config.VAD_SILENCE_SEC * SR) + 1600, np.float32)
        for _ in range(24):
            finished = engine.feed_chunk(pad, allow_async_partial=False)
            samples_fed += pad.size
            if finished is not None:
                results.append(
                    {
                        "src": finished.source,
                        "t_end_sec": round(samples_fed / SR, 3),
                        "audio_sec_final": finished.audio_sec_final,
                        "rms_pre_200ms": None,
                        "rms_post_200ms": None,
                    }
                )
                break

    print(f"wav={WAV.name} utts={len(results)}")
    for i, r in enumerate(results):
        print(
            f"[{i}] t_end={r['t_end_sec']}s dur≈{r.get('audio_sec_final')} "
            f"rms_pre={r['rms_pre_200ms']} rms_post={r['rms_post_200ms']}"
        )
        print(f"     src={r['src']!r}")
        # mid-word heuristic: last token looks truncated (no vowels? short odd ending)
        last = (r["src"] or "").rstrip(".?,!。").split()[-1:] or [""]
        last = last[0].lower()
        midword = bool(last) and last[-1].isalpha() and last in (
            "vala",
            "neare",
            "the",
            "a",
            "an",
            "to",
            "of",
            "and",
        )
        if "vala" in (r["src"] or "").lower() or last == "vala":
            print(
                "     DIAG: contains 'vala' — if rms_post still speech-like "
                f"(>0.01) cut is VAD-early; src last={last!r}"
            )
    out = ROOT / "docs" / "ami_cut_diagnosis.json"
    import json

    out.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
