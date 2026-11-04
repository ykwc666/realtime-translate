"""Sherpa OfflinePunctuation (CT-Transformer zh-en) wrapper.

The downloaded model is *offline* CT-Transformer (not OnlinePunctuation/EN-cnn).
High-level add_punctuation() always emits a terminal 。/？ — it cannot tell
"说话未完". Raw last-token logits also fail to separate 现在是星期 vs 星期二
(see docs/PUNCT_ENDPOINT_PROBE.md). Keep weekday whitelist for completion.

Use this module for optional display punctuation / future experiments only.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from . import config

_lock = threading.Lock()
_punct = None
_load_failed = False


def punct_model_path() -> Path:
    return Path(
        getattr(
            config,
            "SHERPA_PUNCT_MODEL",
            Path(__file__).resolve().parents[1]
            / "models"
            / "sherpa-punct-ct-zh-en"
            / "model.int8.onnx",
        )
    )


def punct_available() -> bool:
    p = punct_model_path()
    return p.is_file() and p.stat().st_size > 1_000_000


def _ensure() -> object | None:
    global _punct, _load_failed
    if _punct is not None:
        return _punct
    if _load_failed or not bool(getattr(config, "ASR_PUNCT_ENABLED", True)):
        return None
    if not punct_available():
        return None
    with _lock:
        if _punct is not None:
            return _punct
        if _load_failed:
            return None
        try:
            import sherpa_onnx

            path = punct_model_path()
            t0 = time.perf_counter()
            cfg = sherpa_onnx.OfflinePunctuationConfig(
                model=sherpa_onnx.OfflinePunctuationModelConfig(
                    ct_transformer=str(path),
                    num_threads=int(getattr(config, "ASR_PUNCT_THREADS", 2)),
                )
            )
            _punct = sherpa_onnx.OfflinePunctuation(cfg)
            print(
                f"[punct] loaded {path.name} "
                f"in {(time.perf_counter() - t0) * 1000:.0f}ms"
            )
        except Exception as exc:  # noqa: BLE001
            _load_failed = True
            print(f"[punct] load failed: {exc}")
            return None
    return _punct


def add_punctuation(text: str) -> tuple[str, float]:
    """Return (punctuated_or_original, ms). No-op if model unavailable."""
    t = (text or "").strip()
    if not t:
        return "", 0.0
    if not bool(getattr(config, "ASR_PUNCT_ENABLED", True)):
        return t, 0.0
    eng = _ensure()
    if eng is None:
        return t, 0.0
    t0 = time.perf_counter()
    try:
        out = (eng.add_punctuation(t) or t).strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[punct] infer error: {exc}")
        return t, 0.0
    return out or t, (time.perf_counter() - t0) * 1000
