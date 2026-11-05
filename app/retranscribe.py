"""会后重译：整段 wav → 能量分块 SenseVoice → MT → 双语稿。"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import config
from .asr_stream import StreamingAsr
from .audio_io import load_wav
from .punct import add_punctuation, punct_available
from .session_record import latest_session_dir
from .translate import Translator, detect_lang

_SENT_SPLIT = re.compile(r"(?<=[。？！；.!?;])\s*")
_CHUNK_TARGET_SEC = 25.0
_CHUNK_SEARCH_SEC = 3.0
_ENERGY_WIN_SEC = 0.2
_SILENCE_DROP_RATIO = 0.95
_SILENCE_RMS = 0.008


@dataclass
class Segment:
    idx: int
    t0: float
    t1: float
    src: str
    tgt: str
    chunk_idx: int
    asr_ms: float
    mt_ms: float
    mt_path: str = "retranscribe"


@dataclass
class RetranscribeResult:
    session_dir: Path
    segments: list[Segment] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)


def resolve_session_dir(spec: str | Path) -> Path:
    s = str(spec).strip()
    if not s or s.lower() == "latest":
        latest = latest_session_dir()
        if latest is None:
            raise FileNotFoundError("data/sessions 下没有含 mic_raw.wav 的会话")
        return latest
    p = Path(s)
    if not p.is_absolute():
        # 允许 sessions 相对名或完整路径
        cand = Path(config.ROOT) / "data" / "sessions" / s
        if cand.is_dir():
            p = cand
    if not p.is_dir():
        raise FileNotFoundError(f"会话目录不存在: {p}")
    if not (p / "mic_raw.wav").exists():
        raise FileNotFoundError(f"缺少 mic_raw.wav: {p}")
    return p


def _frame_energy(audio: np.ndarray, sr: int, win_sec: float = _ENERGY_WIN_SEC) -> np.ndarray:
    win = max(1, int(sr * win_sec))
    n = int(audio.size) // win
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    trimmed = audio[: n * win].reshape(n, win)
    return np.sqrt(np.mean(np.square(trimmed, dtype=np.float64), axis=1))


def split_chunks(
    audio: np.ndarray,
    sr: int = config.SAMPLE_RATE,
    target_sec: float = _CHUNK_TARGET_SEC,
    search_sec: float = _CHUNK_SEARCH_SEC,
) -> tuple[list[tuple[int, int]], int]:
    """返回 [(start, end), ...] 样本下标；以及丢弃的静音块数。"""
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        return [], 0
    target = int(target_sec * sr)
    search = int(search_sec * sr)
    win = max(1, int(_ENERGY_WIN_SEC * sr))
    chunks: list[tuple[int, int]] = []
    dropped = 0
    pos = 0
    n = int(audio.size)
    while pos < n:
        remain = n - pos
        if remain <= int(target * 1.2):
            end = n
        else:
            ideal = pos + target
            lo = max(pos + target // 2, ideal - search)
            hi = min(n - win, ideal + search)
            if hi <= lo:
                end = min(n, pos + target)
            else:
                # 在 [lo, hi] 找能量最低的 200ms 窗终点
                best_e = float("inf")
                best_end = ideal
                i = lo
                while i + win <= hi:
                    seg = audio[i : i + win]
                    e = float(np.sqrt(np.mean(np.square(seg, dtype=np.float64))))
                    if e < best_e:
                        best_e = e
                        best_end = i + win
                    i += win
                end = int(best_end)
        if end <= pos:
            end = min(n, pos + target)
        piece = audio[pos:end]
        rms = float(np.sqrt(np.mean(np.square(piece, dtype=np.float64)))) if piece.size else 0.0
        peak = float(np.max(np.abs(piece))) if piece.size else 0.0
        # 近全静音：帧静音占比高且峰值也极低（避免 AMI 弱电平整段被误丢）
        energies = _frame_energy(piece, sr)
        if energies.size:
            silent_ratio = float(np.mean(energies < _SILENCE_RMS))
        else:
            silent_ratio = 1.0
        is_silence = bool(
            piece.size
            and silent_ratio > _SILENCE_DROP_RATIO
            and peak < 0.02
            and rms < _SILENCE_RMS
        )
        if is_silence:
            dropped += 1
        else:
            chunks.append((pos, end))
        pos = end
    # 绝不因静音启发式交空结果：整段至少保留一刀给 SenseVoice
    if not chunks and audio.size:
        chunks = [(0, int(audio.size))]
        dropped = 0
    return chunks, dropped


def split_sentences(text: str) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []
    # 已有句末标点？
    if re.search(r"[。？！；.!?;]", t):
        parts = [p.strip() for p in _SENT_SPLIT.split(t) if p.strip()]
        return parts or [t]
    if punct_available():
        puncted, _ms = add_punctuation(t)
        t2 = (puncted or t).strip()
        parts = [p.strip() for p in _SENT_SPLIT.split(t2) if p.strip()]
        return parts or [t2]
    return [t]


def _alloc_timestamps(
    sentences: list[str], t0: float, t1: float
) -> list[tuple[float, float, str]]:
    if not sentences:
        return []
    weights = [max(1, len(re.sub(r"\s+", "", s))) for s in sentences]
    total = float(sum(weights)) or 1.0
    dur = max(0.0, t1 - t0)
    out: list[tuple[float, float, str]] = []
    cursor = t0
    for i, (s, w) in enumerate(zip(sentences, weights)):
        if i == len(sentences) - 1:
            end = t1
        else:
            end = cursor + dur * (w / total)
        out.append((cursor, end, s))
        cursor = end
    return out


def _norm_cmp(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", (text or "").lower())


def compare_live(
    live_rows: list[dict], segments: list[Segment]
) -> dict[str, Any]:
    """粗对齐：会后句在实时稿中找不到近似 → live_missed；实时有、会后无 → 可另计。"""
    live_srcs = [_norm_cmp(r.get("src") or "") for r in live_rows]
    live_srcs = [s for s in live_srcs if s]
    post_srcs = [_norm_cmp(s.src) for s in segments if s.src.strip()]
    missed = 0
    for ps in post_srcs:
        if not ps:
            continue
        hit = False
        for ls in live_srcs:
            # 子串或较短一方覆盖 ≥0.6
            a, b = (ps, ls) if len(ps) <= len(ls) else (ls, ps)
            if not a:
                continue
            if a in b or (len(a) >= 4 and sum(1 for c in a if c in b) / len(a) >= 0.6):
                hit = True
                break
        if not hit:
            missed += 1
    wrong = 0
    for ls in live_srcs:
        if not ls:
            continue
        hit = False
        for ps in post_srcs:
            a, b = (ls, ps) if len(ls) <= len(ps) else (ps, ls)
            if a and (a in b or (len(a) >= 4 and sum(1 for c in a if c in b) / len(a) >= 0.6)):
                hit = True
                break
        if not hit:
            wrong += 1
    return {
        "live_utts": len(live_srcs),
        "post_segments": len(post_srcs),
        "live_missed": missed,
        "live_wrong": wrong,
    }


def _fmt_ts(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    if h:
        return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")
    return f"{m:02d}:{s:06.3f}".replace(".", ",")


def write_outputs(
    out_dir: Path,
    segments: list[Segment],
    report: dict[str, Any],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    md_lines = [
        "| t0–t1 | 原文 | 译文 |",
        "|---|---|---|",
    ]
    for seg in segments:
        md_lines.append(
            f"| {seg.t0:.1f}–{seg.t1:.1f} | {seg.src} | {seg.tgt} |"
        )
    (out_dir / "transcript.md").write_text(
        "\n".join(md_lines) + "\n", encoding="utf-8"
    )

    srt_parts: list[str] = []
    for i, seg in enumerate(segments, 1):
        srt_parts.append(
            f"{i}\n{_fmt_ts(seg.t0)} --> {_fmt_ts(seg.t1)}\n{seg.src}\n{seg.tgt}\n"
        )
    (out_dir / "transcript.srt").write_text(
        "\n".join(srt_parts) + ("\n" if srt_parts else ""), encoding="utf-8"
    )

    with (out_dir / "transcript.jsonl").open("w", encoding="utf-8") as f:
        for seg in segments:
            f.write(
                json.dumps(
                    {
                        "idx": seg.idx,
                        "t0": round(seg.t0, 3),
                        "t1": round(seg.t1, 3),
                        "src": seg.src,
                        "tgt": seg.tgt,
                        "chunk_idx": seg.chunk_idx,
                        "asr_ms": round(seg.asr_ms, 1),
                        "mt_ms": round(seg.mt_ms, 1),
                        "mt_path": seg.mt_path,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def retranscribe_audio(
    audio: np.ndarray,
    *,
    asr: StreamingAsr | None = None,
    translator: Translator | None = None,
    force_src_lang: str | None = None,
    sr: int = config.SAMPLE_RATE,
) -> tuple[list[Segment], dict[str, Any]]:
    """核心：音频 → segments + 统计（不写盘）。"""
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    own_asr = asr is None
    own_mt = translator is None
    if asr is None:
        asr = StreamingAsr()
    if translator is None:
        translator = Translator()

    t_wall0 = time.perf_counter()
    chunks, dropped = split_chunks(audio, sr=sr)
    segments: list[Segment] = []
    asr_ms_sum = 0.0
    mt_ms_sum = 0.0
    empty_asr = 0
    idx = 0

    for ci, (a, b) in enumerate(chunks):
        piece = audio[a:b]
        t0 = a / float(sr)
        t1 = b / float(sr)
        res = asr.transcribe_offline_only(piece)
        asr_ms_sum += float(res.ms)
        text = (res.text or "").strip()
        if not text:
            empty_asr += 1
            continue
        sents = split_sentences(text)
        stamped = _alloc_timestamps(sents, t0, t1)
        # 整块 asr_ms 均摊到句
        per_asr = float(res.ms) / max(1, len(stamped))
        for st0, st1, src in stamped:
            src_lang = force_src_lang or detect_lang(src)
            mt = translator.translate(src, src_lang)
            mt_ms_sum += float(mt.ms)
            segments.append(
                Segment(
                    idx=idx,
                    t0=st0,
                    t1=st1,
                    src=src,
                    tgt=mt.text or "",
                    chunk_idx=ci,
                    asr_ms=per_asr,
                    mt_ms=float(mt.ms),
                    mt_path="retranscribe",
                )
            )
            idx += 1

    report = {
        "duration": round(audio.size / float(sr), 3),
        "chunks": len(chunks),
        "dropped_silence": dropped,
        "empty_asr_chunks": empty_asr,
        "segments": len(segments),
        "asr_ms": round(asr_ms_sum, 1),
        "mt_ms": round(mt_ms_sum, 1),
        "wall_ms": round((time.perf_counter() - t_wall0) * 1000, 1),
        "timestamps": "approximate",
        "sample_rate": sr,
    }
    # 避免误导：本地创建的实例可丢弃引用
    del own_asr, own_mt
    return segments, report


def retranscribe_session(
    session_dir: str | Path,
    *,
    live_path: str | Path | None = None,
    force_src_lang: str | None = None,
    asr: StreamingAsr | None = None,
    translator: Translator | None = None,
) -> RetranscribeResult:
    session = resolve_session_dir(session_dir)
    wav = session / "mic_raw.wav"
    print(f"[post] session={session}")
    audio = load_wav(str(wav), target_sr=config.SAMPLE_RATE)
    segments, report = retranscribe_audio(
        audio,
        asr=asr,
        translator=translator,
        force_src_lang=force_src_lang,
    )
    live_file = Path(live_path) if live_path else (session / "live.jsonl")
    live_rows: list[dict] = []
    if live_file.is_file():
        for line in live_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                live_rows.append(json.loads(line))
        report["live_compare"] = compare_live(live_rows, segments)
    else:
        report["live_compare"] = {"note": "no live.jsonl"}
    write_outputs(session, segments, report)
    print(
        f"[post] segments={len(segments)} chunks={report['chunks']} "
        f"dropped_silence={report['dropped_silence']} "
        f"asr_ms={report['asr_ms']} mt_ms={report['mt_ms']} "
        f"wall_ms={report['wall_ms']}"
    )
    if "live_missed" in (report.get("live_compare") or {}):
        lc = report["live_compare"]
        print(
            f"[post] live_missed={lc.get('live_missed')} "
            f"live_wrong={lc.get('live_wrong')}"
        )
    print(f"[post] 写出 → {session / 'transcript.md'}")
    return RetranscribeResult(session_dir=session, segments=segments, report=report)
