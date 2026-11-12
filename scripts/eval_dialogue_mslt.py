"""MSLT 双人对话 KPI 基线：ASR / 译文可用 / 半句碎切（方向分开报）。

用法：
  .venv\\Scripts\\python.exe scripts\\eval_dialogue_mslt.py
  .venv\\Scripts\\python.exe scripts\\eval_dialogue_mslt.py --manifest data\\dialogue_corpus_dev\\manifest.jsonl --out-dir data\\eval\\dialogue_mslt_dev
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402
from app.audio_io import load_wav  # noqa: E402
from app.dual_track import DualTrackEngine  # noqa: E402
from app.pipeline import TranslatePipeline  # noqa: E402

DEFAULT_MANIFEST = ROOT / "data" / "dialogue_corpus" / "manifest.jsonl"
DEFAULT_OUT_DIR = ROOT / "data" / "eval" / "dialogue_mslt"
SR = int(config.SAMPLE_RATE)
BLOCK = int(SR * 0.1)


def _norm_en_words(text: str) -> list[str]:
    t = (text or "").lower()
    t = re.sub(r"[^a-z0-9'\s]+", " ", t)
    return [w for w in t.split() if w]


def _norm_zh_chars(text: str) -> list[str]:
    t = re.sub(r"[^\w\u4e00-\u9fff]+", "", text or "", flags=re.UNICODE)
    return list(t.lower())


def _lev_rate(ref_toks: list[str], hyp_toks: list[str]) -> float:
    if not ref_toks:
        return 0.0 if not hyp_toks else 1.0
    n, m = len(ref_toks), len(hyp_toks)
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, m + 1):
            cur = dp[j]
            if ref_toks[i - 1] == hyp_toks[j - 1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j - 1])
            prev = cur
    # 短参考 + 超长识别时 raw rate 可 >>1；报表统一封顶 1.0
    return min(1.0, dp[m] / float(n))


def asr_error(direction: str, ref: str, hyp: str) -> float:
    if direction == "zh_en":
        return _lev_rate(_norm_zh_chars(ref), _norm_zh_chars(hyp))
    return _lev_rate(_norm_en_words(ref), _norm_en_words(hyp))


# 语气词：仅诊断口径 asr_err_core 使用；不进 KPI
FILLER_ZH = set("嗯呃啊哎哦唉呀诶额噢喔")


def _lcs_len(a: str, b: str) -> int:
    """最长公共子序列长度（字符级）。"""
    if not a or not b:
        return 0
    n, m = len(a), len(b)
    # 滚动一维 DP
    prev = [0] * (m + 1)
    for i in range(1, n + 1):
        cur = [0] * (m + 1)
        ai = a[i - 1]
        for j in range(1, m + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
            else:
                cur[j] = prev[j] if prev[j] >= cur[j - 1] else cur[j - 1]
        prev = cur
    return prev[m]


def _core_zh(text: str) -> str:
    """去标点 →（有剩余内容时）去语气词 → 连续重复折叠为 1。"""
    t = "".join(_norm_zh_chars(text))
    stripped = "".join(ch for ch in t if ch not in FILLER_ZH)
    if stripped:
        t = stripped
    return re.sub(r"(.)\1+", r"\1", t)


def asr_err_core(direction: str, ref: str, hyp: str) -> float:
    """zh_en 诊断口径：内容召回（LCS），不惩罚多字/重复/语气词。

    阈值仍用 0.35，与 KPI asr_err 并列；不进 summary。
    """
    if direction != "zh_en":
        raise ValueError("asr_err_core 本轮只处理 zh_en")
    r, h = _core_zh(ref), _core_zh(hyp)
    if not r:
        return 0.0 if not h else 1.0
    return round(1.0 - _lcs_len(r, h) / float(len(r)), 3)


def asr_coverage(direction: str, ref: str, hyp: str) -> float:
    if direction == "zh_en":
        r, h = _norm_zh_chars(ref), _norm_zh_chars(hyp)
    else:
        r, h = _norm_en_words(ref), _norm_en_words(hyp)
    if not r:
        return 1.0
    return min(1.0, len(h) / float(len(r)))


def mt_usable(direction: str, ref_tgt: str, hyp_tgt: str) -> tuple[str, str]:
    """粗闸门：ok / awkward / wrong。

    对话译文允许意译：英文词重叠阈值 0.35（朗读/书面可用 0.45 过严）。
    """
    hyp = (hyp_tgt or "").strip()
    ref = (ref_tgt or "").strip()
    if not hyp:
        return "wrong", "empty_tgt"
    ok_thr, awk_thr = 0.35, 0.20
    if direction == "zh_en":
        rw, hw = _norm_en_words(ref), _norm_en_words(hyp)
        if not rw:
            return "ok", "empty_ref"
        overlap = len(set(rw) & set(hw)) / float(len(set(rw)))
        if overlap >= ok_thr:
            return "ok", f"en_overlap={overlap:.2f}"
        if overlap >= awk_thr:
            return "awkward", f"en_overlap={overlap:.2f}"
        return "wrong", f"en_overlap={overlap:.2f}"
    # en_zh：汉字集合覆盖
    rc, hc = set(_norm_zh_chars(ref)), set(_norm_zh_chars(hyp))
    if not rc:
        return "ok", "empty_ref"
    overlap = len(rc & hc) / float(len(rc))
    if overlap >= ok_thr:
        return "ok", f"zh_overlap={overlap:.2f}"
    if overlap >= awk_thr:
        return "awkward", f"zh_overlap={overlap:.2f}"
    return "wrong", f"zh_overlap={overlap:.2f}"


# 对话同义簇（仅用于 sem 口径；不改 0.35 旧闸门）
_EN_SYN_CLUSTERS: tuple[frozenset[str], ...] = (
    frozenset({"continue", "keep", "going"}),
    frozenset({"popular", "penetration", "rate", "common", "widespread"}),
    frozenset({"right", "yes", "yeah", "exactly", "correct", "ok", "okay"}),
    frozenset({"good", "well", "nice", "fine", "great"}),
    frozenset({"talk", "chat", "discuss", "speak"}),
    frozenset({"smart", "phone", "smartphone", "phones"}),
)
_ZH_SYN_CLUSTERS: tuple[frozenset[str], ...] = (
    frozenset({"对", "是", "啊", "嗯", "没", "错", "好"}),
    frozenset({"哦", "喔", "噢"}),
    frozenset({"准", "确", "没", "错"}),
)


def _expand_tokens(tokens: list[str], clusters: tuple[frozenset[str], ...]) -> set[str]:
    out = set(tokens)
    for t in tokens:
        for c in clusters:
            if t in c:
                out |= set(c)
    return out


def mt_usable_sem(direction: str, ref_tgt: str, hyp_tgt: str) -> tuple[str, str]:
    """语义放宽口径：同义/更简洁可 ok；漏译（过短）绝不豁免。

    旧 mt_usable 阈值不变；本函数只作并列报表。
    """
    base_v, base_r = mt_usable(direction, ref_tgt, hyp_tgt)
    hyp = (hyp_tgt or "").strip()
    ref = (ref_tgt or "").strip()
    if not hyp:
        return "wrong", "empty_tgt"
    if base_v == "ok":
        return "ok", base_r + "|sem_keep"

    if direction == "zh_en":
        rw, hw = _norm_en_words(ref), _norm_en_words(hyp)
        if not rw:
            return "ok", "empty_ref|sem"
        # 漏译：译文词数明显短于参考
        if len(hw) < max(3, int(0.45 * len(rw))):
            return "wrong", f"sem_trunc hyp={len(hw)} ref={len(rw)}"
        rs = _expand_tokens(rw, _EN_SYN_CLUSTERS)
        hs = _expand_tokens(hw, _EN_SYN_CLUSTERS)
        overlap = len(rs & hs) / float(len(rs))
        if overlap >= 0.35:
            return "ok", f"sem_en_overlap={overlap:.2f}"
        if overlap >= 0.20:
            return "awkward", f"sem_en_overlap={overlap:.2f}"
        return base_v, base_r + f"|sem_en={overlap:.2f}"

    rc, hc = _norm_zh_chars(ref), _norm_zh_chars(hyp)
    if not rc:
        return "ok", "empty_ref|sem"
    if len(hc) < max(1, int(0.45 * len(rc))):
        return "wrong", f"sem_trunc hyp={len(hc)} ref={len(rc)}"
    rs = _expand_tokens(rc, _ZH_SYN_CLUSTERS)
    hs = _expand_tokens(hc, _ZH_SYN_CLUSTERS)
    overlap = len(rs & hs) / float(len(rs))
    if overlap >= 0.35:
        return "ok", f"sem_zh_overlap={overlap:.2f}"
    if overlap >= 0.20:
        return "awkward", f"sem_zh_overlap={overlap:.2f}"
    return base_v, base_r + f"|sem_zh={overlap:.2f}"


def _utt_dict(finished) -> dict:
    return {
        "src": finished.source or "",
        "tgt": finished.translated or "",
        "audio_sec": float(finished.audio_sec_final or 0),
        "cut_reason": getattr(finished, "probe_cut_reason", None),
        "asr_online": getattr(finished, "asr_online", None),
        "asr_offline": getattr(finished, "asr_offline", None),
        "arbiter_pick": getattr(finished, "arbiter_pick", None),
    }


def run_one(pipe: TranslatePipeline, wav: Path, *, force_src: str) -> list[dict]:
    raw = load_wav(str(wav), target_sr=SR)
    pad_n = int(0.5 * SR)
    audio = np.concatenate(
        [
            np.zeros(pad_n, np.float32),
            np.asarray(raw, dtype=np.float32).reshape(-1),
            np.zeros(int(1.0 * SR), np.float32),
        ]
    )
    engine = DualTrackEngine(
        pipe.asr,
        pipe.translator,
        None,
        play=False,
        force_src_lang=force_src,
        label="mslt",
    )
    utts: list[dict] = []
    for i in range(0, audio.size, BLOCK):
        finished = engine.feed_chunk(audio[i : i + BLOCK], allow_async_partial=False)
        if finished is not None:
            utts.append(_utt_dict(finished))
    if engine.in_speech and engine.buf.size:
        pad = np.zeros(int(config.VAD_SILENCE_SEC * SR) + 1600, np.float32)
        for _ in range(32):
            finished = engine.feed_chunk(pad, allow_async_partial=False)
            if finished is not None:
                utts.append(_utt_dict(finished))
                break
    return utts


def _fragment_dup(utts: list[dict], *, direction: str) -> bool:
    """相邻 utt 源文高度重叠（包含或 Jaccard≥0.8）。"""
    srcs = [(u.get("src") or "").strip() for u in utts if (u.get("src") or "").strip()]
    if len(srcs) < 2:
        return False
    for a, b in zip(srcs, srcs[1:]):
        if direction == "zh_en":
            ta, tb = set(_norm_zh_chars(a)), set(_norm_zh_chars(b))
        else:
            ta, tb = set(_norm_en_words(a)), set(_norm_en_words(b))
        if not ta or not tb:
            continue
        if a in b or b in a:
            return True
        j = len(ta & tb) / float(len(ta | tb))
        if j >= 0.8:
            return True
    return False


def _join_obs(utts: list[dict]) -> tuple[str | None, str | None, str | None]:
    """条目级：取最长 utt 的双假设（长句听错对照用）。"""
    if not utts:
        return None, None, None
    best = max(utts, key=lambda u: len(u.get("src") or ""))
    return (
        best.get("asr_online"),
        best.get("asr_offline"),
        best.get("arbiter_pick"),
    )


def _percentile(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    xs = sorted(vals)
    if len(xs) == 1:
        return round(xs[0], 4)
    # nearest-rank style
    idx = int(round((p / 100.0) * (len(xs) - 1)))
    idx = max(0, min(len(xs) - 1, idx))
    return round(xs[idx], 4)


def _agg(rows: list[dict]) -> dict:
    if not rows:
        return {}
    asr = [min(1.0, float(r["asr_err"])) for r in rows]
    cov = [float(r["asr_coverage"]) for r in rows]
    ok = sum(1 for r in rows if r["mt_verdict"] == "ok")
    ok_sem = sum(1 for r in rows if r.get("mt_verdict_sem", r["mt_verdict"]) == "ok")
    multi = sum(1 for r in rows if int(r["n_utts"]) >= 2)
    short = sum(1 for r in rows if r["has_short_utt"])
    incomplete = sum(1 for r in rows if float(r["asr_coverage"]) < 0.6)
    empty = sum(1 for r in rows if not str(r.get("asr_hyp") or "").strip())
    long = sum(
        1
        for r in rows
        if len(r.get("asr_hyp") or "") > max(8, 3 * len(r.get("src_ref") or ""))
    )
    asr_ok = sum(1 for e in asr if e <= 0.35)
    given = [r for r in rows if float(r["asr_err"]) <= 0.35]
    given_ok = sum(1 for r in given if r["mt_verdict"] == "ok")
    frag_lost = sum(1 for r in rows if bool(r.get("fragment_lost")))
    frag_dup = sum(1 for r in rows if bool(r.get("fragment_dup")))
    pair_sims = [
        float(r["pair_sim"])
        for r in rows
        if r.get("pair_sim") is not None
    ]
    n = len(rows)
    out = {
        "n": n,
        "asr_err_mean": round(sum(asr) / n, 4),
        "asr_ok_le_0.35": asr_ok,
        "asr_ok_rate": round(asr_ok / n, 4),
        "asr_coverage_mean": round(sum(cov) / n, 4),
        "hyp_much_longer": long,
        "hyp_much_longer_rate": round(long / n, 4),
        "mt_ok": ok,
        "mt_ok_rate": round(ok / n, 4),
        "mt_ok_sem": ok_sem,
        "mt_ok_rate_sem": round(ok_sem / n, 4),
        "mt_ok_given_asr": given_ok,
        "mt_ok_given_asr_n": len(given),
        "mt_ok_given_asr_rate": (
            round(given_ok / len(given), 4) if given else None
        ),
        "multi_utt": multi,
        "multi_utt_rate": round(multi / n, 4),
        "short_utt_items": short,
        "short_utt_item_rate": round(short / n, 4),
        "incomplete_asr": incomplete,
        "incomplete_asr_rate": round(incomplete / n, 4),
        "empty_asr": empty,
        "empty_asr_rate": round(empty / n, 4),
        "fragment_lost": frag_lost,
        "fragment_lost_rate": round(frag_lost / n, 4),
        "fragment_dup": frag_dup,
        "fragment_dup_rate": round(frag_dup / n, 4),
    }
    if pair_sims:
        out["pair_sim_min"] = round(min(pair_sims), 4)
        out["pair_sim_p10"] = _percentile(pair_sims, 10)
        out["pair_sim_p50"] = _percentile(pair_sims, 50)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--manifest",
        type=str,
        default=str(DEFAULT_MANIFEST),
        help="manifest.jsonl 路径（wav 相对该文件所在目录）",
    )
    ap.add_argument(
        "--out-dir",
        type=str,
        default=str(DEFAULT_OUT_DIR),
        help="detail/summary 输出目录",
    )
    args = ap.parse_args()

    manifest = Path(args.manifest)
    if not manifest.is_absolute():
        manifest = ROOT / manifest
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    detail_path = out_dir / "detail.jsonl"
    summary_path = out_dir / "summary.json"
    corpus_root = manifest.parent

    items = [
        json.loads(l)
        for l in manifest.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    if len(items) < 40:
        print(f"[abort] manifest too small: {len(items)} ({manifest})")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"loading pipeline… n={len(items)} manifest={manifest}")
    pipe = TranslatePipeline(enable_tts=False, tts_warmup_langs=())

    detail: list[dict] = []
    for i, item in enumerate(items):
        direction = item["direction"]
        force_src = "zh" if direction == "zh_en" else "en"
        wav = corpus_root / item["wav"]
        print(f"[{i+1}/{len(items)}] {item['id']} {direction}")
        utts = run_one(pipe, wav, force_src=force_src)
        src_join = " ".join(u["src"] for u in utts if u.get("src")).strip()
        if direction == "zh_en":
            src_join = "".join(u["src"] for u in utts if u.get("src")).strip()
        tgt_join = " ".join(u["tgt"] for u in utts if u.get("tgt")).strip()
        err = asr_error(direction, item["src_ref"], src_join)
        cov = asr_coverage(direction, item["src_ref"], src_join)
        verdict, reason = mt_usable(direction, item["tgt_ref"], tgt_join)
        verdict_sem, reason_sem = mt_usable_sem(
            direction, item["tgt_ref"], tgt_join
        )
        has_short = any(float(u.get("audio_sec") or 0) < 1.0 for u in utts)
        obs_on, obs_off, obs_pick = _join_obs(utts)
        frag_lost = bool(cov < 0.8)
        frag_dup = _fragment_dup(utts, direction=direction)
        pair_sim = item.get("pair_sim")
        row = {
            "id": item["id"],
            "direction": direction,
            "src_ref": item["src_ref"],
            "tgt_ref": item["tgt_ref"],
            "asr_hyp": src_join,
            "mt_hyp": tgt_join,
            "asr_err": round(err, 4),
            "asr_err_core": (
                asr_err_core(direction, item["src_ref"], src_join)
                if direction == "zh_en"
                else None
            ),
            "asr_coverage": round(cov, 4),
            "mt_verdict": verdict,
            "mt_reason": reason,
            "mt_verdict_sem": verdict_sem,
            "mt_reason_sem": reason_sem,
            "n_utts": len(utts),
            "has_short_utt": has_short,
            "asr_online": obs_on,
            "asr_offline": obs_off,
            "arbiter_pick": obs_pick,
            "fragment_lost": frag_lost,
            "fragment_dup": frag_dup,
            "pair_sim": pair_sim,
            "utts": utts,
        }
        detail.append(row)

    pipe.close()

    by_dir = {
        "zh_en": _agg([r for r in detail if r["direction"] == "zh_en"]),
        "en_zh": _agg([r for r in detail if r["direction"] == "en_zh"]),
    }
    summary = {
        "n": len(detail),
        "manifest": str(manifest),
        "by_direction": by_dir,
        "note": "当前 KPI=双人对话；AMI 非 KPI。asr_err: zh_en=CER, en_zh=WER",
    }

    with detail_path.open("w", encoding="utf-8") as f:
        for r in detail:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"wrote {detail_path}")
    print(f"wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
