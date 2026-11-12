"""zh→en 时间捏数字：16点31分 → 16:31。"""

from __future__ import annotations

from app.mt_postedit import postedit_zh_en


def test_clock_glued_digits() -> None:
    out = postedit_zh_en("现在是16点31分。", "It's 1631.")
    assert "16:31" in out
    assert "1631" not in out.replace("16:31", "")


def test_clock_padded_minute() -> None:
    out = postedit_zh_en("现在是9点05分。", "It's 905.")
    assert "9:05" in out


def test_not_yidian_dou() -> None:
    # 「一点都不」无「分」，不得改写
    hyp = "I don't like it at all."
    assert postedit_zh_en("我一点都不喜欢。", hyp) == hyp
