"""Extract ≥10 FLEURS en_us utterances from local parquet (+ align local fixtures).

python scripts/extract_fleurs_parquet.py
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "cache" / "fleurs_hf" / "en_us_test_0.parquet"
PUB = ROOT / "cache" / "harvest_public"
OUT = ROOT / "data" / "ledger_corpus"
SR = 16000
N_TARGET = 12

# Local fixture utterance ids (path/audio may embed these).
LOCAL_UIDS = [
    "18066939864018796519",
    "3529855487992513201",
    "8173060820032255320",
]


def _resample(arr: np.ndarray, sr: int) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.float32).reshape(-1)
    if sr == SR:
        return arr
    x = np.arange(arr.size, dtype=np.float64)
    n = int(round(arr.size * SR / float(sr)))
    xp = np.linspace(0, arr.size - 1, max(n, 1))
    return np.interp(xp, x, arr).astype(np.float32)


def main() -> int:
    if not PARQUET.exists():
        print(f"missing {PARQUET}")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "fleurs").mkdir(parents=True, exist_ok=True)
    pf = pq.ParquetFile(PARQUET)
    print(f"parquet rows={pf.metadata.num_rows}")

    # Index all rows with text + audio bytes; prefer local fixture ids first.
    all_rows: list[dict] = []
    local_hits: dict[str, dict] = {}
    for rg in range(pf.num_row_groups):
        table = pf.read_row_group(rg)
        cols = {n: table.column(n) for n in table.column_names}
        for i in range(table.num_rows):
            text = (
                (cols["raw_transcription"][i].as_py() or "")
                or (cols["transcription"][i].as_py() or "")
            ).strip()
            if len(text.split()) < 5:
                continue
            audio = cols["audio"][i].as_py()
            if not isinstance(audio, dict) or not audio.get("bytes"):
                continue
            path = str(cols["path"][i].as_py() or "")
            audio_path = str(audio.get("path") or "")
            sid = str(cols["id"][i].as_py())
            blob = " ".join([sid, path, audio_path])
            rec = {
                "sentence_id": sid,
                "text": text,
                "bytes": audio["bytes"],
                "blob": blob,
            }
            all_rows.append(rec)
            for uid in LOCAL_UIDS:
                if uid in blob or uid in path or uid in audio_path:
                    local_hits[uid] = rec

    print(f"local fixture hits={len(local_hits)}/{len(LOCAL_UIDS)}")
    selected: list[tuple[str, dict]] = []
    seen_text: set[str] = set()
    for uid in LOCAL_UIDS:
        if uid in local_hits:
            selected.append((uid, local_hits[uid]))
            seen_text.add(local_hits[uid]["text"].lower())

    # Prefer also copying local wavs when present (bit-identical fixture).
    for uid, rec in list(selected):
        local_wav = PUB / f"fleurs_en_test_{uid}.wav"
        if local_wav.exists():
            arr, sr = sf.read(str(local_wav), dtype="float32", always_2d=False)
            rec = {**rec, "array": np.asarray(arr, dtype=np.float32), "sr": int(sr)}
            selected[selected.index((uid, local_hits[uid]))] = (uid, rec)

    for rec in all_rows:
        if len(selected) >= N_TARGET:
            break
        key = rec["text"].lower()
        if key in seen_text:
            continue
        uid = f"sid{rec['sentence_id']}"
        selected.append((uid, rec))
        seen_text.add(key)

    rows_out: list[dict] = []
    for i, (uid, rec) in enumerate(selected[:N_TARGET]):
        if "array" in rec:
            arr = _resample(rec["array"], int(rec.get("sr") or SR))
        else:
            arr, sr = sf.read(io.BytesIO(rec["bytes"]), dtype="float32", always_2d=False)
            arr = _resample(arr, int(sr))
        rel = f"fleurs/fleurs_{i:03d}_{uid}.wav"
        path = OUT / rel
        sf.write(str(path), arr, SR)
        rows_out.append(
            {
                "id": f"fleurs_{i:03d}",
                "corpus": "fleurs",
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "ref": rec["text"],
                "duration_sec": round(arr.size / float(SR), 3),
                "utterance_id": uid,
                "sentence_id": rec.get("sentence_id"),
                "ref_source": "google/fleurs en_us parquet official transcription",
            }
        )

    man = OUT / "manifest_fleurs.jsonl"
    with man.open("w", encoding="utf-8") as f:
        for r in rows_out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {man} n={len(rows_out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
