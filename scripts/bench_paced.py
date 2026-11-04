"""Realtime-paced dualsim with perceived-latency timestamps (JSONL).

按墙钟节奏喂 100ms 块，让 VAD 静音等待 ≈ 实机。
主指标（默认 --tts off）：latency_perceived_ms = 停说 → 译文就绪（mt_ready）。
--tts on 时额外：latency_device_ttfa_ms = 停说 → 设备首次写出非零 PCM。

用法：
  python scripts/bench_paced.py --spec on  --tag paced-spec-on
  python scripts/bench_paced.py --tts on --persistent on --wav cache/ab_weekday/accept_phrases.wav
  python scripts/bench_paced.py --pace 0 --tag unpaced
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


def _agg(xs: list[float]) -> dict:
    if not xs:
        return {}
    s = sorted(xs)
    return {
        "n": len(s),
        "mean": round(sum(s) / len(s), 1),
        "median": round(s[len(s) // 2], 1),
        "p90": round(s[int(0.9 * (len(s) - 1))], 1),
        "max": round(max(s), 1),
    }


def _run_paced(
    pipe: TranslatePipeline,
    tag: str,
    wav: Path,
    pace: float,
    note: str,
    *,
    play: bool,
    close_tts: bool = True,
) -> list[dict]:
    audio = load_wav(str(wav))
    engine = DualTrackEngine(pipe.asr, pipe.translator, pipe.tts, play=play)
    # Keep capture thread free while TTS plays
    assert not bool(getattr(config, "FINALIZE_WAIT_TTS", True)) or not play
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

    if play:
        # Wait for TTS worker to finish so late device-TTFA events merge onto results
        engine.wait_tts_idle(timeout=180.0)

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
            # legacy mt_ready
            "latency_perceived_ms": r.latency_perceived_ms,
            "t_mt_enqueue_ms": getattr(r, "t_mt_enqueue_ms", None),
            "t_cut_ms": r.t_cut_ms,
            "t_asr_done_ms": r.t_asr_done_ms,
            "t_mt_done_ms": r.t_mt_done_ms,
            "t_first_audio_ms": r.t_first_audio_ms,  # deprecated = mt enqueue
            "t_tts_pcm_ms": getattr(r, "t_tts_pcm_ms", None),
            "t_first_audio_callback_ms": getattr(r, "t_first_audio_callback_ms", None),
            "latency_device_ttfa_ms": getattr(r, "latency_device_ttfa_ms", None),
            "mt_ready_to_callback_ms": getattr(r, "mt_ready_to_callback_ms", None),
            "tts_active_underruns": getattr(r, "tts_active_underruns", None),
            "tts_samples_expected": getattr(r, "tts_samples_expected", None),
            "tts_samples_written": getattr(r, "tts_samples_written", None),
            "tts_persistent": getattr(r, "tts_persistent", None),
            "tts_stream_reused": getattr(r, "tts_stream_reused", None),
            "tts_interrupted": getattr(r, "tts_interrupted", None),
            "speculate_hit": r.speculate_hit,
            "utt_wall_ms": r.utt_wall_ms,
            "first_stable_mt_ms": r.first_stable_mt_ms,
            "asr_wait_after_cut_ms": (
                None
                if r.t_asr_done_ms is None or r.t_cut_ms is None
                else round(float(r.t_asr_done_ms) - float(r.t_cut_ms), 1)
            ),
            "audio_sec_final": getattr(r, "audio_sec_final", None),
            "early_cut_probe": getattr(r, "early_cut_probe", None),
            "silence_sec": config.VAD_SILENCE_SEC,
            "early_cut_probe_enabled": bool(
                getattr(config, "ENDPOINT_EARLY_CUT_PROBE", False)
            ),
            "pad_sec": config.ONLINE_TAIL_PAD_SEC,
            "speculate": config.ASR_SPECULATE_ENABLED,
            "sync_ms": config.ENDPOINT_PARTIAL_SYNC_MS,
            "vad": config.VAD_BACKEND,
            "online_threads": getattr(config, "ASR_THREADS_ONLINE", config.ASR_THREADS),
            "offline_threads": getattr(config, "ASR_THREADS_OFFLINE", config.ASR_THREADS),
            "two_pass_parallel": bool(getattr(config, "ASR_2PASS_PARALLEL", False)),
            "adaptive_spec": bool(
                getattr(config, "ASR_SPECULATE_ADAPTIVE_LEAD", False)
            ),
            "tts_enabled": play,
            "persistent": bool(getattr(config, "TTS_PERSISTENT_OUTPUT", False)),
            "finalize_wait_tts": bool(getattr(config, "FINALIZE_WAIT_TTS", True)),
        }
        rows.append(row)
        print(
            f"  utt{i}: src={r.source!r} tgt={r.translated!r} "
            f"mt_ready={_r(r.latency_perceived_ms)} "
            f"device_ttfa={_r(getattr(r, 'latency_device_ttfa_ms', None))} "
            f"pcm={_r(getattr(r, 't_tts_pcm_ms', None))} "
            f"cut={_r(r.t_cut_ms)} asr={_r(r.t_asr_done_ms)} mt={_r(r.t_mt_done_ms)} "
            f"spec={r.speculate_hit} underrun={getattr(r, 'tts_active_underruns', None)} "
            f"wall={_r(r.utt_wall_ms)}"
        )
    if rows:
        mt_ready = [
            x["latency_perceived_ms"]
            for x in rows
            if x["latency_perceived_ms"] is not None
        ]
        device = [
            x["latency_device_ttfa_ms"]
            for x in rows
            if x["latency_device_ttfa_ms"] is not None
        ]
        if mt_ready:
            a = _agg(mt_ready)
            print(
                f"  mt_ready n={a['n']} mean={a['mean']}ms median={a['median']}ms "
                f"p90={a['p90']}ms max={a['max']}ms"
            )
        if device:
            a = _agg(device)
            print(
                f"  device_ttfa n={a['n']} mean={a['mean']}ms median={a['median']}ms "
                f"p90={a['p90']}ms max={a['max']}ms"
            )
        hits = sum(1 for r in results if r.speculate_hit)
        print(f"  spec_hit={hits}/{len(results)}")
        sc = getattr(engine, "spec_counters", None)
        if isinstance(sc, dict):
            print(f"  spec_counters={sc}")
            for row in rows:
                row["spec_counters"] = dict(sc)
    if proc_ms:
        s = sorted(proc_ms)
        over = sum(1 for v in proc_ms if v > 100)
        thread_stats = {
            "median_ms": round(s[len(s) // 2], 1),
            "max_ms": round(max(proc_ms), 1),
            "over_100ms": over,
            "n": len(proc_ms),
        }
        print(
            f"  audio-thread per-chunk: median={thread_stats['median_ms']}ms "
            f"max={thread_stats['max_ms']}ms over_100ms={over}/{len(proc_ms)}"
        )
        for row in rows:
            row["audio_thread"] = thread_stats
    if close_tts and play and pipe.tts is not None:
        pipe.tts.close()
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
    ap.add_argument(
        "--vad",
        choices=["silero", "ten", "energy"],
        default="",
        help="覆盖 VAD_BACKEND（ten 需 models/ten-vad.onnx）",
    )
    ap.add_argument(
        "--online-threads",
        type=int,
        default=0,
        help="覆盖 ASR_THREADS_ONLINE（须在加载模型前；0=不改）",
    )
    ap.add_argument(
        "--offline-threads",
        type=int,
        default=0,
        help="覆盖 ASR_THREADS_OFFLINE（0=不改）",
    )
    ap.add_argument(
        "--2pass-parallel",
        choices=["on", "off", ""],
        default="",
        dest="two_pass_parallel",
        help="覆盖 ASR_2PASS_PARALLEL",
    )
    ap.add_argument(
        "--adaptive-spec",
        choices=["on", "off", ""],
        default="",
        help="覆盖 ASR_SPECULATE_ADAPTIVE_LEAD",
    )
    ap.add_argument(
        "--tts",
        choices=["on", "off"],
        default="off",
        help="是否真实播 TTS（默认 off，保持历史 paced 口径）",
    )
    ap.add_argument(
        "--persistent",
        choices=["on", "off", ""],
        default="",
        help="覆盖 TTS_PERSISTENT_OUTPUT；空=沿用配置默认",
    )
    ap.add_argument(
        "--probe",
        choices=["on", "off"],
        default="off",
        help="只读 early-cut 探针（ENDPOINT_EARLY_CUT_PROBE）；默认 off",
    )
    ap.add_argument("--phrases", default="", help="合成文本，用 | 分隔")
    ap.add_argument("--pace", type=float, default=1.0, help="1=实时节奏；0=全速（旧 dualsim 口径）")
    ap.add_argument("--no-log", action="store_true", help="不写 docs/paced_bench.jsonl")
    args = ap.parse_args()

    config.ASR_SPECULATE_ENABLED = args.spec == "on"
    if args.silence > 0:
        config.VAD_SILENCE_SEC = args.silence
    if args.vad:
        config.VAD_BACKEND = args.vad
    # Thread/parallel overrides MUST land before TranslatePipeline/StreamingAsr init.
    if args.online_threads > 0:
        config.ASR_THREADS_ONLINE = int(args.online_threads)
    if args.offline_threads > 0:
        config.ASR_THREADS_OFFLINE = int(args.offline_threads)
    if args.two_pass_parallel == "on":
        config.ASR_2PASS_PARALLEL = True
    elif args.two_pass_parallel == "off":
        config.ASR_2PASS_PARALLEL = False
    if args.adaptive_spec == "on":
        config.ASR_SPECULATE_ADAPTIVE_LEAD = True
    elif args.adaptive_spec == "off":
        config.ASR_SPECULATE_ADAPTIVE_LEAD = False
    if args.persistent == "on":
        config.TTS_PERSISTENT_OUTPUT = True
    elif args.persistent == "off":
        config.TTS_PERSISTENT_OUTPUT = False
    config.ENDPOINT_EARLY_CUT_PROBE = args.probe == "on"
    # Never block capture thread on TTS during paced+tts
    if args.tts == "on":
        config.FINALIZE_WAIT_TTS = False

    if args.wav:
        wav = Path(args.wav)
        if not wav.exists():
            print(f"wav missing: {wav}")
            return 2
    else:
        phrases = args.phrases.split("|") if args.phrases else PHRASES
        print("build wav（Piper 合成）…")
        wav = _ensure_wav(phrases)

    play = args.tts == "on"
    print(
        f"ASR threads online={config.ASR_THREADS_ONLINE} "
        f"offline={config.ASR_THREADS_OFFLINE} "
        f"2pass_parallel={config.ASR_2PASS_PARALLEL} "
        f"adaptive_spec={getattr(config, 'ASR_SPECULATE_ADAPTIVE_LEAD', False)} "
        f"tts={play} persistent={config.TTS_PERSISTENT_OUTPUT}"
    )
    print("load models…")
    pipe = TranslatePipeline(enable_tts=play)

    tag = args.tag or (
        f"paced-spec-{args.spec}"
        + (f"-vad-{config.VAD_BACKEND}" if args.vad else "")
        + ("" if pace_is_real(args.pace) else "-unpaced")
        + (f"-tts-{'p' if config.TTS_PERSISTENT_OUTPUT else 'o'}" if play else "")
    )
    rows = _run_paced(pipe, tag, wav, args.pace, args.note, play=play)
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
