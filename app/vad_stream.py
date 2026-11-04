from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import config


@dataclass
class SpeechSegment:
    audio: np.ndarray
    start_sample: int
    end_sample: int


def create_endpoint():
    """Factory: silero (default) or energy fallback."""
    if config.VAD_BACKEND == "silero" and config.SILERO_VAD_ONNX.exists():
        try:
            return SileroVadEndpoint()
        except Exception as exc:  # noqa: BLE001
            print(f"[vad] Silero 初始化失败，回退能量 VAD: {exc}")
    return EnergyEndpoint()


class EnergyEndpoint:
    """Lightweight end-of-utterance detector for half-duplex mic loop."""

    def __init__(
        self,
        sample_rate: int = config.SAMPLE_RATE,
        silence_sec: float = config.VAD_SILENCE_SEC,
        min_speech_sec: float = config.MIN_SPEECH_SEC,
        energy_threshold: float | None = None,
    ) -> None:
        self.sr = sample_rate
        self.silence_needed = int(silence_sec * sample_rate)
        self.min_speech = int(min_speech_sec * sample_rate)
        self.energy_threshold = (
            config.VAD_ENERGY_THRESHOLD if energy_threshold is None else energy_threshold
        )
        self._noise_ema = self.energy_threshold * 0.4
        self.reset()

    @property
    def in_speech(self) -> bool:
        return self._speech_started

    @property
    def trailing_silence_sec(self) -> float:
        if not self._speech_started:
            return 0.0
        return float(self._silence) / float(self.sr)

    def reset(self) -> None:
        self._buf = np.zeros(0, dtype=np.float32)
        self._speech_started = False
        self._silence = 0
        self._absolute_pos = 0
        self._utt_start = 0

    def _rms(self, chunk: np.ndarray) -> float:
        if chunk.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(chunk), dtype=np.float64)))

    def _is_speech(self, chunk: np.ndarray) -> bool:
        rms = self._rms(chunk)
        if not self._speech_started:
            # adapt noise floor slowly in silence
            self._noise_ema = 0.95 * self._noise_ema + 0.05 * rms
            thr = max(self.energy_threshold, self._noise_ema * 3.5)
        else:
            thr = max(self.energy_threshold * 0.7, self._noise_ema * 2.2)
        return rms >= thr

    def feed(self, chunk: np.ndarray) -> SpeechSegment | None:
        chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if chunk.size == 0:
            return None

        speaking = self._is_speech(chunk)
        if speaking:
            if not self._speech_started:
                self._speech_started = True
                self._utt_start = self._absolute_pos
                self._buf = np.zeros(0, dtype=np.float32)
            self._silence = 0
            self._buf = np.concatenate([self._buf, chunk])
        elif self._speech_started:
            self._silence += chunk.size
            self._buf = np.concatenate([self._buf, chunk])
            if self._silence >= self.silence_needed and self._buf.size >= self.min_speech:
                keep = max(
                    self.min_speech,
                    self._buf.size - self._silence + int(0.08 * self.sr),
                )
                audio = self._buf[:keep].copy()
                start = self._utt_start
                end = start + audio.size
                self.reset()
                self._absolute_pos = end
                return SpeechSegment(audio=audio, start_sample=start, end_sample=end)

        self._absolute_pos += chunk.size
        return None


