from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf
from piper import PiperVoice

from . import config


@dataclass
class TtsResult:
    path: Path | None
    ms: float
    voice: str
    audio: np.ndarray | None = None
    sample_rate: int = 22050


class TtsPlayer:
    """Offline Piper TTS (no network)."""

    def __init__(
        self,
        out_dir: Path | None = None,
        output_device: int | str | None = None,
    ) -> None:
        self.out_dir = out_dir or (config.CACHE_DIR / "tts")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._voices: dict[str, PiperVoice] = {}
        self._warmed = False
        self._io_lock = threading.Lock()  # duplex: mic+loopback share one player
        self.output_device = self._resolve_device(output_device)

    @staticmethod
    def _resolve_device(output_device: int | str | None) -> int | None:
        from .audio_io import resolve_output_device

        if isinstance(output_device, int):
            return output_device
        if isinstance(output_device, str) and output_device.strip():
            return resolve_output_device(output_device)
        return resolve_output_device(None)

    def _load(self, lang: str) -> tuple[PiperVoice, str]:
        if lang not in self._voices:
            model = config.PIPER_VOICE_ZH if lang == "zh" else config.PIPER_VOICE_EN
            if not model.exists():
                raise FileNotFoundError(
                    f"缺少 Piper 音色: {model}\n请运行: python scripts/download_piper_voices.py"
                )
            cfg = Path(str(model) + ".json")
            self._voices[lang] = PiperVoice.load(
                str(model),
                config_path=str(cfg) if cfg.exists() else None,
                use_cuda=False,
            )
        name = (
            config.PIPER_VOICE_ZH.name if lang == "zh" else config.PIPER_VOICE_EN.name
        )
        return self._voices[lang], name

    def warmup(self) -> dict[str, float]:
        """Load zh/en voices and run a tiny synth so first real call is warm."""
        costs: dict[str, float] = {}
        for lang, text in (("zh", "测试"), ("en", "test")):
            t0 = time.perf_counter()
            self._load(lang)
            _ = self.synthesize(text, lang, save_wav=False)
            costs[lang] = (time.perf_counter() - t0) * 1000
        self._warmed = True
        return costs

    def synthesize(
        self, text: str, lang: str, save_wav: bool = True
    ) -> TtsResult:
        text = (text or "").strip()
        if not text:
            return TtsResult(None, 0.0, "")

        voice, voice_name = self._load(lang)
        t0 = time.perf_counter()
        chunks: list[np.ndarray] = []
        sample_rate = 22050
        for chunk in voice.synthesize(text):
            sample_rate = int(chunk.sample_rate)
            audio = np.asarray(chunk.audio_float_array, dtype=np.float32)
            if audio.size:
                chunks.append(audio)
        ms = (time.perf_counter() - t0) * 1000
        if not chunks:
            return TtsResult(None, ms, voice_name)
        wav = np.concatenate(chunks)
        path = None
        if save_wav:
            path = self.out_dir / f"utt_{int(time.time() * 1000)}.wav"
            sf.write(str(path), wav, sample_rate)
        return TtsResult(path, ms, voice_name, audio=wav, sample_rate=sample_rate)

    def play_array(self, audio: np.ndarray, sample_rate: int) -> float:
        if audio.size == 0:
            return 0.0
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        pad_sec = float(getattr(config, "TTS_PLAY_TAIL_PAD_SEC", 0.18))
        if pad_sec > 0:
            n = int(pad_sec * sample_rate)
            if n > 0:
                audio = np.concatenate(
                    [audio, np.zeros(n, dtype=np.float32)]
                )
        duration = len(audio) / float(sample_rate)
        # blocking=True 在部分 Windows 设备上仍会略早返回；再等一小段硬件尾
        kwargs = {"blocking": True}
        if self.output_device is not None:
            kwargs["device"] = self.output_device
        sd.play(audio, sample_rate, **kwargs)
        sd.wait()
        return duration


    def play_file(self, path: Path) -> float:
        data, sr = sf.read(str(path), dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        return self.play_array(data, sr)

    def speak(self, text: str, lang: str, save_wav: bool = False) -> tuple[TtsResult, float]:
        with self._io_lock:
            syn = self.synthesize(text, lang, save_wav=save_wav)
            play_sec = 0.0
            if syn.audio is not None and syn.audio.size:
                play_sec = self.play_array(syn.audio, syn.sample_rate)
            elif syn.path and syn.path.exists():
                play_sec = self.play_file(syn.path)
            return syn, play_sec
