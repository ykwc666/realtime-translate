"""10-minute dualsim soak: resource sampling only (no feature changes).

Builds cache/soak/soak_10min.wav from four real-mic sessions (optionally
repeats 163258 to reach >=600s), then runs:

  run.py --mode dualsim --file … --no-tts --subtitle --subtitle-path …

Samples the child every 30s (RSS / threads / CPU%) into cache/soak/samples.jsonl.
Stdout is mirrored to cache/soak/child_stdout.txt for post counts.
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
SR = 16000
SILENCE_SEC = 0.5
TARGET_SEC = 600.0

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


def build_wav() -> tuple[float, int, list[tuple[str, float]]]:
    SOAK_DIR.mkdir(parents=True, exist_ok=True)
    parts: list[np.ndarray] = []
    meta: list[tuple[str, float]] = []
    silence = np.zeros(int(SR * SILENCE_SEC), dtype=np.float32)

    def append_sid(sid: str) -> None:
        path = _session_wav(sid)
        if not path.is_file():
            raise FileNotFoundError(path)
        audio, file_sr = sf.read(str(path), dtype="float32", always_2d=False)
        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)
        if int(file_sr) != SR:
            raise ValueError(f"{path}: sr={file_sr}, need {SR}")
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if parts:
            parts.append(silence)
        parts.append(audio)
        meta.append((sid, len(audio) / SR))

    for sid in SEGMENTS:
        append_sid(sid)
    total = sum(d for _, d in meta) + SILENCE_SEC * max(0, len(meta) - 1)
    if total < TARGET_SEC:
        append_sid(REPEAT_IF_SHORT)
        total = sum(d for _, d in meta) + SILENCE_SEC * max(0, len(meta) - 1)

    wav = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
    sf.write(str(WAV_OUT), wav, SR, subtype="PCM_16")
    return float(len(wav) / SR), len(meta), meta


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


def _count_live(path: Path) -> tuple[int, int]:
    if not path.is_file():
        return 0, 0
    n = 0
    empty = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        n += 1
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            empty += 1
            continue
        src = (o.get("src") or "").strip()
        tgt = (o.get("tgt") or "").strip()
        if not src or not tgt:
            empty += 1
    return n, empty


def _slope_mb_per_min(samples: list[dict], window_s: float = 180.0) -> float | None:
    if len(samples) < 2:
        return None
    t_end = samples[-1]["t_s"]
    window = [s for s in samples if s["t_s"] >= t_end - window_s]
    if len(window) < 2:
        window = samples
    t0 = window[0]["t_s"]
    xs = [s["t_s"] - t0 for s in window]
    ys = [s["rss_mb"] for s in window]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 1e-9:
        return 0.0
    slope_per_s = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    return slope_per_s * 60.0


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
    for p in (SAMPLES, SUBTITLE, CHILD_OUT):
        if p.is_file():
            p.unlink()

    dur, nseg, meta = build_wav()
    print(f"[soak] wav → {WAV_OUT}")
    print(f"[soak] segments={nseg} duration_s={dur:.1f}")
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
    next_sample = time.time() + 30.0
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
            next_sample = time.time() + 30.0
        time.sleep(0.2)

    rt.join(timeout=5)
    exit_code = proc.wait()
    sample_once()
    out_f.close()
    wall = time.time() - t0

    full_out = CHILD_OUT.read_text(encoding="utf-8", errors="replace") if CHILD_OUT.is_file() else ""
    has_tb = "Traceback (most recent call last)" in full_out
    utt_stdout = len(UTT_RE.findall(full_out))
    mt_stdout = len(MT_RE.findall(full_out))
    sub_fail_n = len(SUB_FAIL_RE.findall(full_out))

    sess = _newest_session_after(t0)
    live_path = (sess / "live.jsonl") if sess else None
    live_n, live_empty = _count_live(live_path) if live_path else (0, 0)

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
    # steady: first sample with RSS>=200MB (post model load), if any
    steady = [s for s in samples if s["rss_mb"] >= 200.0]
    if len(steady) >= 2:
        rss_steady0 = steady[0]["rss_mb"]
        rss_steady1 = steady[-1]["rss_mb"]
        slope_steady = _slope_mb_per_min(steady)
    else:
        rss_steady0 = rss_steady1 = None
        slope_steady = None
    thr_vals = [s["threads"] for s in samples]
    thr_span = (max(thr_vals) - min(thr_vals)) if thr_vals else 0
    thr_steady_vals = [s["threads"] for s in steady] if steady else []
    thr_steady_span = (
        (max(thr_steady_vals) - min(thr_steady_vals)) if thr_steady_vals else None
    )
    slope = _slope_mb_per_min(samples)

    print("\n=== soak table ===")
    print(f"{'t(s)':>8} | {'RSS(MB)':>8} | {'threads':>7} | {'utt':>5}")
    for s in samples:
        print(
            f"{s['t_s']:8.1f} | {s['rss_mb']:8.1f} | {s['threads']:7d} | {s['utt_n']:5d}"
        )

    summary = {
        "wav_duration_s": dur,
        "segments": meta,
        "segment_count": nseg,
        "wall_s": round(wall, 1),
        "exit_code": exit_code,
        "truncated_25min": truncated,
        "has_traceback": has_tb,
        "utt_stdout": utt_stdout,
        "mt_stdout": mt_stdout,
        "subtitle_write_failed": sub_fail_n,
        "session": str(sess) if sess else None,
        "live_rows": live_n,
        "live_empty_src_or_tgt": live_empty,
        "subtitle_json_ok": sub_ok,
        "subtitle_state": sub_state,
        "subtitle_hist_n": sub_hist_n,
        "subtitle_err": sub_err,
        "rss_first_mb": rss0,
        "rss_last_mb": rss1,
        "rss_delta_mb": round(rss1 - rss0, 1),
        "rss_slope_last3min_mb_per_min": None if slope is None else round(slope, 3),
        "rss_steady_first_mb": rss_steady0,
        "rss_steady_last_mb": rss_steady1,
        "rss_steady_delta_mb": None
        if rss_steady0 is None
        else round(rss_steady1 - rss_steady0, 1),
        "rss_steady_slope_mb_per_min": None
        if slope_steady is None
        else round(slope_steady, 3),
        "threads_min": min(thr_vals) if thr_vals else None,
        "threads_max": max(thr_vals) if thr_vals else None,
        "threads_span": thr_span,
        "threads_steady_span": thr_steady_span,
        "samples": samples,
    }
    (SOAK_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n[soak] summary → {SOAK_DIR / 'summary.json'}")
    print(
        f"[soak] exit={exit_code} wall={wall:.1f}s utt_stdout={utt_stdout} "
        f"live={live_n} rssΔ={rss1 - rss0:.1f}MB sub_fail={sub_fail_n}"
    )
    return 0 if exit_code == 0 and not truncated else (exit_code or 1)


if __name__ == "__main__":
    raise SystemExit(main())
