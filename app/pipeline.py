from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np

from . import config
from .asr_stream import StreamingAsr
from .audio_io import LoopbackStream, MicStream, list_loopback_devices, load_wav, record_until_enter
from .dual_track import DualTrackEngine
from .translate import Translator
from .tts_play import TtsPlayer
from .vad_stream import segment_audio


@dataclass
class TurnResult:
    source: str
    translated: str
    src_lang: str
    tgt_lang: str
    asr_ms: float
    mt_ms: float
    tts_ms: float
    e2e_ms: float


def _post_utt_echo_pad(mute_fn, *drain_fns) -> None:  # noqa: ANN001
    """After an utterance: optional echo mute pad on the audio thread.

    When FINALIZE_WAIT_TTS is False, pad already runs inside tts_queue (TTS
    thread) so the capture loop must not sleep here.
    """
    if not bool(getattr(config, "FINALIZE_WAIT_TTS", True)):
        return
    pad = float(getattr(config, "PLAYBACK_MUTE_PAD_SEC", 0.0) or 0.0)
    if pad <= 0 or mute_fn is None:
        return
    mute_fn(True)
    for d in drain_fns:
        if callable(d):
            d()
    time.sleep(pad)
    for d in drain_fns:
        if callable(d):
            d()
    mute_fn(False)


