from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import config

# Hot-path backends only. FunASR paths removed from runtime.
_SUPPORTED = frozenset({"sherpa_paraformer_online", "sherpa_sensevoice"})


@dataclass
class AsrResult:
    text: str
    ms: float
    audio_samples: int = 0


@dataclass
class AsrIncState:
    """Incremental / streaming ASR state for one utterance."""

    decoded_samples: int = 0
    text: str = ""
    calls: int = 0
    total_model_ms: float = 0.0
    stream: Any = None
    finished: bool = False


def prefer_final_asr(partial: str, final: str) -> str:
    """Pick longer text when one is a prefix of the other (guard trailing-char loss)."""
    p = (partial or "").strip()
    f = (final or "").strip()
    if not p:
        return f
    if not f:
        return p
    if f.startswith(p) or p.startswith(f):
        return f if len(f) >= len(p) else p
    return f


def _strip_asr_tail_punct(text: str) -> str:
    return (text or "").strip().rstrip("。！？.!?,，、；; ")


def prefer_2pass_asr(stream_text: str, offline_text: str) -> str:
    """Merge streaming Paraformer final with SenseVoice offline rescoring."""
    s = _strip_asr_tail_punct(stream_text)
    o = _strip_asr_tail_punct(offline_text)
    if not o:
        return s
    if not s:
        return o
    if o.startswith(s) or s.startswith(o):
        return o if len(o) >= len(s) else s
    if s in o:
        return o
    if o in s and len(s) - len(o) <= 2:
        return s
    common = 0
    for a, b in zip(s, o):
        if a != b:
            break
        common += 1
    if common >= max(2, (len(s) + 1) // 2):
        return o if len(o) >= len(s) else s
    # Short stream + clearly longer offline → trust offline (mic truncations)
    if len(o) >= len(s) + 2:
        return o
    return s


def merge_overlap_text(prev: str, tail: str) -> str:
    """Merge previous hypothesis with SenseVoice overlap-window ASR text."""
    prev = (prev or "").strip()
    tail = (tail or "").strip()
    if not prev:
        return tail
    if not tail:
        return prev
    stripped = re.sub(r"^(?:[A-Za-z]{1,12}[。.!?\,]?\s*)+", "", prev).strip()
    if stripped != prev and re.search(r"[\u4e00-\u9fff]", tail):
        if not stripped:
            return tail
        prev = stripped
    elif re.match(r"^[A-Za-z]{2,8}[。.!?]*$", prev) and re.search(
        r"[\u4e00-\u9fff]", tail
    ):
        return tail
    if tail.startswith(prev):
        return tail
    if prev.startswith(tail) and len(tail) < len(prev):
        return prev

    max_k = min(len(prev), len(tail), 24)
    best = 0
    for k in range(max_k, 0, -1):
        if prev.endswith(tail[:k]):
            best = k
            break
        a = prev.rstrip("。！？.!?,， ")
        b = tail[:k].rstrip("。！？.!?,， ")
        if a.endswith(b) or prev[-k:] == tail[:k]:
            best = k
            break
    if best > 0:
        return prev + tail[best:]

    needle_max = min(len(prev), len(tail), 12)
    for k in range(needle_max, 2, -1):
        piece = prev[-k:]
        idx = tail.find(piece)
        if idx >= 0:
            return prev[:-k] + tail[idx:]
        piece2 = prev[-k:].rstrip("。！？.!?,， ")
        if piece2 and piece2 in tail:
            idx = tail.find(piece2)
            return prev[: len(prev) - k] + tail[idx:]

    return prev + tail


class StreamingAsr:
    """ASR: sherpa Online Paraformer (default) or sherpa SenseVoice (fallback)."""

    def __init__(self) -> None:
        os.environ.setdefault("OMP_NUM_THREADS", str(config.ASR_THREADS))
        self.backend = config.ASR_BACKEND
        if self.backend not in _SUPPORTED:
            raise ValueError(
                f"不支持 ASR_BACKEND={self.backend!r}；"
                f"可选: {', '.join(sorted(_SUPPORTED))}"
            )
        self.is_true_streaming = self.backend == "sherpa_paraformer_online"
        self.recognizer = None  # SenseVoice offline (primary or 2-pass)
        self._2pass = False
        if self.is_true_streaming:
            self._init_sherpa_paraformer_online()
            if bool(getattr(config, "ASR_FINAL_2PASS", False)):
                self._init_sherpa_sensevoice()
                self._2pass = self.recognizer is not None
        else:
            self._init_sherpa_sensevoice()

    def _init_sherpa_sensevoice(self) -> None:
        import sherpa_onnx

        if not config.SHERPA_SENSEVOICE_MODEL.exists():
            raise FileNotFoundError(
                f"缺少 {config.SHERPA_SENSEVOICE_MODEL}，请运行 scripts/download_sherpa_sensevoice.py"
            )
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(config.SHERPA_SENSEVOICE_MODEL),
            tokens=str(config.SHERPA_SENSEVOICE_TOKENS),
            num_threads=int(config.ASR_THREADS),
            use_itn=True,
            debug=False,
            language="auto",
        )

    def _init_sherpa_paraformer_online(self) -> None:
        import sherpa_onnx

        enc = config.SHERPA_PF_ONLINE_ENCODER
        dec = config.SHERPA_PF_ONLINE_DECODER
        tok = config.SHERPA_PF_ONLINE_TOKENS
        if not (enc.exists() and dec.exists() and tok.exists()):
            raise FileNotFoundError(
                f"缺少流式 Paraformer，请运行 scripts/download_sherpa_paraformer_online.py\n"
                f"  need {enc.name}, {dec.name}, {tok.name}"
            )
        self.online = sherpa_onnx.OnlineRecognizer.from_paraformer(
            tokens=str(tok),
            encoder=str(enc),
            decoder=str(dec),
            num_threads=int(config.ASR_THREADS),
            sample_rate=int(config.SAMPLE_RATE),
            enable_endpoint_detection=False,
            provider="cpu",
            debug=False,
        )

    def transcribe(self, audio: np.ndarray) -> AsrResult:
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            return AsrResult("", 0.0, 0)
        if self.is_true_streaming:
            return self._transcribe_paraformer_online_oneshot(audio)
        return self._transcribe_sherpa_offline(audio)

    def feed_stream(self, new_audio: np.ndarray, state: AsrIncState) -> AsrResult:
        """True-streaming only: feed newly arrived samples (no full-buffer copy)."""
        if not self.is_true_streaming:
            raise RuntimeError("feed_stream 仅用于 sherpa_paraformer_online")
        new_audio = np.asarray(new_audio, dtype=np.float32).reshape(-1)
        if state.finished:
            return AsrResult(state.text, 0.0, 0)
        if state.stream is None:
            state.stream = self.online.create_stream()
            state.decoded_samples = 0
        if new_audio.size == 0:
            return AsrResult(state.text, 0.0, 0)

        t0 = time.perf_counter()
        state.stream.accept_waveform(config.SAMPLE_RATE, new_audio)
        while self.online.is_ready(state.stream):
            self.online.decode_stream(state.stream)
        text = (self.online.get_result(state.stream) or "").strip()
        ms = (time.perf_counter() - t0) * 1000
        fed = int(new_audio.size)
        state.decoded_samples += fed
        state.text = text
        state.calls += 1
        state.total_model_ms += ms
        return AsrResult(text, ms, fed)

    def transcribe_incremental(
        self, full_audio: np.ndarray, state: AsrIncState
    ) -> AsrResult:
        """
        Offline SenseVoice: overlap-window incremental.
        True streaming: prefer feed_stream(); this still works if given full audio.
        """
        full_audio = np.asarray(full_audio, dtype=np.float32).reshape(-1)
        if full_audio.size == 0:
            return AsrResult(state.text, 0.0, 0)

        if self.is_true_streaming:
            new = full_audio[state.decoded_samples :]
            return self.feed_stream(new, state)

        if not config.ASR_INCREMENTAL or state.decoded_samples <= 0:
            res = self.transcribe(full_audio)
            state.decoded_samples = full_audio.size
            state.text = res.text
            state.calls += 1
            state.total_model_ms += res.ms
            return AsrResult(state.text, res.ms, full_audio.size)

        overlap = int(config.ASR_OVERLAP_SEC * config.SAMPLE_RATE)
        start = max(0, state.decoded_samples - overlap)
        if full_audio.size - state.decoded_samples < int(0.15 * config.SAMPLE_RATE):
            return AsrResult(state.text, 0.0, 0)

        clip = full_audio[start:]
        res = self.transcribe(clip)
        merged = merge_overlap_text(state.text, res.text)
        state.text = merged
        state.decoded_samples = full_audio.size
        state.calls += 1
        state.total_model_ms += res.ms
        return AsrResult(merged, res.ms, clip.size)

    def transcribe_with_2pass(self, audio: np.ndarray) -> AsrResult:
        """Independent oneshot (+ SenseVoice) for speculative finalize; no live state."""
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            return AsrResult("", 0.0, 0)
        if self.is_true_streaming:
            online = self._transcribe_paraformer_online_oneshot(audio)
            if not self._2pass or self.recognizer is None:
                return online
            offline = self._transcribe_sherpa_offline(audio)
            merged = prefer_2pass_asr(online.text, offline.text)
            ms = float(online.ms) + float(offline.ms)
            return AsrResult(merged, ms, int(audio.size))
        return self.transcribe(audio)

    def finalize_stream(
        self, full_audio: np.ndarray, state: AsrIncState
    ) -> AsrResult:
        full_audio = np.asarray(full_audio, dtype=np.float32).reshape(-1)
        if not self.is_true_streaming:
            return self.transcribe(full_audio)
        online = self._finalize_paraformer_online(full_audio, state)
        if not self._2pass or self.recognizer is None or full_audio.size == 0:
            return online
        offline = self._transcribe_sherpa_offline(full_audio)
        stream_t = (online.text or "").strip()
        off_t = (offline.text or "").strip()
        merged = prefer_2pass_asr(stream_t, off_t)
        ms = float(online.ms) + float(offline.ms)
        if merged != stream_t:
            print(
                f"[ASR 2pass offline {offline.ms:.0f}ms] "
                f"{stream_t!r} → {merged!r}"
            )
        return AsrResult(merged, ms, online.audio_samples)

    @staticmethod
    def _mark_paraformer_final(stream) -> bool:
        """Flush CIF / short final chunk (sherpa OnlineStream option)."""
        setter = getattr(stream, "set_option", None)
        if setter is None:
            return False
        try:
            setter("is_final", "1")
            return True
        except Exception:
            return False

    def _finalize_paraformer_online(
        self, full_audio: np.ndarray, state: AsrIncState
    ) -> AsrResult:
        if state.stream is None:
            return self._transcribe_paraformer_online_oneshot(full_audio)

        t0 = time.perf_counter()
        fed = 0
        if full_audio.size > state.decoded_samples:
            tail = full_audio[state.decoded_samples :]
            fed = int(tail.size)
            state.stream.accept_waveform(config.SAMPLE_RATE, tail)
            state.decoded_samples = full_audio.size
        # is_final before pad/input_finished: flush residual CIF + short last chunk
        self._mark_paraformer_final(state.stream)
        pad_sec = float(getattr(config, "ONLINE_TAIL_PAD_SEC", 1.0))
        pad_n = int(pad_sec * config.SAMPLE_RATE)
        if pad_n > 0:
            pad = np.zeros(pad_n, dtype=np.float32)
            state.stream.accept_waveform(config.SAMPLE_RATE, pad)
            fed += pad_n
        state.stream.input_finished()
        while self.online.is_ready(state.stream):
            self.online.decode_stream(state.stream)
        text = (self.online.get_result(state.stream) or "").strip()
        ms = (time.perf_counter() - t0) * 1000
        state.text = text
        state.finished = True
        state.calls += 1
        state.total_model_ms += ms
        return AsrResult(text, ms, fed)

    def _transcribe_paraformer_online_oneshot(self, audio: np.ndarray) -> AsrResult:
        t0 = time.perf_counter()
        stream = self.online.create_stream()
        stream.accept_waveform(config.SAMPLE_RATE, audio)
        self._mark_paraformer_final(stream)
        pad_sec = float(getattr(config, "ONLINE_TAIL_PAD_SEC", 1.0))
        pad_n = int(max(0.0, pad_sec) * config.SAMPLE_RATE)
        if pad_n > 0:
            stream.accept_waveform(
                config.SAMPLE_RATE, np.zeros(pad_n, dtype=np.float32)
            )
        stream.input_finished()
        while self.online.is_ready(stream):
            self.online.decode_stream(stream)
        text = (self.online.get_result(stream) or "").strip()
        ms = (time.perf_counter() - t0) * 1000
        return AsrResult(text, ms, audio.size)

    def _transcribe_sherpa_offline(self, audio: np.ndarray) -> AsrResult:
        t0 = time.perf_counter()
        stream = self.recognizer.create_stream()
        stream.accept_waveform(config.SAMPLE_RATE, audio)
        self.recognizer.decode_stream(stream)
        text = (stream.result.text or "").strip()
        ms = (time.perf_counter() - t0) * 1000
        return AsrResult(text, ms, audio.size)
