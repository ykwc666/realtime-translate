from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass

from . import config
from .mt_postedit import postedit_zh_en


@dataclass
class TranslateResult:
    src_lang: str
    tgt_lang: str
    text: str
    ms: float


def detect_lang(text: str) -> str:
    """Rough ZH/EN detect for bilingual call use."""
    t = text.strip()
    if not t:
        return "zh"
    latin = len(re.findall(r"[A-Za-z]", t))
    cjk = len(re.findall(r"[\u4e00-\u9fff]", t))
    if latin >= cjk * 2 and latin >= 2:
        return "en"
    return "zh"


class Translator:
    def __init__(self) -> None:
        os.environ.setdefault("OMP_NUM_THREADS", str(config.MT_THREADS))
        self.backend = config.MT_BACKEND
        if self.backend == "ct2":
            self._init_ct2()
        else:
            self._init_transformers()

    def _init_transformers(self) -> None:
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        self._tok_zh_en = AutoTokenizer.from_pretrained(str(config.OPUS_ZH_EN))
        self._mt_zh_en = AutoModelForSeq2SeqLM.from_pretrained(str(config.OPUS_ZH_EN))
        self._tok_en_zh = AutoTokenizer.from_pretrained(str(config.OPUS_EN_ZH))
        self._mt_en_zh = AutoModelForSeq2SeqLM.from_pretrained(str(config.OPUS_EN_ZH))
        self._mt_zh_en.eval()
        self._mt_en_zh.eval()

    def _init_ct2(self) -> None:
        import ctranslate2
        from transformers import AutoTokenizer

        if not config.OPUS_ZH_EN_CT2.exists() or not config.OPUS_EN_ZH_CT2.exists():
            raise FileNotFoundError(
                "缺少 CT2 模型，请先运行: python scripts/convert_opus_ct2.py"
            )
        self._tok_zh_en = AutoTokenizer.from_pretrained(str(config.OPUS_ZH_EN))
        self._tok_en_zh = AutoTokenizer.from_pretrained(str(config.OPUS_EN_ZH))
        threads = max(1, int(config.MT_THREADS))
        self._mt_zh_en = ctranslate2.Translator(
            str(config.OPUS_ZH_EN_CT2),
            device="cpu",
            compute_type="int8",
            inter_threads=1,
            intra_threads=threads,
        )
        self._mt_en_zh = ctranslate2.Translator(
            str(config.OPUS_EN_ZH_CT2),
            device="cpu",
            compute_type="int8",
            inter_threads=1,
            intra_threads=threads,
        )

    def translate(self, text: str, src_lang: str | None = None) -> TranslateResult:
        text = (text or "").strip()
        if not text:
            return TranslateResult("zh", "en", "", 0.0)

        src = src_lang or detect_lang(text)
        tgt = "en" if src == "zh" else "zh"
        t0 = time.perf_counter()
        if self.backend == "ct2":
            translated = self._translate_ct2(text, src)
        else:
            translated = self._translate_transformers(text, src)
        if src == "zh" and tgt == "en":
            translated = postedit_zh_en(text, translated)
        ms = (time.perf_counter() - t0) * 1000
        return TranslateResult(src, tgt, translated, ms)

    def _translate_transformers(self, text: str, src: str) -> str:
        tok = self._tok_zh_en if src == "zh" else self._tok_en_zh
        mt = self._mt_zh_en if src == "zh" else self._mt_en_zh
        batch = tok(text, return_tensors="pt", truncation=True, max_length=256)
        gen_cfg = getattr(mt, "generation_config", None)
        if gen_cfg is not None and getattr(gen_cfg, "max_length", None) is not None:
            gen_cfg.max_length = None
        out = mt.generate(**batch, max_new_tokens=128)
        return tok.decode(out[0], skip_special_tokens=True).strip()

    def _translate_ct2(self, text: str, src: str) -> str:
        tok = self._tok_zh_en if src == "zh" else self._tok_en_zh
        mt = self._mt_zh_en if src == "zh" else self._mt_en_zh
        ids = tok.encode(text, truncation=True, max_length=256)
        tokens = tok.convert_ids_to_tokens(ids)
        results = mt.translate_batch(
            [tokens],
            beam_size=1,
            max_decoding_length=128,
        )
        out_tokens = results[0].hypotheses[0]
        return tok.decode(tok.convert_tokens_to_ids(out_tokens), skip_special_tokens=True).strip()