class SileroVadEndpoint:
    """sherpa-onnx Silero VAD with EnergyEndpoint-compatible API."""

    def __init__(self) -> None:
        import sherpa_onnx

        if not config.SILERO_VAD_ONNX.exists():
            raise FileNotFoundError(config.SILERO_VAD_ONNX)
        self.sr = config.SAMPLE_RATE
        vad_cfg = sherpa_onnx.VadModelConfig()
        vad_cfg.silero_vad.model = str(config.SILERO_VAD_ONNX)
        vad_cfg.silero_vad.threshold = float(
            getattr(config, "VAD_SILERO_THRESHOLD", 0.6)
        )
        vad_cfg.silero_vad.min_silence_duration = float(config.VAD_SILENCE_SEC)
        vad_cfg.silero_vad.min_speech_duration = float(config.MIN_SPEECH_SEC)
        vad_cfg.sample_rate = config.SAMPLE_RATE
        vad_cfg.num_threads = 1
        vad_cfg.provider = "cpu"
        self._vad = sherpa_onnx.VoiceActivityDetector(vad_cfg, buffer_size_in_seconds=60)
        self._window = int(vad_cfg.silero_vad.window_size)
        self._pending = np.zeros(0, dtype=np.float32)
        self._speech_started = False
        self._utt_audio = np.zeros(0, dtype=np.float32)
        self._absolute_pos = 0
        self._utt_start = 0
        self._silence_samples = 0
        self._silence_base = int(float(config.VAD_SILENCE_SEC) * self.sr)
        self._silence_hang = int(
            (
                float(config.VAD_SILENCE_SEC)
                + float(getattr(config, "VAD_HANG_EXTRA_SILENCE_SEC", 0.7))
            )
            * self.sr
        )
        self._silence_needed = self._silence_base
        self._hang_hold = False
        self._min_speech = int(float(config.MIN_SPEECH_SEC) * self.sr)

    @property
    def in_speech(self) -> bool:
        # Prefer our latch; silero flag can lag after segment pop
        return self._speech_started or (
            bool(self._vad.is_speech_detected) and self._utt_audio.size > 0
        )

    @property
    def trailing_silence_sec(self) -> float:
        if not self._speech_started:
            return 0.0
        return float(self._silence_samples) / float(self.sr)

    def set_hang_hold(self, hold: bool) -> None:
        """Ignore Silero early segment pops while strong-hang; silence floor unchanged."""
        self._hang_hold = bool(hold)
        self._silence_needed = self._silence_base

    def reopen_after_hold(self) -> None:
        """Keep listening after a swallowed candidate cut (arbiter completion window)."""
        self._speech_started = True
        self._silence_samples = 0

    def reset(self) -> None:
        self._vad.reset()
        self._pending = np.zeros(0, dtype=np.float32)
        self._speech_started = False
        self._utt_audio = np.zeros(0, dtype=np.float32)
        self._silence_samples = 0
        self._hang_hold = False
        self._silence_needed = self._silence_base
        # keep absolute_pos continuous



    def _emit_utt(self, audio: np.ndarray) -> SpeechSegment | None:
        if audio.size < self._min_speech:
            self._speech_started = False
            self._utt_audio = np.zeros(0, dtype=np.float32)
            self._silence_samples = 0
            return None
        start = self._utt_start
        end = start + audio.size
        self._speech_started = False
        self._utt_audio = np.zeros(0, dtype=np.float32)
        self._silence_samples = 0
        return SpeechSegment(audio=audio.copy(), start_sample=start, end_sample=end)

    def flush(self) -> SpeechSegment | None:
        """Flush remaining buffered speech (file/sim end)."""
        try:
            self._vad.flush()
        except Exception:
            pass
        out = None
        while not self._vad.empty():
            seg = self._vad.front
            samples = np.asarray(seg.samples, dtype=np.float32).reshape(-1)
            self._vad.pop()
            if samples.size:
                out = SpeechSegment(
                    audio=samples.copy(),
                    start_sample=self._utt_start,
                    end_sample=self._utt_start + samples.size,
                )
        if out is None and self._utt_audio.size:
            out = self._emit_utt(self._utt_audio)
        else:
            self._speech_started = False
            self._utt_audio = np.zeros(0, dtype=np.float32)
            self._silence_samples = 0
        return out

    def feed(self, chunk: np.ndarray) -> SpeechSegment | None:
        chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if chunk.size == 0:
            return None
        self._pending = np.concatenate([self._pending, chunk])
        out: SpeechSegment | None = None

        while self._pending.size >= self._window:
            win = self._pending[: self._window]
            self._pending = self._pending[self._window :]
            self._vad.accept_waveform(win.tolist())
            if self._vad.is_speech_detected:
                # Soft energy gate: reject near-silent / fan-floor false starts
                rms = float(np.sqrt(np.mean(np.square(win), dtype=np.float64)))
                energy_floor = float(
                    getattr(
                        config,
                        "VAD_SPEECH_RMS_MIN",
                        getattr(config, "VAD_ENERGY_THRESHOLD", 0.015),
                    )
                )
                allow = self._speech_started or rms >= energy_floor

                if allow:
                    self._silence_samples = 0
                    if not self._speech_started:
                        self._speech_started = True
                        self._utt_start = self._absolute_pos
                        self._utt_audio = np.zeros(0, dtype=np.float32)
                    self._utt_audio = np.concatenate([self._utt_audio, win])
            elif self._speech_started:
                # Silero often stops flagging speech without popping a segment —
                # count trailing silence ourselves and force endpoint.
                self._utt_audio = np.concatenate([self._utt_audio, win])
                self._silence_samples += int(win.size)
                if self._silence_samples >= self._silence_needed:
                    forced = self._emit_utt(self._utt_audio)
                    if forced is not None:
                        out = forced
            self._absolute_pos += win.size

            while not self._vad.empty():
                seg = self._vad.front
                samples = np.asarray(seg.samples, dtype=np.float32).reshape(-1)
                self._vad.pop()
                if samples.size < self._min_speech:
                    continue
                # Always emit — dual_track arbiter decides hold vs cut.
                # Swallowing pops here caused「星期」粘住直到 unchanged=9.
                start = self._utt_start
                end = start + samples.size
                out = SpeechSegment(
                    audio=samples.copy(), start_sample=start, end_sample=end
                )
                self._speech_started = False
                self._utt_audio = np.zeros(0, dtype=np.float32)
                self._silence_samples = 0
        return out




def segment_audio(
    audio: np.ndarray, block_ms: int = 100
) -> list[SpeechSegment]:
    """Split a wav with the same Silero/Energy endpoint used live (no FunASR)."""
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        return []
    ep = create_endpoint()
    block = max(1, int(config.SAMPLE_RATE * block_ms / 1000))
    out: list[SpeechSegment] = []
    for i in range(0, audio.size, block):
        seg = ep.feed(audio[i : i + block])
        if seg is not None:
            out.append(seg)
    # drain trailing speech
    pad = np.zeros(
        int(config.VAD_SILENCE_SEC * config.SAMPLE_RATE) + block * 2, dtype=np.float32
    )
    for _ in range(12):
        seg = ep.feed(pad)
        if seg is not None:
            out.append(seg)
            break
    else:
        flush = getattr(ep, "flush", None)
        if callable(flush):
            seg = flush()
            if seg is not None and seg.audio.size:
                out.append(seg)
    if not out and audio.size >= int(config.MIN_SPEECH_SEC * config.SAMPLE_RATE):
        out.append(
            SpeechSegment(audio=audio.copy(), start_sample=0, end_sample=audio.size)
        )
    return out
