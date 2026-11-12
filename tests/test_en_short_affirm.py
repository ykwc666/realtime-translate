"""en→zh 极短句：假名映射 key + TM 精确命中。"""

from __future__ import annotations

import re

from app.dual_track import _KANA_AFFIRM_TO_EN
from app.mt_tm import lookup_tm, normalize_tm_key, reload_tm


def test_kana_en_keys_are_depunc() -> None:
    # 「あ、イ」经 finalize content 规范化后应为 あイ
    raw = "あ、イ"
    content = re.sub(r"[^\w\u4e00-\u9fff]+", "", raw, flags=re.UNICODE)
    assert content == "あイ"
    assert _KANA_AFFIRM_TO_EN.get(content) == "Yes."
    assert _KANA_AFFIRM_TO_EN.get("うん") == "Yeah."


def test_en_short_tm_five() -> None:
    reload_tm()
    assert lookup_tm("Right.", "en") == "对。"
    assert lookup_tm("Right", "en") == "对。"
    assert lookup_tm("No.", "en") == "没有。"
    assert lookup_tm("Nice", "en") == "很好。"
    assert lookup_tm("Yeah.", "en") == "是啊。"
    assert lookup_tm("Exactly!", "en") == "没错。"
    # 长句不得因子串命中
    assert lookup_tm("Right, I'd forgotten this most important point", "en") is None
    assert normalize_tm_key("Right.", "en") == "right"
