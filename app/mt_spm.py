"""用 sentencepiece 直接加载 Marian 的 source.spm / target.spm，
替代 transformers.AutoTokenizer，避免 ~10s 的 transformers 导入。"""

from __future__ import annotations

from pathlib import Path

import sentencepiece as spm


class SpmTokenizer:
    def __init__(self, source_spm: Path, target_spm: Path) -> None:
        self._src = spm.SentencePieceProcessor()
        self._tgt = spm.SentencePieceProcessor()
        if not self._src.Load(str(source_spm)):
            raise FileNotFoundError(f"无法加载 source.spm: {source_spm}")
        if not self._tgt.Load(str(target_spm)):
            raise FileNotFoundError(f"无法加载 target.spm: {target_spm}")
        self._eos_id = int(self._src.eos_id())
        self._unk_id = int(self._tgt.unk_id())

    def encode(
        self,
        text: str,
        *,
        truncation: bool = False,
        max_length: int | None = None,
    ) -> list[int]:
        pieces = self._src.encode(text or "", out_type=str)
        ids = [int(self._src.piece_to_id(p)) for p in pieces]
        if truncation and max_length is not None:
            # 先截到 max_length-1，再补 </s>（总数 ≤ max_length）
            limit = max(0, int(max_length) - 1)
            ids = ids[:limit]
        ids.append(self._eos_id)
        return ids

    def convert_ids_to_tokens(self, ids: list[int]) -> list[str]:
        return [self._src.id_to_piece(int(i)) for i in ids]

    def convert_tokens_to_ids(self, tokens: list[str]) -> list[int]:
        out: list[int] = []
        for tok in tokens:
            pid = int(self._tgt.piece_to_id(tok))
            if pid < 0:
                out.append(self._unk_id)
            else:
                out.append(pid)
        return out

    def decode(self, ids: list[int], *, skip_special_tokens: bool = True) -> str:
        vals = [int(i) for i in ids]
        if skip_special_tokens:
            vals = [i for i in vals if not self._tgt.is_control(i)]
        return self._tgt.decode(vals)
