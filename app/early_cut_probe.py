"""Read-only early-cut oracle helpers (no production cut decisions)."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

# Checkpoints (seconds of trailing silence) sampled while still waiting for 0.45 cut.
PROBE_CHECKPOINTS = (0.30, 0.35, 0.40, 0.45)


def normalize_asr_text(text: str) -> str:
    """Compare ASR src: drop whitespace/punct; keep CJK / letters / digits."""
    t = (text or "").strip()
    if not t:
        return ""
    # Keep unicode word chars (incl. CJK); drop other punctuation/symbols/space.
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
    """Oracle labels vs final 0.45 cut — analysis only, not a live gate."""

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
    """Judge merge/split/tail_loss/mismatch vs reference (0.45) src list."""
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
        # Fewer utts: possible merge if a cand covers concatenated refs
        out["merge"] = True
    if len(cand_n) > len(ref_n):
        out["split"] = True
    if len(cand_n) == len(ref_n):
        for a, b in zip(ref_n, cand_n, strict=True):
            if a != b:
                out["text_mismatch"] = True
                # tail_loss: cand is proper prefix of ref (lost ending)
                if a.startswith(b) and len(b) < len(a):
                    out["tail_loss"] = True
                if b.startswith(a) and len(a) < len(b):
                    # cand longer — not tail_loss; still mismatch
                    pass
    else:
        # Also flag tail_loss if any aligned prefix-shortening is obvious
        for a in ref_n:
            for b in cand_n:
                if a.startswith(b) and 0 < len(b) < len(a) and len(a) - len(b) <= 4:
                    out["tail_loss"] = True
    return out
