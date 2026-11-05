"""A0：final 含中间句末时，MT/TTS 前拆成子句。"""
from __future__ import annotations

from types import MethodType, SimpleNamespace

from app.dual_track import DualTrackEngine, DualTrackStats
from app.retranscribe import split_sentences


def _engine_for_clauses() -> DualTrackEngine:
    e = DualTrackEngine.__new__(DualTrackEngine)
    e._final_mt_clauses = MethodType(DualTrackEngine._final_mt_clauses, e)
    return e


def test_split_sentences_double_q_weather_tuesday() -> None:
    parts = split_sentences("今天天气怎么样？？现在是星期二。")
    assert parts == ["今天天气怎么样？", "现在是星期二。"]


def test_final_mt_clauses_splits_on_mid_end() -> None:
    e = _engine_for_clauses()
    parts = e._final_mt_clauses("今天天气怎么样？现在是星期二。")
    assert len(parts) >= 2
    assert "天气" in parts[0]
    assert "星期二" in parts[1]


def test_final_mt_clauses_single_stays_whole() -> None:
    e = _engine_for_clauses()
    assert e._final_mt_clauses("现在是星期二。") == ["现在是星期二。"]
    assert e._final_mt_clauses("他现在没有回我的消息") == [
        "他现在没有回我的消息"
    ]


def test_final_mt_clauses_ignores_display_punct_need() -> None:
    """无原生句末时不拆（即使展示标点模型会补出双句末）。"""
    e = _engine_for_clauses()
    bare = "今天天气怎么样现在是星期二"
    assert e._final_mt_clauses(bare) == [bare]


def test_translate_final_sets_sub_lines_and_enqueues_list() -> None:
    """不加载真实 MT：桩 Translator，确认 sub_lines + 多段 enqueue。"""
    e = DualTrackEngine.__new__(DualTrackEngine)
    e._final_mt_clauses = MethodType(DualTrackEngine._final_mt_clauses, e)
    e._translate_final = MethodType(DualTrackEngine._translate_final, e)
    e._enqueue_speak = MethodType(DualTrackEngine._enqueue_speak, e)
    e._register_utt_stats = lambda *_a, **_k: None  # type: ignore[method-assign]
    e._forced_truncation = False
    e.force_src_lang = "zh"
    e.tgt_lang = "en"
    e.utt_t0 = None
    e._t_speech_end = 0.0
    e._utt_id = 1
    e._tts_seq = 0
    e._tts_version = 0
    e.mt_ms_sum = 0.0
    e.translated_bits = []
    e.stats = DualTrackStats()
    e.play = False
    e.tts = None
    submitted: list = []
    e.tts_q = SimpleNamespace(
        enabled=True,
        submit=lambda *a, **k: submitted.append((a, k)),
        set_mute_cb=lambda *_: None,
    )

    class _Mt:
        def __init__(self, text: str) -> None:
            self.text = text
            self.ms = 1.0
            self.tgt_lang = "en"
            self.mt_raw = text

    class _Tr:
        def translate(self, text: str, src_lang: str = "zh"):  # noqa: ARG002
            mapping = {
                "今天天气怎么样？": "How's the weather today?",
                "现在是星期二。": "It's Tuesday.",
            }
            return _Mt(mapping.get(text, f"T({text})"))

    e.translator = _Tr()
    e._translate_final("今天天气怎么样？现在是星期二。")
    assert e.stats.sub_lines is not None
    assert len(e.stats.sub_lines) == 2
    assert e.stats.sub_lines[0]["src"] == "今天天气怎么样？"
    assert e.stats.sub_lines[1]["tgt"] == "It's Tuesday."
    assert len(submitted) == 2
