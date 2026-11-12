"""asr_err_core 诊断口径：纯字符串，不加载模型。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.eval_dialogue_mslt import asr_err_core, asr_error  # noqa: E402


@pytest.mark.parametrize(
    "ref,hyp,want",
    [
        ("你有。", "你你你有。", 0.0),
        ("好的。拜拜。", "嗯，好的，嗯，拜拜。", 0.0),
        ("对。我两个都还蛮喜欢。", "对对，我。就就两个都都还蛮喜欢。", 0.0),
        ("主要是纠正他们。", "全儿全而纠正他们。", 0.429),
        ("其实。", "Hereうん", 1.0),
        ("对了。份子钱那", "哎，对了，嗯嗯呃，份子钱拿。", 0.167),
        ("", "", 0.0),
    ],
)
def test_asr_err_core(ref: str, hyp: str, want: float) -> None:
    assert asr_err_core("zh_en", ref, hyp) == pytest.approx(want, abs=0.002)


def test_asr_err_unchanged() -> None:
    # 旧口径必须保持字级编辑距离，防止有人「顺手统一」
    assert asr_error("zh_en", "你有。", "你你你有。") == 1.0


def test_asr_err_core_rejects_en_zh() -> None:
    with pytest.raises(ValueError):
        asr_err_core("en_zh", "hello", "hello")
