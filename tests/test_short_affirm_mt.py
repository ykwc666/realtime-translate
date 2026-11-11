"""短回应应放行 MT；新疆乱码应被 scrub。"""

from __future__ import annotations

from app.mt_postedit import postedit_zh_en


def test_postedit_short_affirm() -> None:
    assert postedit_zh_en("对", "Yes.") == "Right."
    assert postedit_zh_en("嗯", "Um.") == "Yeah."
    assert "how are you" in postedit_zh_en("嗨你好", "Hi there.").lower()


def test_scrub_xinjiang_unk() -> None:
    out = postedit_zh_en("我在新疆大概。", "I'm at  ⁇ injiang, probably.")
    assert "Xinjiang" in out
    assert " ⁇ " not in out


def test_scrub_rihuo_day() -> None:
    out = postedit_zh_en(
        "我不是很爱买日货。",
        "I don't like buying day or Japanese makeup.",
    )
    assert "Japanese" in out
    assert "buying day" not in out.lower()


def test_scrub_rihuo_multiclause() -> None:
    """多子句也要 scrub（曾被 clause_sep early-return 跳过）。"""
    out = postedit_zh_en(
        "好吧。我不是很爱买日货。就是日本的化妆品，说实话。",
        "Well. I don't like buying day or Japanese makeup. It's cosmetics.",
    )
    assert "buying day" not in out.lower()
    assert "products" in out.lower() or "makeup" in out.lower()
    assert "honest" in out.lower()


def test_scrub_mairi_asr_trunc() -> None:
    out = postedit_zh_en(
        "好吧，我不是很爱买日，就是日本的化妆品。说实话。",
        "Well, I don't really like buying daytimes, but Japanese cosmetics.",
    )
    assert "daytime" not in out.lower()
    assert "Japanese" in out
    assert "honest" in out.lower()


def test_scrub_waimai_zhai() -> None:
    out = postedit_zh_en(
        "外卖系统，有的时候会很宅，想要出去吃饭。",
        "delivery systems, it'll be very homey, Wanting to go out for dinner?",
    )
    assert "takeaway" in out.lower()
    assert "homey" not in out.lower()
    assert "going out to eat" in out.lower()


def test_scrub_zhengge_dai() -> None:
    out = postedit_zh_en(
        "你说的是你整个带。",
        "You said the whole tape.",
    )
    assert "tape" not in out.lower()
    assert "big" in out.lower()
