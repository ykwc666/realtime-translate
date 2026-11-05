"""高频通话短语的精确匹配翻译记忆（TM）。

命中 → 跳过 CT2（≈0ms）。未命中 → 调用方走 MT + 后编辑。
键经规范化（大小写/标点/空白）；值为规范口语形式。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from . import config

_PUNCT_RE = re.compile(r"[。．\.！!？\?，,、；;：:\s\"'“”‘’…—\-–]+")


def normalize_tm_key(text: str, lang: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    t = _PUNCT_RE.sub("", t)
    if lang == "en":
        t = t.lower()
    return t


@lru_cache(maxsize=1)
def _load_maps() -> tuple[dict[str, str], dict[str, str]]:
    """返回 (zh_key→en, en_key→zh)。"""
    path = Path(getattr(config, "MT_TM_PATH", config.ROOT / "data" / "mt_tm.jsonl"))
    zh2en: dict[str, str] = {}
    en2zh: dict[str, str] = {}
    if not path.exists():
        return zh2en, en2zh
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            row = json.loads(line)
            direction = (row.get("dir") or "").strip().lower()
            src = (row.get("src") or "").strip()
            tgt = (row.get("tgt") or "").strip()
            if not src or not tgt or direction not in ("zh-en", "en-zh"):
                continue
            if direction == "zh-en":
                zh2en[normalize_tm_key(src, "zh")] = tgt
            else:
                en2zh[normalize_tm_key(src, "en")] = tgt
    return zh2en, en2zh


def lookup_tm(text: str, src_lang: str) -> str | None:
    """src_lang 为 {zh,en} 时的精确 TM 命中；未命中为 None。"""
    if not getattr(config, "MT_TM_ENABLED", True):
        return None
    key = normalize_tm_key(text, src_lang)
    if not key:
        return None
    zh2en, en2zh = _load_maps()
    if src_lang == "zh":
        return zh2en.get(key)
    if src_lang == "en":
        return en2zh.get(key)
    return None


def tm_size() -> tuple[int, int]:
    zh2en, en2zh = _load_maps()
    return len(zh2en), len(en2zh)


def reload_tm() -> None:
    _load_maps.cache_clear()
