"""Realtime-paced dualsim with perceived-latency timestamps (JSONL).

按墙钟节奏喂 100ms 块，让 VAD 静音等待 ≈ 实机。
主指标：latency_perceived_ms = 停说 → 译文就绪（不开 TTS 时的体感代理）。

用法：
  python scripts/bench_paced.py --spec on  --tag paced-spec-on
  python scripts/bench_paced.py --spec off --tag paced-spec-off   # 对照
  python scripts/bench_paced.py --wav cache/ab_weekday/tail_duosimo.wav --tag paced-real
  python scripts/bench_paced.py --pace 0 --tag unpaced            # 复现旧 dualsim 口径
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from datetime import datetime
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
OUT_WAV = ROOT / "cache" / "ab_weekday" / "paced_phrases.wav"
OUT_JSONL = ROOT / "docs" / "paced_bench.jsonl"
PHRASES = ["你好", "延迟大概有多少", "今天天气怎么样", "现在是星期二"]


def _synth(voice, text: str, tail_sil: float = 1.6) -> np.ndarray:  # noqa: ANN001
    chunks = [
        np.asarray(ch.audio_float_array, dtype=np.float32) for ch in voice.synthesize(text)
    ]
    pcm = np.concatenate(chunks)
    src = int(voice.config.sample_rate)
    if src != SR:
        x = np.arange(pcm.size, dtype=np.float64)
        n = int(round(pcm.size * SR / float(src)))
        xp = np.linspace(0, pcm.size - 1, max(n, 1))
        pcm = np.interp(xp, x, pcm).astype(np.float32)
    return np.concatenate([pcm, np.zeros(int(tail_sil * SR), np.float32)])


def _ensure_wav(phrases: list[str]) -> Path:
    from piper import PiperVoice

    voice = PiperVoice.load(str(config.PIPER_VOICE_ZH))
    OUT_WAV.parent.mkdir(parents=True, exist_ok=True)
    audio = np.concatenate([_synth(voice, p) for p in phrases])
    pcm16 = (np.clip(audio, -1, 1) * 32767.0).astype(np.int16)
    with wave.open(str(OUT_WAV), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm16.tobytes())
    return OUT_WAV


def _run_paced(
    pipe: TranslatePipeline, tag: str, wav: Path, pace: float, note: str
) -> list[dict]:
    audio = load_wav(str(wav))
    engine = DualTrackEngine(pipe.asr, pipe.translator, pipe.tts, play=False)
    results = []
    proc_ms: list[float] = []
    t_wall0 = time.perf_counter()
    for i in range(0, audio.size, BLOCK):
        chunk = audio[i : i + BLOCK]
        if chunk.size == 0:
            break
        if pace > 0:
            t_slot = t_wall0 + (i / SR) / pace
            delay = t_slot - time.perf_counter()
            if delay > 0.001:
                time.sleep(delay)
        c0 = time.perf_counter()
        finished = engine.feed_chunk(chunk, mute_cb=None, allow_async_partial=False)
        proc_ms.append((time.perf_counter() - c0) * 1000)
        if finished is not None:
            results.append(finished)

    if engine.in_speech and engine.buf.size:
        pad = np.zeros(int(config.VAD_SILENCE_SEC * SR) + 1600, np.float32)
        for _ in range(16):
            finished = engine.feed_chunk(pad, allow_async_partial=False)
            if finished is not None:
                results.append(finished)
                break

    rows: list[dict] = []
    print(f"\n===== paced {tag} =====")
    for i, r in enumerate(results, 1):
        row = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tag": tag,
            "note": note,
            "utt": i,
            "wav": wav.name,
            "pace": pace,
            "src": r.source,
            "tgt": r.translated,
            "latency_perceived_ms": r.latency_perceived_ms,
            "t_cut_ms": r.t_cut_ms,
            "t_asr_done_ms": r.t_asr_done_ms,
            "t_mt_done_ms": r.t_mt_done_ms,
            "t_first_audio_ms": r.t_first_audio_ms,
            "speculate_hit": r.speculate_hit,
            "utt_wall_ms": r.utt_wall_ms,
            "first_stable_mt_ms": r.first_stable_mt_ms,
            "silence_sec": config.VAD_SILENCE_SEC,
            "pad_sec": config.ONLINE_TAIL_PAD_SEC,
            "speculate": config.ASR_SPECULATE_ENABLED,
            "sync_ms": config.ENDPOINT_PARTIAL_SYNC_MS,
        }
        rows.append(row)
        print(
            f"  utt{i}: src={r.source!r} "
            f"perceived={_r(r.latency_perceived_ms)} "
            f"cut={_r(r.t_cut_ms)} asr={_r(r.t_asr_done_ms)} mt={_r(r.t_mt_done_ms)} "
            f"spec={r.speculate_hit} wall={_r(r.utt_wall_ms)}"
        )
    if rows:
        pers = [x["latency_perceived_ms"] for x in rows if x["latency_perceived_ms"] is not None]
        if pers:
            s = sorted(pers)
            print(
                f"  n={len(pers)} mean={round(sum(pers)/len(pers))}ms "
                f"median={round(s[len(s)//2])}ms max={round(max(pers))}ms"
            )
        hits = sum(1 for r in results if r.speculate_hit)
        print(f"  spec_hit={hits}/{len(results)}")
    if proc_ms:
        s = sorted(proc_ms)
        over = sum(1 for v in proc_ms if v > 100)
        print(
            f"  audio-thread per-chunk: median={round(s[len(s)//2])}ms "
            f"max={round(max(proc_ms))}ms over_100ms={over}/{len(proc_ms)}"
        )
    return rows


def _r(v: float | None) -> str:
    return "—" if v is None else str(round(v))


def main() -> int:
    ap = argparse.ArgumentParser(description="实时节奏双轨 bench（体感延迟）")
    ap.add_argument("--wav", default="", help="真实 wav（16k mono 更佳）；不给则用合成短句")
    ap.add_argument(
        "--spec",
        choices=["on", "off"],
        default="on" if config.ASR_SPECULATE_ENABLED else "off",
        help="投机定稿开关",
    )
    ap.add_argument("--tag", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--silence", type=float, default=0.0, help="覆盖 VAD_SILENCE_SEC")
    ap.add_argument("--phrases", default="", help="合成文本，用 | 分隔")
    ap.add_argument("--pace", type=float, default=1.0, help="1=实时节奏；0=全速（旧 dualsim 口径）")
    ap.add_argument("--no-log", action="store_true", help="不写 docs/paced_bench.jsonl")
    args = ap.parse_args()

    config.ASR_SPECULATE_ENABLED = args.spec == "on"
    if args.silence > 0:
        config.VAD_SILENCE_SEC = args.silence

    if args.wav:
        wav = Path(args.wav)
        if not wav.exists():
            print(f"wav missing: {wav}")
            return 2
    else:
        phrases = args.phrases.split("|") if args.phrases else PHRASES
        print("build wav（Piper 合成）…")
        wav = _ensure_wav(phrases)

    print("load models…")
    pipe = TranslatePipeline(enable_tts=False)

    tag = args.tag or (
        f"paced-spec-{args.spec}" + ("" if pace_is_real(args.pace) else "-unpaced")
    )
    rows = _run_paced(pipe, tag, wav, args.pace, args.note)
    if not rows:
        print("no utterance produced")
        return 1
    if not args.no_log:
        OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
        with OUT_JSONL.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"wrote {len(rows)} rows → {OUT_JSONL}")
    return 0


def pace_is_real(pace: float) -> bool:
    return pace > 0


if __name__ == "__main__":
    raise SystemExit(main())
