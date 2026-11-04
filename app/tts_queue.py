"""Optimistic async TTS with utterance/seq invalidation."""
from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from . import config
from .tts_play import TtsPlayer


@dataclass
class TtsJob:
    text: str
    lang: str
    utt_id: int = 0
    seq: int = 0
    version: int = 0


@dataclass
class TtsQueueStats:
    jobs: int = 0
    dropped: int = 0
    synth_ms: list[float] = field(default_factory=list)


class TtsQueue:
    """Enqueue speakable units; drop stale by (utt_id, seq, version)."""

    def __init__(
        self,
        tts: TtsPlayer | None,
        mute_cb: Callable[[bool], None] | None = None,
        enabled: bool = True,
    ) -> None:
        self.tts = tts
        self.mute_cb = mute_cb
        self.enabled = enabled and tts is not None
        self.stats = TtsQueueStats()
        self._q: queue.Queue[TtsJob | None] = queue.Queue()
        self._idle = threading.Event()
        self._idle.set()
        self._pending = 0
        self._lock = threading.Lock()
        self._min_seq: dict[int, int] = {}
        self._min_version: dict[int, int] = {}
        self._thread: threading.Thread | None = None
        if self.enabled:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def set_mute_cb(self, mute_cb: Callable[[bool], None] | None) -> None:
        self.mute_cb = mute_cb

    def invalidate(
        self, utt_id: int, min_seq: int, min_version: int | None = None
    ) -> None:
        """Drop queued/unplayed jobs below min_seq / min_version for this utt."""
        with self._lock:
            prev = self._min_seq.get(utt_id, 0)
            self._min_seq[utt_id] = max(prev, min_seq)
            if min_version is not None:
                prev_v = self._min_version.get(utt_id, 0)
                self._min_version[utt_id] = max(prev_v, min_version)

    def submit(
        self,
        text: str,
        lang: str,
        utt_id: int = 0,
        seq: int = 0,
        version: int = 0,
    ) -> None:
        text = (text or "").strip()
        if not text:
            return
        if not self.enabled:
            return
        with self._lock:
            if seq < self._min_seq.get(utt_id, 0):
                self.stats.dropped += 1
                return
            if version < self._min_version.get(utt_id, 0):
                self.stats.dropped += 1
                return
            self._pending += 1
            self._idle.clear()
        self._q.put(
            TtsJob(
                text=text, lang=lang, utt_id=utt_id, seq=seq, version=version
            )
        )

    def wait_idle(self, timeout: float = 30.0) -> None:
        self._idle.wait(timeout=timeout)

    def close(self) -> None:
        if not self.enabled:
            return
        self._q.put(None)
        if self._thread:
            self._thread.join(timeout=5)

    def _alive(self, job: TtsJob) -> bool:
        with self._lock:
            if job.seq < self._min_seq.get(job.utt_id, 0):
                return False
            if job.version < self._min_version.get(job.utt_id, 0):
                return False
            return True


    def _loop(self) -> None:
        assert self.tts is not None
        while True:
            item = self._q.get()
            if item is None:
                self._idle.set()
                break
            if not self._alive(item):
                self.stats.dropped += 1
                with self._lock:
                    self._pending = max(0, self._pending - 1)
                    if self._pending == 0 and self._q.empty():
                        self._idle.set()
                continue
            if self.mute_cb:
                self.mute_cb(True)
            played = False
            try:
                # re-check after mute: final may have invalidated
                if not self._alive(item):
                    self.stats.dropped += 1
                else:
                    syn, _play_sec = self.tts.speak(
                        item.text, item.lang, save_wav=False
                    )
                    played = True
                    self.stats.jobs += 1
                    self.stats.synth_ms.append(syn.ms)
                    print(
                        f"[TTS-q {syn.ms:.0f}ms utt={item.utt_id}#{item.seq}"
                        f"v{item.version}] {item.text}"
                    )
            except Exception as exc:  # noqa: BLE001
                print(f"[TTS-q error] {exc}")
            finally:
                # Mute pad lives in pipeline.run_vad (once). Here only unmute after play.
                if self.mute_cb:
                    self.mute_cb(False)
                with self._lock:
                    self._pending = max(0, self._pending - 1)
                    if self._pending == 0 and self._q.empty():
                        self._idle.set()
