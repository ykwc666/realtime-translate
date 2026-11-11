"""No-mic realtime regression gate (dualsim + MicProbe).

Gates:
  - dualsim exits cleanly
  - utterance count in [4, 8] on accept_phrases.wav
  - probe empty_abort == 0; no non-abort rows with empty src
  - probe on vs off wall Δ ≤ 3%
"""

from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402
from app.mic_probe import MicProbe  # noqa: E402
from app.pipeline import TranslatePipeline  # noqa: E402
from piper import PiperVoice  # noqa: E402

SR = int(config.SAMPLE_RATE)
PHRASE_WAV = ROOT / "cache" / "ab_weekday" / "accept_phrases.wav"
UTT_MIN, UTT_MAX = 4, 8
WALL_DELTA_MAX = 0.03


def _ensure_wav() -> Path:
    if PHRASE_WAV.is_file() and PHRASE_WAV.stat().st_size > 1000:
        return PHRASE_WAV
    print(f"[gate] synthesizing {PHRASE_WAV}")
    voice = PiperVoice.load(str(config.PIPER_VOICE_ZH))
    phrases = [
        "你好",
        "延迟大概有多少",
        "今天天气怎么样",
        "现在是星期二",
        "他现在没有回我的消息",
    ]

    def one(text: str) -> np.ndarray:
        chunks = [
            np.asarray(ch.audio_float_array, dtype=np.float32)
            for ch in voice.synthesize(text)
        ]
        pcm = np.concatenate(chunks)
        src = int(voice.config.sample_rate)
        if src != SR:
            x = np.arange(pcm.size, dtype=np.float64)
            n = int(round(pcm.size * SR / float(src)))
            xp = np.linspace(0, pcm.size - 1, max(n, 1))
            pcm = np.interp(xp, x, pcm).astype(np.float32)
        return np.concatenate([pcm, np.zeros(int(1.3 * SR), np.float32)])

    audio = np.concatenate([one(p) for p in phrases])
    PHRASE_WAV.parent.mkdir(parents=True, exist_ok=True)
    pcm16 = (np.clip(audio, -1, 1) * 32767.0).astype(np.int16)
    with wave.open(str(PHRASE_WAV), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm16.tobytes())
    return PHRASE_WAV


def _run_arm(*, with_probe: bool) -> tuple[float, int, MicProbe | None, list]:
    probe = None
    if with_probe:
        probe_dir = ROOT / "cache" / "gate_realtime_probe"
        probe_dir.mkdir(parents=True, exist_ok=True)
        probe = MicProbe(
            jsonl_path=probe_dir / "gate.jsonl",
            summary_path=probe_dir / "summary.json",
        )
    pipe = TranslatePipeline(
        enable_tts=False,
        mic_probe=probe,
        tts_warmup_langs=(),
    )
    t0 = time.perf_counter()
    results = pipe.run_dualsim(str(PHRASE_WAV), play=False)
    wall = time.perf_counter() - t0
    pipe.close()
    return wall, len(results), probe, results


def main() -> int:
    wav = _ensure_wav()
    print(f"[gate] wav={wav}")

    print("\n=== arm A: probe ON ===")
    wall_on, n_on, probe, results = _run_arm(with_probe=True)
    assert probe is not None
    empty_abort = int(probe.empty_abort)
    bad_empty = [
        r
        for r in probe._rows  # noqa: SLF001 — gate reads recorded rows
        if not str(r.get("src") or "").strip()
        and str(r.get("cut_reason") or "") != "empty_abort"
    ]
    print(f"  wall={wall_on:.3f}s utts={n_on} empty_abort={empty_abort} bad_empty={len(bad_empty)}")
    for r in results:
        print(f"  [utt] src={r.source!r} | tgt={r.translated!r}")

    print("\n=== arm B: probe OFF ===")
    wall_off, n_off, _, _ = _run_arm(with_probe=False)
    print(f"  wall={wall_off:.3f}s utts={n_off}")

    denom = max(wall_off, 1e-6)
    delta = abs(wall_on - wall_off) / denom
    print(f"\nwall Δ = {delta * 100:.2f}% (limit ≤ {WALL_DELTA_MAX * 100:.0f}%)")

    fails: list[str] = []
    if not (UTT_MIN <= n_on <= UTT_MAX):
        fails.append(f"utt_count={n_on} not in [{UTT_MIN},{UTT_MAX}]")
    if empty_abort != 0:
        fails.append(f"empty_abort={empty_abort}")
    if bad_empty:
        fails.append(f"non-abort empty src rows={len(bad_empty)}")
    if delta > WALL_DELTA_MAX:
        fails.append(f"wall_delta={delta * 100:.2f}% > {WALL_DELTA_MAX * 100:.0f}%")

    print("\n--- gates ---")
    print(f"utt_count[{UTT_MIN},{UTT_MAX}]: {'PASS' if UTT_MIN <= n_on <= UTT_MAX else 'FAIL'} ({n_on})")
    print(f"empty_abort==0: {'PASS' if empty_abort == 0 else 'FAIL'} ({empty_abort})")
    print(f"no bad empty src: {'PASS' if not bad_empty else 'FAIL'}")
    print(f"probe wall Δ≤3%: {'PASS' if delta <= WALL_DELTA_MAX else 'FAIL'} ({delta * 100:.2f}%)")

    if fails:
        print("GATE_REALTIME_FAIL", "; ".join(fails))
        return 1
    print("GATE_REALTIME_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
