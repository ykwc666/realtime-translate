from __future__ import annotations

import queue
import threading
from collections.abc import Iterator

import numpy as np
import sounddevice as sd
import soundfile as sf

from . import config


def list_loopback_devices() -> list[str]:
    """WASAPI 环回采集端点名称（需 `soundcard` 包）。"""
    try:
        import soundcard as sc
    except ImportError:
        return []
    out: list[str] = []
    for m in sc.all_microphones(include_loopback=True):
        if getattr(m, "isloopback", False):
            out.append(m.name)
    return out


def list_output_devices() -> list[tuple[int, str, int]]:
    """sounddevice 播放设备：(index, name, max_output_channels)。"""
    rows: list[tuple[int, str, int]] = []
    for i, d in enumerate(sd.query_devices()):
        ch = int(d.get("max_output_channels") or 0)
        if ch > 0:
            rows.append((i, str(d.get("name") or f"#{i}"), ch))
    return rows


def resolve_output_device(name_substr: str | None = None) -> int | None:
    """TTS_OUTPUT_DEVICE_NAME 子串匹配 → 设备 index；None 为默认。"""
    needle = (name_substr if name_substr is not None else getattr(config, "TTS_OUTPUT_DEVICE_NAME", "")) or ""
    needle = needle.strip().lower()
    if not needle:
        return None
    hits = [
        (i, name)
        for i, name, _ch in list_output_devices()
        if needle in name.lower()
    ]
    if not hits:
        raise RuntimeError(
            f"找不到输出设备含 {needle!r}；可用: {[n for _, n, _ in list_output_devices()]}"
        )
    return hits[0][0]


