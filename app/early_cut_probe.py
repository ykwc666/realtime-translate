"""只读早切 oracle 辅助（不参与生产切分决策）。"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

# 仍在等待 0.45 切分时，对尾部静音时长（秒）采样的检查点。
PROBE_CHECKPOINTS = (0.30, 0.35, 0.40, 0.45)


def normalize_asr_text(text: str) -> str:
    """对比 ASR 原文：去空白/标点；保留 CJK / 字母 / 数字。"""
    t = (text or "").strip()
    if not t:
        return ""
    # 保留 unicode 词字符（含 CJK）；去掉其它标点/符号/空格。
    return re.sub(r"[^\w]+", "", t, flags=re.UNICODE)


@dataclass
class ProbeSnap:
    checkpoint_sec: float
    silence_sec: float
    audio_sec: float
    utt_id: int
    spec_exists: bool
    spec_done: bool
    spec_text: str = ""
    spec_ms: float | None = None
    partial_text: str = ""
    resumed_after: bool = False


@dataclass
class ProbeOracle:
    """相对最终 0.45 切的 oracle 标签 — 仅分析用，非在线门控。"""

    ready_at_350: bool = False
    same_text_at_350: bool = False
    resumed_before_450: bool = False
    safe_350: bool = False
    ready_at_400: bool = False
    same_text_at_400: bool = False
    resumed_before_450_from_400: bool = False
    safe_400: bool = False
    earliest_safe: float | None = None
    final_src: str = ""
    snaps: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def compute_oracle(
    snaps: dict[float, ProbeSnap],
    final_src: str,
    *,
    resume_after: set[float] | None = None,
) -> ProbeOracle:
    resume_after = resume_after or set()
    final_n = normalize_asr_text(final_src)
    out = ProbeOracle(final_src=final_src or "")

    def _at(cp: float) -> tuple[bool, bool, bool, bool]:
        snap = snaps.get(cp)
        if snap is None:
            return False, False, False, False
        ready = bool(snap.spec_done and (snap.spec_text or "").strip())
        same = bool(ready and normalize_asr_text(snap.spec_text) == final_n and final_n)
        resumed = cp in resume_after or bool(snap.resumed_after)
        safe = bool(ready and same and not resumed)
        return ready, same, resumed, safe

    r35, s35, u35, safe35 = _at(0.35)
    r40, s40, u40, safe40 = _at(0.40)
    out.ready_at_350 = r35
    out.same_text_at_350 = s35
    out.resumed_before_450 = u35
    out.safe_350 = safe35
    out.ready_at_400 = r40
    out.same_text_at_400 = s40
    out.resumed_before_450_from_400 = u40
    out.safe_400 = safe40

    earliest: float | None = None
    for cp in PROBE_CHECKPOINTS:
        _r, _s, _u, safe = _at(cp)
        if safe:
            earliest = cp
            break
    out.earliest_safe = earliest
    out.snaps = [
        asdict(snaps[cp]) for cp in PROBE_CHECKPOINTS if cp in snaps
    ]
    return out


def classify_utt_bucket(audio_sec: float, wav_name: str) -> str:
    breath = {
        "pause_xingqi_er.wav",
        "stop_zheshi_xingqi.wav",
        "cont_tuesday.wav",
    }
    if (wav_name or "") in breath:
        return "breath"
    if float(audio_sec or 0) >= 1.6:
        return "long"
    return "short"


def compare_segmentations(
    ref_srcs: list[str], cand_srcs: list[str]
) -> dict:
    """相对参考（0.45）原文列表，判定 merge/split/tail_loss/mismatch。"""
    ref_n = [normalize_asr_text(s) for s in ref_srcs]
    cand_n = [normalize_asr_text(s) for s in cand_srcs]
    out = {
        "ref_n": len(ref_srcs),
        "cand_n": len(cand_srcs),
        "count_delta": len(cand_srcs) - len(ref_srcs),
        "merge": False,
        "split": False,
        "tail_loss": False,
        "text_mismatch": False,
        "ref_srcs": list(ref_srcs),
        "cand_srcs": list(cand_srcs),
    }
    if len(cand_n) < len(ref_n):
        # 句数更少：若某 cand 覆盖拼接后的 ref，则可能 merge
        out["merge"] = True
    if len(cand_n) > len(ref_n):
        out["split"] = True
    if len(cand_n) == len(ref_n):
        for a, b in zip(ref_n, cand_n, strict=True):
            if a != b:
                out["text_mismatch"] = True
                # tail_loss：cand 为 ref 的真前缀（丢句尾）
                if a.startswith(b) and len(b) < len(a):
                    out["tail_loss"] = True
                if b.startswith(a) and len(a) < len(b):
                    # cand 更长 — 非 tail_loss；仍为 mismatch
                    pass
    else:
        # 若存在明显的前缀缩短，也标 tail_loss
        for a in ref_n:
            for b in cand_n:
                if a.startswith(b) and 0 < len(b) < len(a) and len(a) - len(b) <= 4:
                    out["tail_loss"] = True
    return out
