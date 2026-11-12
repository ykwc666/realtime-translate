"""乐观异步 TTS，按 utterance/seq 失效。"""
from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from . import config
from .tts_play import PlayTiming, TtsPlayer, TtsResult


@dataclass
class TtsJob:
    text: str
    lang: str
    utt_id: int = 0
    seq: int = 0
    version: int = 0
    submit_t: float = 0.0


@dataclass
class TtsPlayEvent:
    """单条任务的播放结果；绝对时间用 perf_counter。"""

    utt_id: int
    seq: int
    version: int
    submit_t: float
    first_pcm_ready_t: float | None = None
    first_callback_t: float | None = None
    persistent: bool = False
    stream_reused: bool = False
    stream_open_ms: float = 0.0
    active_underruns: int = 0
    samples_expected: int = 0
    samples_written: int = 0
    interrupted: bool = False
    callback_valid: bool = False


@dataclass
class TtsQueueStats:
    jobs: int = 0
    dropped: int = 0
    synth_ms: list[float] = field(default_factory=list)


def _event_from_timing(
    job: TtsJob,
    timing: PlayTiming | None,
    *,
    first_pcm_ready_t: float | None,
    interrupted: bool,
) -> TtsPlayEvent:
    if timing is None:
        return TtsPlayEvent(
            utt_id=job.utt_id,
            seq=job.seq,
            version=job.version,
            submit_t=job.submit_t,
            first_pcm_ready_t=first_pcm_ready_t,
            interrupted=interrupted,
        )
    return TtsPlayEvent(
        utt_id=job.utt_id,
        seq=job.seq,
        version=job.version,
        submit_t=job.submit_t,
        first_pcm_ready_t=timing.first_pcm_ready_t or first_pcm_ready_t,
        first_callback_t=timing.first_callback_t if timing.callback_valid else None,
        persistent=bool(timing.persistent),
        stream_reused=bool(timing.stream_reused),
        stream_open_ms=float(timing.stream_open_ms),
        active_underruns=int(timing.active_underruns),
        samples_expected=int(timing.samples_expected),
        samples_written=int(timing.samples_written),
        interrupted=bool(timing.interrupted or interrupted),
        callback_valid=bool(timing.callback_valid),
    )


