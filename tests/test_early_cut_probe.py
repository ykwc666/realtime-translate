"""Unit tests for early-cut probe oracle (no real audio)."""
from __future__ import annotations

from app.early_cut_probe import (
    ProbeSnap,
    compare_segmentations,
    compute_oracle,
    normalize_asr_text,
)


def test_normalize_keeps_cjk_drops_punct():
    assert normalize_asr_text(" 现在是星期二。 ") == "现在是星期二"
    assert normalize_asr_text("How's the weather?") == "Howstheweather"


def test_oracle_safe_350():
    snaps = {
        0.35: ProbeSnap(
            checkpoint_sec=0.35,
            silence_sec=0.35,
            audio_sec=1.2,
            utt_id=1,
            spec_exists=True,
            spec_done=True,
            spec_text="今天天气怎么样",
        ),
        0.45: ProbeSnap(
            checkpoint_sec=0.45,
            silence_sec=0.45,
            audio_sec=1.2,
            utt_id=1,
            spec_exists=True,
            spec_done=True,
            spec_text="今天天气怎么样",
        ),
    }
    o = compute_oracle(snaps, "今天天气怎么样", resume_after=set())
    assert o.ready_at_350 and o.same_text_at_350 and o.safe_350
    assert o.earliest_safe == 0.35


def test_oracle_resumed_blocks_safe():
    snaps = {
        0.35: ProbeSnap(
            checkpoint_sec=0.35,
            silence_sec=0.35,
            audio_sec=1.2,
            utt_id=1,
            spec_exists=True,
            spec_done=True,
            spec_text="现在是星期",
            resumed_after=True,
        ),
    }
    o = compute_oracle(snaps, "现在是星期二", resume_after={0.35})
    assert o.ready_at_350
    assert not o.same_text_at_350  # 星期 != 星期二
    assert o.resumed_before_450
    assert not o.safe_350


def test_oracle_400_safe_when_350_not_ready():
    snaps = {
        0.35: ProbeSnap(
            checkpoint_sec=0.35,
            silence_sec=0.35,
            audio_sec=2.0,
            utt_id=1,
            spec_exists=True,
            spec_done=False,
            spec_text="",
        ),
        0.40: ProbeSnap(
            checkpoint_sec=0.40,
            silence_sec=0.40,
            audio_sec=2.0,
            utt_id=1,
            spec_exists=True,
            spec_done=True,
            spec_text="他现在没有回我的消息",
        ),
    }
    o = compute_oracle(snaps, "他现在没有回我的消息")
    assert not o.safe_350
    assert o.safe_400
    assert o.earliest_safe == 0.40


def test_compare_merge_split_tail():
    ref = ["你好", "延迟大概有多少"]
    merged = compare_segmentations(ref, ["你好延迟大概有多少"])
    assert merged["merge"] and merged["count_delta"] == -1
    split = compare_segmentations(["现在是星期二"], ["现在是", "星期二"])
    assert split["split"]
    tail = compare_segmentations(["延迟大概有多少"], ["延迟大概有多"])
    assert tail["tail_loss"] and tail["text_mismatch"]