def load_wav(path: str, target_sr: int = config.SAMPLE_RATE) -> np.ndarray:
    data, sr = sf.read(path, dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != target_sr:
        # 线性重采样；原型足够
        duration = len(data) / float(sr)
        n = int(duration * target_sr)
        x_old = np.linspace(0.0, 1.0, num=len(data), endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=n, endpoint=False)
        data = np.interp(x_new, x_old, data).astype(np.float32)
    return data.astype(np.float32)


class MicStream:
    """后台麦克风采集，写入 float32 块队列。"""

    def __init__(
        self,
        sample_rate: int = config.SAMPLE_RATE,
        block_ms: int = 100,
        device: int | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.block_size = int(sample_rate * block_ms / 1000)
        self.device = device
        self._q: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=50)
        self._stream: sd.InputStream | None = None
        self._muted = threading.Event()

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if self._muted.is_set():
            return
        mono = np.asarray(indata[:, 0], dtype=np.float32).copy()
        try:
            self._q.put_nowait(mono)
        except queue.Full:
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(mono)
            except queue.Full:
                pass

    def start(self) -> None:
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self.block_size,
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass

    def mute(self, muted: bool = True) -> None:
        if muted:
            self._muted.set()
            # 丢弃已排队数据 — 否则 TTS 泄漏会留在管线里
            self.drain()
        else:
            self._muted.clear()

    def drain(self) -> int:
        """清空已排队 mic 块（TTS 后调用，避免积压误判为语音）。"""
        n = 0
        while True:
            try:
                self._q.get_nowait()
                n += 1
            except queue.Empty:
                break
        return n

    def chunks(self) -> Iterator[np.ndarray]:
        while True:
            item = self._q.get()
            if item is None:
                break
            yield item


class LoopbackStream:
    """经 soundcard 的 WASAPI 环回采集（系统 / 对端播放）。

    真实通话：将对端音频路由到环回设备，TTS 播到
    另一路输出（如 VB-CABLE）— 否则 TTS 会被再次采集。
    """

    def __init__(
        self,
        sample_rate: int = config.SAMPLE_RATE,
        block_ms: int = 100,
        device_name: str | None = None,
    ) -> None:
        try:
            import soundcard as sc
        except ImportError as exc:  # noqa: BLE001
            raise ImportError(
                "环回采集需要 soundcard：pip install soundcard"
            ) from exc
        self._sc = sc
        self.sample_rate = sample_rate
        self.block_size = int(sample_rate * block_ms / 1000)
        self.device_name = device_name or getattr(
            config, "LOOPBACK_DEVICE_NAME", ""
        ) or ""
        self._q: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=50)
        self._muted = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._mic = self._resolve_mic()

    def _resolve_mic(self):
        sc = self._sc
        name = (self.device_name or "").strip()
        if name:
            for m in sc.all_microphones(include_loopback=True):
                if getattr(m, "isloopback", False) and name in m.name:
                    return m
            raise RuntimeError(f"找不到环回设备含名 {name!r}；可选: {list_loopback_devices()}")
        spk = sc.default_speaker()
        # 优先当前默认扬声器的环回
        for m in sc.all_microphones(include_loopback=True):
            if getattr(m, "isloopback", False) and spk and spk.name in m.name:
                return m
        for m in sc.all_microphones(include_loopback=True):
            if getattr(m, "isloopback", False):
                return m
        raise RuntimeError("系统无可用 WASAPI loopback 设备")

    def start(self) -> None:
        self._stop.clear()
        print(f"[loopback] capture ← {self._mic.name}")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @staticmethod
    def _co_initialize() -> None:
        """工作线程上的 WASAPI/soundcard 需 COM 初始化（否则 0x800401f0）。"""
        try:
            import pythoncom  # type: ignore

            pythoncom.CoInitialize()
            return
        except Exception:
            pass
        try:
            import ctypes

            ctypes.windll.ole32.CoInitializeEx(None, 0x0)  # COINIT_MULTITHREADED
        except Exception:
            pass

    def _run(self) -> None:
        self._co_initialize()
        frames = self.block_size
        try:
            with self._mic.recorder(
                samplerate=self.sample_rate, channels=1, blocksize=frames
            ) as rec:
                while not self._stop.is_set():
                    data = rec.record(numframes=frames)
                    if self._muted.is_set():
                        continue
                    mono = np.asarray(data, dtype=np.float32).reshape(-1)
                    if mono.size == 0:
                        continue
                    try:
                        self._q.put_nowait(mono.copy())
                    except queue.Full:
                        try:
                            self._q.get_nowait()
                        except queue.Empty:
                            pass
                        try:
                            self._q.put_nowait(mono.copy())
                        except queue.Full:
                            pass
        except Exception as exc:  # noqa: BLE001
            print(f"[loopback error] {exc}")
        finally:
            try:
                self._q.put_nowait(None)
            except queue.Full:
                pass

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass

    def mute(self, muted: bool = True) -> None:
        if muted:
            self._muted.set()
            self.drain()
        else:
            self._muted.clear()

    def drain(self) -> int:
        n = 0
        while True:
            try:
                self._q.get_nowait()
                n += 1
            except queue.Empty:
                break
        return n

    def chunks(self) -> Iterator[np.ndarray]:
        while True:
            item = self._q.get()
            if item is None:
                break
            yield item


def record_until_enter(sample_rate: int = config.SAMPLE_RATE) -> np.ndarray:
    """按住说话：直到用户按 Enter 结束录音。"""
    print("录音中… 说完后按 Enter 结束本句")
    q: queue.Queue[np.ndarray] = queue.Queue()

    def callback(indata, frames, time_info, status) -> None:  # noqa: ANN001
        q.put(np.asarray(indata[:, 0], dtype=np.float32).copy())

    chunks: list[np.ndarray] = []
    stop = threading.Event()

    def waiter() -> None:
        input()
        stop.set()

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    with sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        blocksize=int(sample_rate * 0.1),
        callback=callback,
    ):
        while not stop.is_set():
            try:
                chunks.append(q.get(timeout=0.1))
            except queue.Empty:
                continue
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks)
