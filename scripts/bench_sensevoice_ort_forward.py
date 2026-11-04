"""Microbench SenseVoice ONNX forward: CPU vs DirectML (same fake features)."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import onnxruntime as ort

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "models" / "sherpa-sensevoice" / "model.int8.onnx"
LOG_MD = ROOT / "docs" / "OPTIMIZATION_LOG.md"
LOG_JSONL = ROOT / "docs" / "bench_history.jsonl"


def make_feeds(t_frames: int) -> dict:
    # shapes mirror sherpa SenseVoice int8 export
    x = np.random.randn(1, t_frames, 560).astype(np.float32) * 0.1
    return {
        "x": x,
        "x_length": np.array([t_frames], dtype=np.int32),
        "language": np.array([0], dtype=np.int32),  # auto
        "text_norm": np.array([14], dtype=np.int32),  # with_itn
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("cpu", "dml"), required=True)
    parser.add_argument("--frames", type=int, default=90, help="~5.5s utterance LFR frames")
    parser.add_argument("--repeats", type=int, default=8)
    parser.add_argument("--tag", default="")
    args = parser.parse_args()

    if args.provider == "dml":
        providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
    else:
        providers = ["CPUExecutionProvider"]

    avail = ort.get_available_providers()
    print("ort", ort.__version__, "avail", avail)
    for p in providers:
        name = p if isinstance(p, str) else p[0]
        if name not in avail and name != "CPUExecutionProvider":
            print("missing provider", name)
            return 2

    so = ort.SessionOptions()
    so.intra_op_num_threads = 6
    if args.provider == "dml":
        # Full graph opt can hard-crash Renoir+DML on this int8 model
        so.enable_mem_pattern = False
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC

    sess = ort.InferenceSession(str(MODEL), sess_options=so, providers=providers)
    print("session_providers", sess.get_providers())

    feeds = make_feeds(args.frames)
    # warmup
    for _ in range(2):
        sess.run(None, feeds)

    times: list[float] = []
    for _ in range(args.repeats):
        t0 = time.perf_counter()
        outs = sess.run(None, feeds)
        times.append((time.perf_counter() - t0) * 1000)
        assert outs and outs[0] is not None

    times_s = sorted(times)
    out = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tag": args.tag or f"sensevoice-ort-{args.provider}",
        "note": f"SenseVoice ONNX forward only provider={args.provider}",
        "mode": "sensevoice_ort_forward_ab",
        "provider": args.provider,
        "ort_version": ort.__version__,
        "session_providers": sess.get_providers(),
        "frames": args.frames,
        "ms_median": round(times_s[len(times_s) // 2], 1),
        "ms_mean": round(sum(times) / len(times), 1),
        "ms_min": round(times_s[0], 1),
        "ms_all": [round(x, 1) for x in times],
    }
    LOG_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with LOG_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False) + "\n")
    md = (
        f"| {out['time']} | {out['tag']} | forward_median={out['ms_median']} "
        f"frames={args.frames} | ort={ort.__version__} sess={out['session_providers']} |"
    )
    if LOG_MD.exists():
        text = LOG_MD.read_text(encoding="utf-8")
        LOG_MD.write_text(text.rstrip() + "\n" + md + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("SENSEVOICE_ORT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
