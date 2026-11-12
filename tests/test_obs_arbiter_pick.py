"""观测字段：arbiter_pick 规范化比对（不加载模型）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.dual_track import DualTrackEngine  # noqa: E402


def test_pick_offline_despite_tail_punct() -> None:
    # m 去标点后等于 offline；旧逻辑会因句号落到 merge
    assert (
        DualTrackEngine.classify_arbiter_pick(
            "有什么舞蹈呢拉丁舞啊",
            "有什么舞蹈的？拉丁舞啊。",
            "有什么舞蹈的？拉丁舞啊？",
        )
        == "offline"
    )


def test_pick_offline_when_same_content_online_unpunct() -> None:
    # 实际选了 offline，内容与 online 相同仅差句号 → 应标 offline（先比 off）
    assert (
        DualTrackEngine.classify_arbiter_pick(
            "可找一些不一样的活动",
            "可找一些不一样的活动。",
            "可找一些不一样的活动。",
        )
        == "offline"
    )


def test_pick_online_when_final_matches_online_only() -> None:
    assert (
        DualTrackEngine.classify_arbiter_pick(
            "你你你有",
            "你你你。",
            "你你你有。",
        )
        == "online"
    )


def test_pick_merge_when_neither() -> None:
    # 假名映射 / 标点吞尾 / partial 补全 → merge
    assert (
        DualTrackEngine.classify_arbiter_pick(
            "うん",
            "うん。",
            "Yeah.",
        )
        == "merge"
    )


def test_norm_cmp_strips_punct() -> None:
    assert DualTrackEngine._norm_asr_obs_cmp("你好。") == DualTrackEngine._norm_asr_obs_cmp(
        "你好？"
    )
