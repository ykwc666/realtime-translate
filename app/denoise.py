"""可选的流式 GTCRN 麦克风降噪（sherpa-onnx OnlineSpeechDenoiser）。"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from . import config


class MicDenoiser:
    """按帧对齐的流式降噪；模型缺失或禁用时直通。"""

    def __init__(self) -> None:
        self._dn = None
        self._frame = 0
        self._buf = np.zeros(0, dtype=np.float32)
        path = Path(getattr(config, "GTCRN_MODEL", config.MODELS_DIR / "gtcrn_simple.onnx"))
        if not bool(getattr(config, "MIC_DENOISE", False)):
            print("[denoise] off (MIC_DENOISE=False)")
            return
        if not path.is_file():
            print(f"[denoise] missing {path} — pass-through; run scripts/download_gtcrn.py")
            return
        try:
            import sherpa_onnx

            model_cfg = sherpa_onnx.OfflineSpeechDenoiserModelConfig(
                gtcrn=sherpa_onnx.OfflineSpeechDenoiserGtcrnModelConfig(
                    model=str(path)
                ),
                num_threads=int(getattr(config, "MIC_DENOISE_THREADS", 1)),
                provider="cpu",
            )
            cfg = sherpa_onnx.OnlineSpeechDenoiserConfig(model=model_cfg)
            self._dn = sherpa_onnx.OnlineSpeechDenoiser(cfg)
            self._frame = int(self._dn.frame_shift_in_samples)
            print(
                f"[denoise] GTCRN online frame={self._frame} "
                f"sr={self._dn.sample_rate}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[denoise] init failed: {exc} — pass-through")
            self._dn = None

    @property
    def enabled(self) -> bool:
        return self._dn is not None

    def process(self, chunk: np.ndarray) -> np.ndarray:
        chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if self._dn is None or chunk.size == 0:
            return chunk
        self._buf = np.concatenate([self._buf, chunk])
        out: list[np.ndarray] = []
        sr = int(config.SAMPLE_RATE)
        while self._buf.size >= self._frame:
            frame = self._buf[: self._frame]
            self._buf = self._buf[self._frame :]
            try:
                denoised = self._dn.run(frame, sr)
                samples = np.asarray(denoised.samples, dtype=np.float32).reshape(-1)
            except Exception:  # noqa: BLE001
                samples = frame
            out.append(samples)
        if not out:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(out)

    def flush(self) -> np.ndarray:
        if self._dn is None:
            rem = self._buf
            self._buf = np.zeros(0, dtype=np.float32)
            return rem
        out: list[np.ndarray] = []
        if self._buf.size:
            # 补零至整帧
            need = self._frame - (self._buf.size % self._frame)
            if need != self._frame:
                self._buf = np.concatenate(
                    [self._buf, np.zeros(need, dtype=np.float32)]
                )
            while self._buf.size >= self._frame:
                frame = self._buf[: self._frame]
                self._buf = self._buf[self._frame :]
                try:
                    samples = np.asarray(
                        self._dn.run(frame, int(config.SAMPLE_RATE)).samples,
                        dtype=np.float32,
                    ).reshape(-1)
                except Exception:  # noqa: BLE001
                    samples = frame
                out.append(samples)
        try:
            tail = np.asarray(self._dn.flush().samples, dtype=np.float32).reshape(-1)
            if tail.size:
                out.append(tail)
        except Exception:  # noqa: BLE001
            pass
        self._buf = np.zeros(0, dtype=np.float32)
        if not out:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(out)
