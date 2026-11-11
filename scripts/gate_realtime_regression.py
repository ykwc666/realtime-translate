"""No-mic realtime regression gate (dualsim + MicProbe).

Gates:
  - dualsim exits cleanly
  - utterance count in [4, 8] on accept_phrases.wav
  - probe empty_abort == 0; no non-abort rows with empty src
  - probe on vs off wall Δ：分进程各跑、≥5 轮 A/B 交替，取中位 ≤ 3%

测法说明（CURSOR_PROMPT_MEASURE_PROTOCOL）：
  同进程先 A 后 B 测到的是冷热差，不是 probe 开销。必须分进程。
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app import config  # noqa: E402
from app.mic_probe import MicProbe  # noqa: E402
from app.pipeline import TranslatePipeline  # noqa: E402
from piper import PiperVoice  # noqa: E402

SR = int(config.SAMPLE_RATE)
PHRASE_WAV = ROOT / "cache" / "ab_weekday" / "accept_phrases.wav"
UTT_MIN, UTT_MAX = 4, 8
WALL_DELTA_MAX = 0.03
ROUNDS = 5
PY = sys.executable


def _ensure_wav() -> Path:
    if PHRASE_WAV.is_file() and PHRASE_WAV.stat().st_size > 1000:
        return PHRASE_WAV
    print(f"[gate] synthesizing {PHRASE_WAV}")
    voice = PiperVoice.load(str(config.PIPER_VOICE_ZH))
    phrases = [
        "你好",
        "延迟大概有多少",
        "今天天气怎么样",
        "现在是星期二",
        "他现在没有回我的消息",
    ]

    def one(text: str) -> np.ndarray:
        chunks = [
            np.asarray(ch.audio_float_array, dtype=np.float32)
            for ch in voice.synthesize(text)
        ]
        pcm = np.concatenate(chunks)
        src = int(voice.config.sample_rate)
        if src != SR:
            x = np.arange(pcm.size, dtype=np.float64)
            n = int(round(pcm.size * SR / float(src)))
            xp = np.linspace(0, pcm.size - 1, max(n, 1))
            pcm = np.interp(xp, x, pcm).astype(np.float32)
        return np.concatenate([pcm, np.zeros(int(1.3 * SR), np.float32)])

    audio = np.concatenate([one(p) for p in phrases])
    PHRASE_WAV.parent.mkdir(parents=True, exist_ok=True)
    pcm16 = (np.clip(audio, -1, 1) * 32767.0).astype(np.int16)
    with wave.open(str(PHRASE_WAV), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm16.tobytes())
    return PHRASE_WAV


def _run_arm(*, with_probe: bool) -> dict:
    """单臂：在本进程跑 dualsim（供 --arm 子进程调用）。"""
    probe = None
    if with_probe:
        probe_dir = ROOT / "cache" / "gate_realtime_probe"
        probe_dir.mkdir(parents=True, exist_ok=True)
        probe = MicProbe(
            jsonl_path=probe_dir / f"gate_{'on' if with_probe else 'off'}.jsonl",
            summary_path=probe_dir / f"summary_{'on' if with_probe else 'off'}.json",
        )
    pipe = TranslatePipeline(
        enable_tts=False,
        mic_probe=probe,
        tts_warmup_langs=(),
    )
    t0 = time.perf_counter()
    results = pipe.run_dualsim(str(PHRASE_WAV), play=False)
    wall = time.perf_counter() - t0
    pipe.close()

    empty_abort = int(probe.empty_abort) if probe is not None else 0
    bad_empty = 0
    if probe is not None:
        bad_empty = len(
            [
                r
                for r in probe._rows  # noqa: SLF001
                if not str(r.get("src") or "").strip()
                and str(r.get("cut_reason") or "") != "empty_abort"
            ]
        )
    srcs = [str(r.source or "") for r in results]
    tgts = [str(r.translated or "") for r in results]
    n_voiced = sum(1 for s, t in zip(srcs, tgts) if s.strip() and t.strip())
    out = {
        "wall": wall,
        "n_utt": len(results),
        "n_voiced": n_voiced,
        "n_total": len(results),
        "empty_abort": empty_abort,
        "bad_empty": bad_empty,
        "probe": bool(with_probe),
        "srcs": srcs,
        "tgts": tgts,
    }
    return out


def _arm_subprocess(*, with_probe: bool) -> dict:
    cmd = [
        PY,
        str(Path(__file__).resolve()),
        "--arm",
        "on" if with_probe else "off",
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"arm subprocess failed rc={proc.returncode}\n"
            f"stdout:\n{proc.stdout[-2000:]}\nstderr:\n{proc.stderr[-2000:]}"
        )
    # 最后一行 JSON
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    payload = None
    for ln in reversed(lines):
        if ln.startswith("{") and '"wall"' in ln:
            payload = json.loads(ln)
            break
    if payload is None:
        raise RuntimeError(f"arm subprocess missing JSON\nstdout:\n{proc.stdout[-2000:]}")
    return payload


def _p90(xs: list[float]) -> float:
    if not xs:
        return float("nan")
    v = sorted(xs)
    return float(v[min(len(v) - 1, int(len(v) * 0.9))])


def _one_campaign(rounds: int) -> dict:
    """一轮完整战役：分进程 A/B 交替 rounds 次。"""
    deltas: list[float] = []
    walls_on: list[float] = []
    walls_off: list[float] = []
    first_on: dict | None = None

    for i in range(rounds):
        if i % 2 == 0:
            on = _arm_subprocess(with_probe=True)
            off = _arm_subprocess(with_probe=False)
        else:
            off = _arm_subprocess(with_probe=False)
            on = _arm_subprocess(with_probe=True)
        if first_on is None:
            first_on = on
        walls_on.append(float(on["wall"]))
        walls_off.append(float(off["wall"]))
        denom = max(float(off["wall"]), 1e-6)
        d = abs(float(on["wall"]) - float(off["wall"])) / denom
        deltas.append(d)
        print(
            f"  round {i+1}/{rounds}: on={on['wall']:.3f}s off={off['wall']:.3f}s "
            f"Δ={d*100:.2f}% utt_on={on['n_utt']} "
            f"voiced={on.get('n_voiced')}/{on.get('n_total')}"
        )

    assert first_on is not None
    return {
        "first_on": first_on,
        "walls_on": walls_on,
        "walls_off": walls_off,
        "deltas": deltas,
        "p50_on": float(statistics.median(walls_on)),
        "p50_off": float(statistics.median(walls_off)),
        "p90_on": _p90(walls_on),
        "p90_off": _p90(walls_off),
        "med_delta": float(statistics.median(deltas)),
    }


def _print_delivery(tag: str, camp: dict) -> None:
    """交数格式（CURSOR_PROMPT_MEASURE_PROTOCOL / 产品测法提示词）。"""
    fo = camp["first_on"]
    n_v = int(fo.get("n_voiced") or 0)
    n_t = int(fo.get("n_total") or fo.get("n_utt") or 0)
    p50_on, p50_off = camp["p50_on"], camp["p50_off"]
    p90_on, p90_off = camp["p90_on"], camp["p90_off"]
    d50 = abs(p50_on - p50_off) / max(p50_off, 1e-6) * 100
    d90 = abs(p90_on - p90_off) / max(p90_off, 1e-6) * 100
    rounds_s = " / ".join(f"{x*100:.2f}%" for x in camp["deltas"])
    worse = "无（probe on 相对 off 的 wall 中位未超闸）"
    if camp["med_delta"] > WALL_DELTA_MAX:
        worse = (
            f"probe wall median Δ 变差至 {camp['med_delta']*100:.2f}% "
            f"（闸门 ≤{WALL_DELTA_MAX*100:.0f}%）"
        )
    print(f"\n===== DELIVERY [{tag}] =====")
    print(f"n: 有声 {n_v} / 总行 {n_t}")
    print(
        f"p50: {p50_on:.3f}s → {p50_off:.3f}s (Δ{d50:.2f}%)     "
        f"p90: {p90_on:.3f}s → {p90_off:.3f}s (Δ{d90:.2f}%)"
    )
    print(f"5 轮各自的数: {rounds_s}")
    print(f"哪个指标变差了、差多少: {worse}")
    print(
        "一行能直接贴进 CMD 的复现命令: "
        r".venv\Scripts\python.exe scripts\gate_realtime_regression.py --rounds 5 --repeat 2"
    )
    print("===== END DELIVERY =====\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--arm",
        choices=["on", "off"],
        default="",
        help="internal: run one arm in this process and print JSON",
    )
    parser.add_argument("--rounds", type=int, default=ROUNDS)
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="完整战役重复次数；≥2 时验收两次 p50(on) 相对差 ≤3%",
    )
    parser.add_argument(
        "--report",
        type=str,
        default=str(ROOT / "docs" / "GATE_REALTIME_RULER.md"),
        help="写出交数报告路径",
    )
    args = parser.parse_args()

    _ensure_wav()

    if args.arm:
        result = _run_arm(with_probe=(args.arm == "on"))
        print(json.dumps(result, ensure_ascii=False))
        return 0

    rounds = max(5, int(args.rounds))
    repeat = max(1, int(args.repeat))
    print(f"[gate] wav={PHRASE_WAV}")
    print(
        f"[gate] subprocess A/B × {rounds} rounds × {repeat} campaigns "
        f"(median wall Δ ≤ {WALL_DELTA_MAX*100:.0f}%; "
        f"repeat p50 相对差 ≤3%)"
    )

    camps: list[dict] = []
    fails: list[str] = []
    for c in range(repeat):
        print(f"\n=== campaign {c+1}/{repeat} ===")
        camp = _one_campaign(rounds)
        camps.append(camp)
        _print_delivery(f"campaign-{c+1}", camp)

    # 闸门：以第一战役为准 + 重复性
    first = camps[0]
    fo = first["first_on"]
    n_on = int(fo["n_utt"])
    empty_abort = int(fo["empty_abort"])
    bad_empty = int(fo["bad_empty"])
    for src, tgt in zip(fo.get("srcs") or [], fo.get("tgts") or []):
        print(f"  [utt] src={src!r} | tgt={tgt!r}")

    if not (UTT_MIN <= n_on <= UTT_MAX):
        fails.append(f"utt_count={n_on} not in [{UTT_MIN},{UTT_MAX}]")
    if empty_abort != 0:
        fails.append(f"empty_abort={empty_abort}")
    if bad_empty:
        fails.append(f"non-abort empty src rows={bad_empty}")
    if first["med_delta"] > WALL_DELTA_MAX:
        fails.append(
            f"median_wall_delta={first['med_delta']*100:.2f}% > {WALL_DELTA_MAX*100:.0f}%"
        )

    p50_spread = 0.0
    if len(camps) >= 2:
        # 同一尺子两次：用 probe-off 的 p50 wall 做复现（分进程冷启，应接近）
        a = camps[0]["p50_off"]
        b = camps[1]["p50_off"]
        p50_spread = abs(a - b) / max(min(a, b), 1e-6)
        print(
            f"\n[ruler] campaign1 p50_off={a:.3f}s  campaign2 p50_off={b:.3f}s  "
            f"相对差={p50_spread*100:.2f}% (limit ≤3%)"
        )
        if p50_spread > 0.03:
            fails.append(f"ruler_p50_spread={p50_spread*100:.2f}% > 3%")

    print("\n--- gates ---")
    print(f"utt_count[{UTT_MIN},{UTT_MAX}]: {'PASS' if UTT_MIN <= n_on <= UTT_MAX else 'FAIL'} ({n_on})")
    print(f"empty_abort==0: {'PASS' if empty_abort == 0 else 'FAIL'} ({empty_abort})")
    print(f"no bad empty src: {'PASS' if not bad_empty else 'FAIL'}")
    print(
        f"probe wall median Δ≤{WALL_DELTA_MAX*100:.0f}%: "
        f"{'PASS' if first['med_delta'] <= WALL_DELTA_MAX else 'FAIL'} "
        f"({first['med_delta']*100:.2f}%)"
    )
    if len(camps) >= 2:
        print(
            f"ruler p50 复现≤3%: "
            f"{'PASS' if p50_spread <= 0.03 else 'FAIL'} ({p50_spread*100:.2f}%)"
        )

    # 写报告
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Gate realtime 尺子报告",
        "",
        f"wav: `{PHRASE_WAV}`",
        f"测法: 分进程 A/B 交替 ×{rounds}，statistics.median / 最近秩 p90；repeat={repeat}",
        "",
    ]
    for i, camp in enumerate(camps, 1):
        fo = camp["first_on"]
        lines.append(f"## campaign {i}")
        lines.append("")
        lines.append(
            f"- n: 有声 {fo.get('n_voiced')} / 总行 {fo.get('n_total')}"
        )
        lines.append(
            f"- p50: {camp['p50_on']:.3f}s → {camp['p50_off']:.3f}s "
            f"(probe on → off)"
        )
        lines.append(
            f"- p90: {camp['p90_on']:.3f}s → {camp['p90_off']:.3f}s"
        )
        lines.append(
            "- 5 轮 Δ: "
            + " / ".join(f"{x*100:.2f}%" for x in camp["deltas"])
        )
        lines.append(f"- median Δ: {camp['med_delta']*100:.2f}%")
        lines.append("")
    if len(camps) >= 2:
        lines.append(
            f"## 尺子复现\n\n"
            f"两次 campaign 的 p50_off 相对差 = **{p50_spread*100:.2f}%**"
            f"（验收 ≤3%）\n"
        )
    lines.append("## 复现命令\n")
    lines.append(
        "```bat\n"
        r"cd /d D:\xuanjian\realtime-translate" + "\n"
        r".venv\Scripts\python.exe scripts\gate_realtime_regression.py --rounds 5 --repeat 2"
        + "\n```\n"
    )
    lines.append(
        f"\n结果: **{'GATE_REALTIME_OK' if not fails else 'GATE_REALTIME_FAIL'}**"
        + (f" ({'; '.join(fails)})" if fails else "")
        + "\n"
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[gate] wrote {report_path}")

    if fails:
        print("GATE_REALTIME_FAIL", "; ".join(fails))
        return 1
    print("GATE_REALTIME_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
