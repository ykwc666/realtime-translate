"""标点吞尾回退守卫：纯字符串，不加载模型。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.dual_track import DualTrackEngine  # noqa: E402

# Test 027 真实文本（OBS_FIX detail）
_FULL_027 = "些葱，然后把这个嗯土豆丝儿放下去，要不断的翻炒。呃，放下去以后呢"
_PUNCT_027 = "些葱，然后把这个嗯土豆丝儿放下去。要不断的翻炒。"


def test_no_drop_keeps_puncted() -> None:
    full = "你好"
    puncted = "你好。"
    out, drop = DualTrackEngine.pick_display_src(full, puncted)
    assert drop == 0
    assert out == puncted


def test_drop_3_no_fallback() -> None:
    # 阈值边界：丢 3 字不回退
    full = "abcdefghij"  # 10
    puncted = "abcdefg"  # 7 → drop 3
    out, drop = DualTrackEngine.pick_display_src(full, puncted, min_drop=4)
    assert drop == 3
    assert out == puncted


def test_drop_7_fallback_027() -> None:
    out, drop = DualTrackEngine.pick_display_src(_FULL_027, _PUNCT_027)
    assert drop == 7
    assert out == _FULL_027


def test_empty_puncted_fallback() -> None:
    full = "你好世界"
    out, drop = DualTrackEngine.pick_display_src(full, "")
    assert out == full
    assert drop == DualTrackEngine.display_drop_chars(full, "")
