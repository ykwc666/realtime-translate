"""Extract AMI ihm utterances from a local parquet shard (official text refs).

python scripts/extract_ami_parquet.py
"""
from __future__ import annotations

import io
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "cache" / "ami_hf" / "ihm" / "test-00000-of-00004.parquet"
OUT = ROOT / "data" / "ledger_corpus"
AMI_DIR = OUT / "ami"
SR = 16000
N_TARGET = 48
MAX_MEETINGS = 3


def main() -> int:
    if not PARQUET.exists():
        print(f"missing {PARQUET}")
        return 2
    AMI_DIR.mkdir(parents=True, exist_ok=True)
    pf = pq.ParquetFile(PARQUET)
    print(f"parquet rows={pf.metadata.num_rows}")

    # First pass: meeting counts for utterances that look usable
    meeting_hits: dict[str, int] = defaultdict(int)
    candidates: list[dict] = []
    for rg in range(pf.num_row_groups):
        table = pf.read_row_group(rg)
        cols = {name: table.column(name) for name in table.column_names}
        for i in range(table.num_rows):
            text = (cols["text"][i].as_py() or "").strip()
            if len(text.split()) < 4:
                continue
            begin = float(cols["begin_time"][i].as_py() or 0)
            end = float(cols["end_time"][i].as_py() or 0)
            dur = end - begin
            if dur < 1.0 or dur > 20.0:
                continue
            mid = str(cols["meeting_id"][i].as_py())
            audio = cols["audio"][i].as_py()
            if not isinstance(audio, dict) or not audio.get("bytes"):
                continue
            candidates.append(
                {
                    "meeting_id": mid,
                    "speaker_id": str(cols["speaker_id"][i].as_py()),
                    "audio_id": str(cols["audio_id"][i].as_py()),
                    "text": text,
                    "begin_time": begin,
                    "end_time": end,
                    "duration_sec": round(dur, 3),
                    "bytes": audio["bytes"],
                }
            )
            meeting_hits[mid] += 1

    # pick top meetings by count
    top = sorted(meeting_hits.items(), key=lambda x: -x[1])[:MAX_MEETINGS]
    allowed = {m for m, _ in top}
    print("meetings selected:", top)

    per_meeting = N_TARGET // max(len(allowed), 1)
    taken: dict[str, int] = defaultdict(int)
    rows: list[dict] = []
    for c in candidates:
        mid = c["meeting_id"]
        if mid not in allowed:
            continue
        if taken[mid] >= per_meeting + 4:
            continue
        # decode wav bytes
        arr, sr = sf.read(io.BytesIO(c["bytes"]), dtype="float32", always_2d=False)
        arr = np.asarray(arr, dtype=np.float32).reshape(-1)
        if sr != SR:
            x = np.arange(arr.size, dtype=np.float64)
            n = int(round(arr.size * SR / float(sr)))
            xp = np.linspace(0, arr.size - 1, max(n, 1))
            arr = np.interp(xp, x, arr).astype(np.float32)
            sr = SR
        idx = len(rows)
        rel = f"ami/{mid}_{c['speaker_id']}_{idx:03d}.wav"
        path = OUT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(path), arr, sr)
        rows.append(
            {
                "id": f"ami_{idx:03d}",
                "corpus": "ami",
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "ref": c["text"],
                "duration_sec": round(arr.size / float(sr), 3),
                "meeting_id": mid,
                "speaker_id": c["speaker_id"],
                "audio_id": c["audio_id"],
                "ref_source": "edinburghcstr/ami ihm parquet official text",
            }
        )
        taken[mid] += 1
        if len(rows) >= N_TARGET:
            break

    man = OUT / "manifest_ami.jsonl"
    with man.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {man} n={len(rows)} per_meeting={dict(taken)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
