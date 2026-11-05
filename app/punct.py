"""Sherpa OfflinePunctuation（CT-Transformer 中英）封装。

下载的模型为*离线* CT-Transformer（非 OnlinePunctuation/EN-cnn）。
高层 add_punctuation() 总会输出句末 。/？ — 无法判断「说话未完」。
原始末 token logits 也无法区分「现在是星期」与「星期二」
（见 docs/PUNCT_ENDPOINT_PROBE.md）。句末仍保留 weekday 白名单补全。

本模块仅用于可选展示标点 / 未来实验。
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
    """返回 (加标点或原文, 毫秒)。模型不可用时为 no-op。"""
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
