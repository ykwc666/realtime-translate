"""会话旁路录音：mic_raw.wav + live.jsonl + meta.json。不阻塞 feed_chunk。"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from . import config
from .harvest import row_from_stats


def sessions_root() -> Path:
    return Path(config.ROOT) / "data" / "sessions"


def next_session_dir(root: Path | None = None) -> Path:
    base = Path(root) if root is not None else sessions_root()
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = base / stamp
    n = 0
    while path.exists():
        n += 1
        path = base / f"{stamp}_{n}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def latest_session_dir(root: Path | None = None) -> Path | None:
    base = Path(root) if root is not None else sessions_root()
    if not base.is_dir():
        return None
    dirs = [p for p in base.iterdir() if p.is_dir() and (p / "mic_raw.wav").exists()]
    if not dirs:
        return None
    return max(dirs, key=lambda p: p.stat().st_mtime)


def _git_commit() -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(config.ROOT),
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
        return (out or "").strip() or None
    except Exception:  # noqa: BLE001
        return None


class SessionRecorder:
    """降噪前写 mic；旁路 live.jsonl；close 时写 meta。"""

    def __init__(
        self,
        session_dir: Path | None = None,
        *,
        mode: str = "vad",
        src_lang: str = "zh",
        tgt_lang: str = "en",
        sample_rate: int = config.SAMPLE_RATE,
    ) -> None:
        self.dir = Path(session_dir) if session_dir is not None else next_session_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.src_lang = src_lang
        self.tgt_lang = tgt_lang
        self.sample_rate = int(sample_rate)
        self.wav_path = self.dir / "mic_raw.wav"
        self.live_path = self.dir / "live.jsonl"
        self.meta_path = self.dir / "meta.json"
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self._t0 = time.perf_counter()
        self._n_samples = 0
        self._lock = threading.Lock()
        self._write_failed = False
        self._warned = False
        self._sf: sf.SoundFile | None = None
        self._live_n = 0
        try:
            self._sf = sf.SoundFile(
                str(self.wav_path),
                mode="w",
                samplerate=self.sample_rate,
                channels=1,
                subtype="PCM_16",
            )
        except Exception as exc:  # noqa: BLE001
            self._write_failed = True
            print(f"[record] 无法打开 {self.wav_path}: {exc}")
            self._sf = None
        print(f"录音 → {self.dir.resolve()}")

    def write_chunk(self, chunk: np.ndarray) -> None:
        """写入原始 float32 块（应在 denoise 之前调用）。"""
        if self._sf is None or self._write_failed:
            return
        arr = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if arr.size == 0:
            return
        try:
            with self._lock:
                if self._sf is None:
                    return
                self._sf.write(arr)
                self._n_samples += int(arr.size)
        except Exception as exc:  # noqa: BLE001
            self._write_failed = True
            if not self._warned:
                self._warned = True
                print(f"[record] 写入失败，后续跳过录音: {exc}")

    def attach_engine(self, engine: Any) -> None:
        prev = getattr(engine, "on_utt_final", None)

        def _cb(stats: Any) -> None:
            self.emit_live(stats)
            if callable(prev):
                prev(stats)

        engine.on_utt_final = _cb

    def emit_live(self, stats: Any) -> None:
        row = row_from_stats(
            stats, mode=self.mode, source_desc=f"session:{self.dir.name}"
        )
        try:
            with self._lock:
                with self.live_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                self._live_n += 1
        except Exception as exc:  # noqa: BLE001
            if not self._warned:
                self._warned = True
                print(f"[record] live.jsonl 写入失败: {exc}")

    def close(self) -> None:
        duration = float(self._n_samples) / float(self.sample_rate) if self.sample_rate else 0.0
        with self._lock:
            if self._sf is not None:
                try:
                    self._sf.flush()
                    self._sf.close()
                except Exception:  # noqa: BLE001
                    pass
                self._sf = None
        meta = {
            "mode": self.mode,
            "src_lang": self.src_lang,
            "tgt_lang": self.tgt_lang,
            "started_at": self.started_at,
            "ended_at": datetime.now().isoformat(timespec="seconds"),
            "duration_sec": round(duration, 3),
            "n_samples": self._n_samples,
            "sample_rate": self.sample_rate,
            "denoise": bool(getattr(config, "MIC_DENOISE", False)),
            "git_commit": _git_commit(),
            "live_utts": self._live_n,
            "wav": self.wav_path.name,
            "write_failed": self._write_failed,
            "wall_sec": round(time.perf_counter() - self._t0, 3),
            "config": {
                "VAD_TEN_THRESHOLD": getattr(config, "VAD_TEN_THRESHOLD", None),
                "VAD_SILENCE_SEC": getattr(config, "VAD_SILENCE_SEC", None),
                "VAD_SPEECH_RMS_MIN": getattr(config, "VAD_SPEECH_RMS_MIN", None),
                "ASR_SPEC_REJECT_STALE": getattr(config, "ASR_SPEC_REJECT_STALE", None),
                "ASR_2PASS_MERGE_MODE": getattr(config, "ASR_2PASS_MERGE_MODE", None),
                "ASR_BACKEND": getattr(config, "ASR_BACKEND", None),
                "MT_BACKEND": getattr(config, "MT_BACKEND", None),
            },
        }
        try:
            self.meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[record] meta.json 写入失败: {exc}")
        print(
            f"[record] 结束 duration={duration:.1f}s live_utts={self._live_n} → {self.dir}"
        )