class TranslatePipeline:
    def __init__(self, enable_tts: bool = True, harvest=None) -> None:  # noqa: ANN001
        print(
            f"加载模型中（CPU）ASR={config.ASR_BACKEND} MT={config.MT_BACKEND} …"
        )
        t0 = time.perf_counter()
        self.asr = StreamingAsr()
        self.translator = Translator()
        self.tts = TtsPlayer() if enable_tts else None
        # Optional HarvestWriter (side-channel only; see app/harvest.py)
        self.harvest = harvest
        if self.tts:
            if self.tts.output_device is not None:
                print(f"TTS 输出设备 index={self.tts.output_device}")
            else:
                print("TTS 输出设备=系统默认（可用 TTS_OUTPUT_DEVICE_NAME 与环回分离）")
            print("TTS 预热（中英）…")
            costs = self.tts.warmup()
            print(
                f"TTS 预热完成 zh={costs.get('zh', 0):.0f}ms en={costs.get('en', 0):.0f}ms"
            )
        # ASR/MT tiny warmup（避开 TM 命中句，确保 CT2 两边都热）
        _ = self.translator.translate("你好，世界")
        _ = self.translator.translate("hello world")
        if bool(getattr(config, "ASR_PUNCT_DISPLAY", False)):
            from .punct import add_punctuation, punct_available

            if punct_available():
                add_punctuation("你好")  # lazy-load OfflinePunctuation
        print(f"模型就绪，用时 {(time.perf_counter() - t0):.1f}s")
        print("优化记录见 docs/OPTIMIZATION_LOG.md ；测速: python scripts/bench.py --tag ...")

    def process_audio(self, audio: np.ndarray, play: bool = True) -> TurnResult | None:
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size < int(config.MIN_SPEECH_SEC * config.SAMPLE_RATE):
            print("（太短，已忽略）")
            return None

        t0 = time.perf_counter()
        asr = self.asr.transcribe(audio)
        if not asr.text:
            print("（未识别到有效文本）")
            return None
        print(f"[ASR {asr.ms:.0f}ms] {asr.text}")

        mt = self.translator.translate(asr.text)
        print(f"[MT  {mt.ms:.0f}ms] {mt.src_lang}->{mt.tgt_lang}: {mt.text}")

        tts_ms = 0.0
        if play and self.tts and mt.text:
            syn, _dur = self.tts.speak(mt.text, mt.tgt_lang, save_wav=False)
            tts_ms = syn.ms
            print(f"[TTS {tts_ms:.0f}ms] voice={syn.voice}")

        e2e = (time.perf_counter() - t0) * 1000
        print(f"[E2E {e2e:.0f}ms]")
        return TurnResult(
            source=asr.text,
            translated=mt.text,
            src_lang=mt.src_lang,
            tgt_lang=mt.tgt_lang,
            asr_ms=asr.ms,
            mt_ms=mt.ms,
            tts_ms=tts_ms,
            e2e_ms=e2e,
        )

    def run_file(self, path: str, play: bool = True) -> list[TurnResult]:
        audio = load_wav(path)
        segs = segment_audio(audio)
        segs_audio = [s.audio for s in segs] if segs else [audio]
        results: list[TurnResult] = []
        for i, seg in enumerate(segs_audio, 1):
            print(f"\n--- 片段 {i}/{len(segs_audio)} ---")
            r = self.process_audio(seg, play=play)
            if r:
                results.append(r)
        return results

    def run_ptt(self) -> None:
        print("半双工 PTT：每轮录音，说完按 Enter；Ctrl+C 退出")
        while True:
            try:
                audio = record_until_enter()
            except KeyboardInterrupt:
                print("\n退出")
                break
            print("\n--- 处理本句 ---")
            self.process_audio(audio, play=True)

    def run_vad(self) -> None:
        print(
            f"双轨实机 VAD：partial={config.PARTIAL_INTERVAL_SEC}s，"
            f"静音={config.VAD_SILENCE_SEC}s，句尾整句 MT（中途不译）；Ctrl+C 退出"
        )
        from .denoise import MicDenoiser

        mic = MicStream()
        denoise = MicDenoiser()
        engine = DualTrackEngine(
            self.asr,
            self.translator,
            self.tts,
            play=self.tts is not None,
            force_src_lang="zh",
            label="mic",
        )
        if self.harvest is not None:
            self.harvest.attach_engine(engine)
        mic.start()
        try:
            for chunk in mic.chunks():
                chunk = denoise.process(chunk)
                if chunk.size == 0:
                    continue
                finished = engine.feed_chunk(
                    chunk,
                    mute_cb=mic.mute,
                    allow_async_partial=True,
                )
                if finished is not None:
                    _post_utt_echo_pad(mic.mute, mic.drain)
        except KeyboardInterrupt:
            print("\n退出")
        finally:
            mic.stop()

    def run_loopback(self) -> None:
        """Peer→me only: WASAPI loopback (expect English) → zh TTS."""
        devices = list_loopback_devices()
        print(
            "环回对端→我：系统播放声 → ASR → en→zh → 中文播报；Ctrl+C 退出\n"
            f"  可选环回: {devices or ['(无)']}\n"
            "  注意：TTS 若与环回同设备会自听；请用 VB-CABLE 等把对端/TTS 分到不同设备。"
        )
        lb = LoopbackStream()
        # Own ASR instance so mic path can later run in parallel without racing streams
        asr_lb = StreamingAsr()
        engine = DualTrackEngine(
            asr_lb,
            self.translator,
            self.tts,
            play=self.tts is not None,
            force_src_lang="en",
            label="loopback",
        )
        if self.harvest is not None:
            self.harvest.attach_engine(engine)
        lb.start()
        try:
            for chunk in lb.chunks():
                finished = engine.feed_chunk(
                    chunk,
                    mute_cb=lb.mute if config.LOOPBACK_MUTE_ON_TTS else None,
                    allow_async_partial=True,
                )
                if finished is not None and config.LOOPBACK_MUTE_ON_TTS:
                    _post_utt_echo_pad(lb.mute, lb.drain)
        except KeyboardInterrupt:
            print("\n退出")
        finally:
            lb.stop()

    def run_duplex(self) -> None:
        """Mic (zh→en) + loopback (en→zh) in parallel threads."""
        devices = list_loopback_devices()
        print(
            "双通道：麦 zh→en + 环回 en→zh（各一路 final_only）；Ctrl+C 退出\n"
            f"  环回设备候选: {devices or ['(无)']}\n"
            "  强烈建议：对端声音与本机 TTS 输出分设备，否则环回会吃到自己的翻译。"
        )
        from .denoise import MicDenoiser

        mic = MicStream()
        lb = LoopbackStream()
        asr_lb = StreamingAsr()
        denoise = MicDenoiser()
        eng_mic = DualTrackEngine(
            self.asr,
            self.translator,
            self.tts,
            play=self.tts is not None,
            force_src_lang="zh",
            label="mic",
        )
        eng_lb = DualTrackEngine(
            asr_lb,
            self.translator,
            self.tts,
            play=self.tts is not None,
            force_src_lang="en",
            label="loopback",
        )
        if self.harvest is not None:
            self.harvest.attach_engine(eng_mic)
            self.harvest.attach_engine(eng_lb)
        stop = threading.Event()

        def _mute_both(muted: bool) -> None:
            mic.mute(muted)
            if config.LOOPBACK_MUTE_ON_TTS:
                lb.mute(muted)

        def _run_mic() -> None:
            try:
                for chunk in mic.chunks():
                    if stop.is_set():
                        break
                    chunk = denoise.process(chunk)
                    if chunk.size == 0:
                        continue
                    finished = eng_mic.feed_chunk(
                        chunk, mute_cb=_mute_both, allow_async_partial=True
                    )
                    if finished is not None:
                        _post_utt_echo_pad(
                            _mute_both, mic.drain, lb.drain
                        )
            except Exception as exc:  # noqa: BLE001
                print(f"[mic thread] {exc}")

        def _run_lb() -> None:
            try:
                for chunk in lb.chunks():
                    if stop.is_set():
                        break
                    finished = eng_lb.feed_chunk(
                        chunk,
                        mute_cb=_mute_both if config.LOOPBACK_MUTE_ON_TTS else None,
                        allow_async_partial=True,
                    )
                    if finished is not None and config.LOOPBACK_MUTE_ON_TTS:
                        _post_utt_echo_pad(
                            _mute_both, mic.drain, lb.drain
                        )
            except Exception as exc:  # noqa: BLE001
                print(f"[loopback thread] {exc}")

        mic.start()
        lb.start()
        t_mic = threading.Thread(target=_run_mic, daemon=True)
        t_lb = threading.Thread(target=_run_lb, daemon=True)
        t_mic.start()
        t_lb.start()
        try:
            while t_mic.is_alive() or t_lb.is_alive():
                t_mic.join(timeout=0.5)
                t_lb.join(timeout=0.5)
        except KeyboardInterrupt:
            print("\n退出")
        finally:
            stop.set()
            mic.stop()
            lb.stop()
            t_mic.join(timeout=2.0)
            t_lb.join(timeout=2.0)

    def run_dualsim(self, path: str, play: bool = False) -> list:
        """Offline dual-track simulation by chunking a wav (no mic)."""
        audio = load_wav(path)
        block = int(config.SAMPLE_RATE * 0.1)
        chunks = [
            audio[i : i + block] for i in range(0, len(audio), block) if audio[i : i + block].size
        ]
        print(
            f"双轨仿真：{path}，{len(chunks)} 个 100ms 块，"
            f"partial={config.PARTIAL_INTERVAL_SEC}s play={play}"
        )
        engine = DualTrackEngine(self.asr, self.translator, self.tts, play=play)
        return engine.run_on_chunks(iter(chunks), mute_cb=None, async_partial=False)
