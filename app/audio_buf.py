"""可增长的 float32 音频缓冲，避免每块 O(n) 全量拷贝。"""
from __future__ import annotations

import numpy as np


class AudioBuffer:
    def __init__(self) -> None:
        self._chunks: list[np.ndarray] = []
        self._size = 0

    def clear(self) -> None:
        self._chunks.clear()
        self._size = 0

    @property
    def size(self) -> int:
        return self._size

    def append(self, chunk: np.ndarray) -> None:
        chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if chunk.size == 0:
            return
        self._chunks.append(chunk)
        self._size += int(chunk.size)

    def asarray(self) -> np.ndarray:
        """物化为连续视图；就地合并各块。"""
        if self._size == 0:
            return np.zeros(0, dtype=np.float32)
        if len(self._chunks) == 1:
            return self._chunks[0]
        merged = np.concatenate(self._chunks)
        self._chunks = [merged]
        return merged

    def slice_from(self, start: int) -> np.ndarray:
        start = max(0, int(start))
        arr = self.asarray()
        if start >= arr.size:
            return np.zeros(0, dtype=np.float32)
        return arr[start:]

    def copy(self) -> np.ndarray:
        return self.asarray().copy()
