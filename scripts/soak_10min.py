"""Long dualsim soak: adaptive resource sampling (no product changes).

Builds cache/soak/soak_10min.wav from four real-mic sessions, repeats the
pack until audio >= 38 minutes, then runs:

  run.py --mode dualsim --file … --no-tts --subtitle --subtitle-path …

Samples on an adaptive wall-clock interval into cache/soak/samples.jsonl.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import psutil
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
SOAK_DIR = ROOT / "cache" / "soak"
WAV_OUT = SOAK_DIR / "soak_10min.wav"
SAMPLES = SOAK_DIR / "samples.jsonl"
SUBTITLE = SOAK_DIR / "subtitle.json"
CHILD_OUT = SOAK_DIR / "child_stdout.txt"
SUMMARY = SOAK_DIR / "summary.json"
SR = 16000
SILENCE_SEC = 0.5
BASE_TARGET_SEC = 600.0
MIN_AUDIO_SEC = 38 * 60  # ≥38 minutes
LOAD_RSS_MB = 500.0

SEGMENTS = [
    "20261105-160805",
    "20261105-163258",
    "20261106-130636",
    "20261106-131540",
]
REPEAT_IF_SHORT = "20261105-163258"

UTT_RE = re.compile(r"^\[utt\]", re.M)
MT_RE = re.compile(r"^\[MT\b", re.M)
SUB_FAIL_RE = re.compile(r"\[subtitle\] write failed", re.I)


def _session_wav(sid: str) -> Path:
    return ROOT / "data" / "sessions" / sid / "mic_raw.wav"


def _load_mono(path: Path) -> np.ndarray:
    audio, file_sr = sf.read(str(path), dtype="float32", always_2d=False)
    if getattr(audio, "ndim", 1) > 1:
        audio = audio.mean(axis=1)
    if int(file_sr) != SR:
        raise ValueError(f"{path}: sr={file_sr}, need {SR}")
    return np.asarray(audio, dtype=np.float32).reshape(-1)


def build_wav() -> tuple[float, int, list[tuple[str, float]], int]:
    """Return (duration_s, base_segment_count, meta, pack_repeats)."""
    SOAK_DIR.mkdir(parents=True, exist_ok=True)
    silence = np.zeros(int(SR * SILENCE_SEC), dtype=np.float32)
    base_parts: list[np.ndarray] = []
    meta: list[tuple[str, float]] = []

    def append_sid(sid: str) -> None:
        audio = _load_mono(_session_wav(sid))
        if base_parts:
            base_parts.append(silence)
        base_parts.append(audio)
        meta.append((sid, len(audio) / SR))

    for sid in SEGMENTS:
        append_sid(sid)
    base_dur = sum(d for _, d in meta) + SILENCE_SEC * max(0, len(meta) - 1)
    if base_dur < BASE_TARGET_SEC:
        append_sid(REPEAT_IF_SHORT)
        base_dur = sum(d for _, d in meta) + SILENCE_SEC * max(0, len(meta) - 1)

    base = np.concatenate(base_parts) if base_parts else np.zeros(0, dtype=np.float32)
    packs = [base]
    # Repeat whole pack until >= 38 minutes (user baseline: ×3 ≈ 38.5 min)
    while (sum(len(p) for p in packs) / SR) < MIN_AUDIO_SEC:
        packs.append(silence)
        packs.append(base)
    wav = np.concatenate(packs)
    sf.write(str(WAV_OUT), wav, SR, subtype="PCM_16")
    n_packs = sum(1 for p in packs if len(p) == len(base))
    return float(len(wav) / SR), len(meta), meta, n_packs


def sample_interval_s(wav_duration_s: float) -> float:
    """Wall-clock interval: clamp(wav/40, 3, 15), then tighten for ≥30 points."""
    interval = float(np.clip(wav_duration_s / 40.0, 3.0, 15.0))
    # dualsim is ~12× realtime; estimate wall so we still get ≥30 samples
    est_wall = max(60.0, wav_duration_s / 12.0)
    if est_wall / interval < 30.0:
        interval = float(np.clip(est_wall / 35.0, 3.0, 15.0))
    return interval


def _newest_session_after(t_start: float) -> Path | None:
    base = ROOT / "data" / "sessions"
    if not base.is_dir():
        return None
    dirs = [p for p in base.iterdir() if p.is_dir() and p.stat().st_mtime >= t_start - 5]
    if not dirs:
        dirs = [p for p in base.iterdir() if p.is_dir()]
    if not dirs:
        return None
    return max(dirs, key=lambda p: p.stat().st_mtime)


def _count_live(path: Path) -> tuple[int, int, int, int]:
    """Returns (total, nonempty_src, empty_src, src_nonempty_tgt_empty)."""
    if not path.is_file():
        return 0, 0, 0, 0
    total = 0
    nonempty = 0
    empty_src = 0
    src_no_tgt = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        total += 1
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            empty_src += 1
            continue
        src = (o.get("src") or "").strip()
        tgt = (o.get("tgt") or "").strip()
        if not src:
            empty_src += 1
        else:
            nonempty += 1
            if not tgt:
                src_no_tgt += 1
    return total, nonempty, empty_src, src_no_tgt


def _steady_start_index(samples: list[dict]) -> int | None:
    """First post-load flat point: after RSS>500, first step with slope < 50 MB/min."""
    load_i = next((i for i, s in enumerate(samples) if s["rss_mb"] > LOAD_RSS_MB), None)
    if load_i is None:
        return None
    for i in range(load_i, len(samples) - 1):
        dt = samples[i + 1]["t_s"] - samples[i]["t_s"]
        if dt <= 1e-6:
            continue
        step_slope = (samples[i + 1]["rss_mb"] - samples[i]["rss_mb"]) / dt * 60.0
        if step_slope < 50.0:
            return i + 1
    return load_i


def _polyfit_slope_mb_per_min(samples: list[dict]) -> float | None:
    if len(samples) < 2:
        return None
    xs = np.array([s["t_s"] for s in samples], dtype=np.float64)
    ys = np.array([s["rss_mb"] for s in samples], dtype=np.float64)
    if float(np.ptp(xs)) < 1e-9:
        return 0.0
    slope_per_s = float(np.polyfit(xs, ys, 1)[0])
    return slope_per_s * 60.0


def _thirds_table(steady: list[dict]) -> list[dict]:
    """Split steady samples into early/mid/late thirds; report RSS endpoints + slope."""
    if len(steady) < 2:
        return []
    n = len(steady)
    cuts = [0, n // 3, (2 * n) // 3, n]
    out = []
    labels = ("前1/3", "中1/3", "后1/3")
    for i, lab in enumerate(labels):
        chunk = steady[cuts[i] : cuts[i + 1]]
        if len(chunk) < 2:
            if chunk:
                out.append(
                    {
                        "label": lab,
                        "rss0": chunk[0]["rss_mb"],
                        "rss1": chunk[0]["rss_mb"],
                        "slope_mb_per_min": 0.0,
                        "n": 1,
                    }
                )
            continue
        out.append(
            {
                "label": lab,
                "rss0": chunk[0]["rss_mb"],
                "rss1": chunk[-1]["rss_mb"],
                "slope_mb_per_min": round(_polyfit_slope_mb_per_min(chunk) or 0.0, 3),
                "n": len(chunk),
            }
        )
    return out


def _threads_pass(steady: list[dict]) -> tuple[bool, str]:
    if not steady:
        return False, "no steady samples"
    thr = [s["threads"] for s in steady]
    first, last = thr[0], thr[-1]
    mx = max(thr)
    span = mx - min(thr)
    argmax = thr.index(mx)
    in_first_third = argmax <= max(0, (len(thr) - 1) // 3)
    ok = (last <= first + 3) and (in_first_third or span < 5)
    detail = (
        f"first={first} last={last} max={mx}@{argmax}/{len(thr)} "
        f"span={span} first_third={in_first_third}"
    )
    return ok, detail


def _proc_rss_threads(proc: psutil.Process) -> tuple[float, int, float] | None:
    try:
        with proc.oneshot():
            rss = proc.memory_info().rss / (1024 * 1024)
            thr = proc.num_threads()
            cpu = proc.cpu_percent(interval=None)
        for c in proc.children(recursive=True):
            try:
                rss += c.memory_info().rss / (1024 * 1024)
                thr += c.num_threads()
            except (psutil.Error, ProcessLookupError):
                pass
        return rss, thr, cpu
    except (psutil.Error, ProcessLookupError):
        return None


def main() -> int:
    SOAK_DIR.mkdir(parents=True, exist_ok=True)
    for p in (SAMPLES, SUBTITLE, CHILD_OUT, SUMMARY):
        if p.is_file():
            p.unlink()

    dur, nseg, meta, n_packs = build_wav()
    interval = sample_interval_s(dur)
    print(f"[soak] wav → {WAV_OUT}")
    print(
        f"[soak] base_segments={nseg} packs×{n_packs} duration_s={dur:.1f} "
        f"({dur / 60:.1f} min) sample_interval={interval:.1f}s"
    )
    for sid, sec in meta:
        print(f"  - {sid}: {sec:.1f}s")

    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.is_file():
        py = Path(sys.executable)
    cmd = [
        str(py),
        "-u",
        str(ROOT / "run.py"),
        "--mode",
        "dualsim",
        "--file",
        str(WAV_OUT),
        "--no-tts",
        "--subtitle",
        "--subtitle-path",
        str(SUBTITLE),
    ]
    print("[soak] exec:", " ".join(cmd))
    t0 = time.time()
    deadline = t0 + 25 * 60

    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdout is not None
    child = psutil.Process(proc.pid)

    samples: list[dict] = []
    counts = {"utt": 0, "mt": 0, "sub_fail": 0}
    lock = threading.Lock()
    out_f = CHILD_OUT.open("w", encoding="utf-8")

    def on_line(line: str) -> None:
        out_f.write(line)
        out_f.flush()
        sys.stdout.write(line)
        sys.stdout.flush()
        with lock:
            if line.startswith("[utt]"):
                counts["utt"] += 1
            if line.startswith("[MT"):
                counts["mt"] += 1
            if SUB_FAIL_RE.search(line):
                counts["sub_fail"] += 1

    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            on_line(line)

    rt = threading.Thread(target=reader, name="soak-stdout", daemon=True)
    rt.start()

    def sample_once() -> None:
        got = _proc_rss_threads(child)
        if got is None:
            return
        rss, thr, cpu = got
        with lock:
            utt_n = counts["utt"]
        row = {
            "t_s": round(time.time() - t0, 1),
            "rss_mb": round(rss, 1),
            "threads": thr,
            "cpu_pct": round(cpu, 1),
            "utt_n": utt_n,
        }
        samples.append(row)
        with SAMPLES.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(
            f"[sample] t={row['t_s']:6.1f}s  RSS={row['rss_mb']:7.1f}MB  "
            f"threads={row['threads']:3d}  utt={utt_n}"
        )

    try:
        child.cpu_percent(interval=None)
    except psutil.Error:
        pass
    sample_once()
    next_sample = time.time() + interval
    truncated = False

    while proc.poll() is None:
        if time.time() > deadline:
            truncated = True
            print("[soak] wall > 25min — stopping child")
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
            break
        if time.time() >= next_sample:
            sample_once()
            next_sample = time.time() + interval
        time.sleep(0.1)

    rt.join(timeout=5)
    exit_code = proc.wait()
    sample_once()
    out_f.close()
    wall = time.time() - t0

    full_out = (
        CHILD_OUT.read_text(encoding="utf-8", errors="replace") if CHILD_OUT.is_file() else ""
    )
    has_tb = "Traceback (most recent call last)" in full_out
    utt_stdout = len(UTT_RE.findall(full_out))
    mt_stdout = len(MT_RE.findall(full_out))
    sub_fail_n = len(SUB_FAIL_RE.findall(full_out))

    sess = _newest_session_after(t0)
    live_path = (sess / "live.jsonl") if sess else None
    live_total, live_nonempty, live_empty_src, live_src_no_tgt = (
        _count_live(live_path) if live_path else (0, 0, 0, 0)
    )
    # [utt] 只在完整 finalize 打印；过短跳过 MT 有 src、无 tgt、无 [utt]
    live_utt_like = live_nonempty - live_src_no_tgt

    sub_ok = False
    sub_state = None
    sub_err = None
    sub_hist_n = 0
    if SUBTITLE.is_file():
        try:
            sub = json.loads(SUBTITLE.read_text(encoding="utf-8"))
            sub_ok = True
            sub_state = sub.get("state")
            sub_hist_n = len(sub.get("history") or [])
        except Exception as exc:  # noqa: BLE001
            sub_err = str(exc)

    rss0 = samples[0]["rss_mb"] if samples else 0.0
    rss1 = samples[-1]["rss_mb"] if samples else 0.0
    first_gt500 = next((i for i, s in enumerate(samples) if s["rss_mb"] > LOAD_RSS_MB), None)
    steady_i = _steady_start_index(samples)
    if first_gt500 is None:
        load_delta = None
        steady = []
    else:
        load_delta = round(samples[first_gt500]["rss_mb"] - rss0, 1)
        steady = samples[steady_i:] if steady_i is not None else []
    steady_delta = (
        None if len(steady) < 2 else round(steady[-1]["rss_mb"] - steady[0]["rss_mb"], 1)
    )
    steady_slope = _polyfit_slope_mb_per_min(steady) if len(steady) >= 2 else None
    # 报告用：全程三等分（对照基线）；泄漏判据用 steady_*（加载平台后）
    thirds_all = _thirds_table(samples)
    thirds = _thirds_table(steady)
    thr_ok, thr_detail = _threads_pass(steady)
    speed = (dur / wall) if wall > 1e-6 else None

    # gates
    gate1 = exit_code == 0 and not has_tb and not truncated
    gate2 = steady_slope is not None and steady_slope < 10.0
    gate3 = thr_ok
    gate4 = live_empty_src == 0 and abs(live_utt_like - utt_stdout) <= 1
    gate5 = sub_ok and sub_state == "idle"
    gate6 = sub_fail_n == 0

    print("\n=== soak table ===")
    print(f"{'t(s)':>8} | {'RSS(MB)':>8} | {'threads':>7} | {'utt':>5}")
    for s in samples:
        print(
            f"{s['t_s']:8.1f} | {s['rss_mb']:8.1f} | {s['threads']:7d} | {s['utt_n']:5d}"
        )
    print("\n=== RSS thirds (all samples) ===")
    for row in thirds_all:
        print(
            f"{row['label']}: {row['rss0']:.0f} → {row['rss1']:.0f} MB  "
            f"slope={row['slope_mb_per_min']} MB/min  n={row['n']}"
        )
    print("=== RSS thirds (post-load steady) ===")
    for row in thirds:
        print(
            f"{row['label']}: {row['rss0']:.0f} → {row['rss1']:.0f} MB  "
            f"slope={row['slope_mb_per_min']} MB/min  n={row['n']}"
        )
    print(
        f"\n[gates] 1={gate1} 2={gate2} 3={gate3} 4={gate4} 5={gate5} 6={gate6} "
        f"steady_slope={steady_slope} thr={thr_detail}"
    )

    summary = {
        "wav_duration_s": dur,
        "wav_duration_min": round(dur / 60.0, 2),
        "base_segments": meta,
        "base_segment_count": nseg,
        "pack_repeats": n_packs,
        "sample_interval_s": interval,
        "n_samples": len(samples),
        "wall_s": round(wall, 1),
        "speed_x": None if speed is None else round(speed, 2),
        "exit_code": exit_code,
        "truncated_25min": truncated,
        "has_traceback": has_tb,
        "utt_stdout": utt_stdout,
        "mt_stdout": mt_stdout,
        "subtitle_write_failed": sub_fail_n,
        "session": str(sess) if sess else None,
        "live_rows": live_total,
        "live_rows_nonempty": live_nonempty,
        "live_empty_rows": live_empty_src,
        "live_src_no_tgt": live_src_no_tgt,
        "live_utt_like": live_utt_like,
        "steady_start_t_s": None if not steady else steady[0]["t_s"],
        "first_rss_gt500_t_s": None
        if first_gt500 is None
        else samples[first_gt500]["t_s"],
        "subtitle_json_ok": sub_ok,
        "subtitle_state": sub_state,
        "subtitle_hist_n": sub_hist_n,
        "subtitle_err": sub_err,
        "rss_first_mb": rss0,
        "rss_last_mb": rss1,
        "load_delta_mb": load_delta,
        "load_delta_note": "含模型加载，不作为泄漏判据",
        "steady_delta_mb": steady_delta,
        "steady_slope_mb_per_min": None
        if steady_slope is None
        else round(steady_slope, 3),
        "rss_thirds_all": thirds_all,
        "rss_thirds_steady": thirds,
        "threads_detail": thr_detail,
        "gates": {
            "1_exit_ok": gate1,
            "2_steady_slope_lt_10": gate2,
            "3_threads_no_monotone_growth": gate3,
            "4_live_nonempty_vs_utt": gate4,
            "5_subtitle_idle": gate5,
            "6_no_write_failed": gate6,
        },
        "samples": samples,
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[soak] summary → {SUMMARY}")
    print(
        f"[soak] exit={exit_code} wall={wall:.1f}s audio={dur / 60:.1f}min "
        f"~{speed:.1f}x utt={utt_stdout} live_nonempty={live_nonempty} "
        f"steady_slope={steady_slope}"
    )
    return 0 if exit_code == 0 and not truncated else (exit_code or 1)


if __name__ == "__main__":
    raise SystemExit(main())