class TtsQueue:
    """入队可播报单元；按 (utt_id, seq, version) 丢弃过期任务。"""

    def __init__(
        self,
        tts: TtsPlayer | None,
        mute_cb: Callable[[bool], None] | None = None,
        enabled: bool = True,
        on_play_event: Callable[[TtsPlayEvent], None] | None = None,
    ) -> None:
        self.tts = tts
        self.mute_cb = mute_cb
        self.enabled = enabled and tts is not None
        self.on_play_event = on_play_event
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

    def set_on_play_event(
        self, cb: Callable[[TtsPlayEvent], None] | None
    ) -> None:
        self.on_play_event = cb

    def invalidate(
        self, utt_id: int, min_seq: int, min_version: int | None = None
    ) -> None:
        """丢弃本 utt 队列中 seq/version 低于阈值的未播任务。

        同时中止进行中的硬件播放，避免 barge-in / 最终改写
        在下一轮仍继续播报。
        """
        with self._lock:
            prev = self._min_seq.get(utt_id, 0)
            self._min_seq[utt_id] = max(prev, min_seq)
            if min_version is not None:
                prev_v = self._min_version.get(utt_id, 0)
                self._min_version[utt_id] = max(prev_v, min_version)
        stop = getattr(self.tts, "stop", None)
        if callable(stop):
            stop()

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
                text=text,
                lang=lang,
                utt_id=utt_id,
                seq=seq,
                version=version,
                submit_t=time.perf_counter(),
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

    def _emit(self, ev: TtsPlayEvent) -> None:
        cb = self.on_play_event
        if cb is None:
            return
        try:
            cb(ev)
        except Exception as exc:  # noqa: BLE001
            print(f"[TTS-q event error] {exc}")

    def _loop(self) -> None:
        assert self.tts is not None
        # 预取：队列有下一任务时，synth(N+1) 与 play(N) 重叠。
        pref_job: TtsJob | None = None
        pref_syn: TtsResult | None = None
        while True:
            item = pref_job if pref_job is not None else self._q.get()
            pref_job = None
            syn_ready = pref_syn
            pref_syn = None
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
            mute_mic = bool(getattr(config, "TTS_MUTE_MIC", False)) and (
                self.mute_cb is not None
            )
            if mute_mic:
                self.mute_cb(True)
            interrupted = False
            try:
                if not self._alive(item):
                    self.stats.dropped += 1
                else:
                    nxt: TtsJob | None = None
                    try:
                        nxt = self._q.get_nowait()
                    except queue.Empty:
                        nxt = None
                    pref_thread: threading.Thread | None = None
                    pref_box: list = [None]
                    if nxt is not None:
                        pref_job = nxt
                        if self._alive(nxt):

                            def _pref() -> None:
                                try:
                                    pref_box[0] = self.tts.synthesize(
                                        nxt.text, nxt.lang, save_wav=False
                                    )
                                except Exception as exc:  # noqa: BLE001
                                    print(f"[TTS-q prefetch error] {exc}")

                            pref_thread = threading.Thread(
                                target=_pref, daemon=True
                            )
                            pref_thread.start()
                    play_sec = 0.0
                    first_pcm_t: float | None = None
                    if syn_ready is not None:
                        # 已预取整段音频 → 仅可中断播放
                        first_pcm_t = getattr(syn_ready, "first_pcm_ready_t", None)
                        speak_t0 = time.perf_counter()
                        if syn_ready.audio is not None and syn_ready.audio.size:
                            play_sec = self.tts.play_array(
                                syn_ready.audio,
                                syn_ready.sample_rate,
                                speak_t0=speak_t0,
                                first_pcm_ready_t=first_pcm_t,
                            )
                        elif syn_ready.path and syn_ready.path.exists():
                            play_sec = self.tts.play_file(syn_ready.path)
                    else:
                        syn_ready, play_sec = self.tts.speak(
                            item.text, item.lang, save_wav=False
                        )
                        first_pcm_t = getattr(syn_ready, "first_pcm_ready_t", None)
                    if pref_thread is not None:
                        pref_thread.join(timeout=30)
                        pref_syn = pref_box[0]
                    interrupted = not self._alive(item)
                    timing = getattr(self.tts, "last_play_timing", None)
                    if syn_ready is not None and getattr(syn_ready, "play_timing", None):
                        timing = syn_ready.play_timing or timing
                    self._emit(
                        _event_from_timing(
                            item,
                            timing,
                            first_pcm_ready_t=first_pcm_t,
                            interrupted=interrupted and play_sec <= 0.05,
                        )
                    )
                    if interrupted and play_sec <= 0.05:
                        self.stats.dropped += 1
                    else:
                        self.stats.jobs += 1
                        self.stats.synth_ms.append(syn_ready.ms if syn_ready else 0.0)
                        ttfa = (
                            getattr(syn_ready, "ttfa_ms", 0.0) if syn_ready else 0.0
                        ) or 0.0
                        print(
                            f"[TTS-q synth={getattr(syn_ready, 'ms', 0):.0f}ms "
                            f"ttfa={ttfa:.0f}ms "
                            f"utt={item.utt_id}#{item.seq}v{item.version}] {item.text}"
                        )
            except Exception as exc:  # noqa: BLE001
                print(f"[TTS-q error] {exc}")
                self._emit(
                    _event_from_timing(
                        item, None, first_pcm_ready_t=None, interrupted=True
                    )
                )
            finally:
                if mute_mic:
                    if not bool(getattr(config, "FINALIZE_WAIT_TTS", True)):
                        pad = float(
                            getattr(config, "PLAYBACK_MUTE_PAD_SEC", 0.0) or 0.0
                        )
                        if pad > 0:
                            time.sleep(pad)
                    self.mute_cb(False)
                with self._lock:
                    self._pending = max(0, self._pending - 1)
                    if self._pending == 0 and self._q.empty() and pref_job is None:
                        self._idle.set()
