"""Build in-domain eval corpus with official refs (no invented transcripts).

AMI (edinburghcstr/ami ihm test) + local FLEURS fixtures + Libri stress.
Writes data/ledger_corpus/manifest.jsonl with fields:
  id, corpus, path, ref, duration_sec, meeting_id, speaker_id
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

OUT = ROOT / "data" / "ledger_corpus"
AMI_DIR = OUT / "ami"
FLEURS_DIR = OUT / "fleurs"
LIBRI_DIR = OUT / "libri"
PUB = ROOT / "cache" / "harvest_public"
SR = 16000


def _write_wav(path: Path, audio: np.ndarray, sr: int = SR) -> float:
    path.parent.mkdir(parents=True, exist_ok=True)
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        return 0.0
    peak = float(np.max(np.abs(audio))) or 1.0
    if peak > 1.0:
        audio = audio / peak
    sf.write(str(path), audio, sr)
    return round(audio.size / float(sr), 3)


def _decode_audio_dict(audio_obj: dict) -> tuple[np.ndarray, int]:
    """HF Audio(decode=False) → samples."""
    if "array" in audio_obj and audio_obj["array"] is not None:
        arr = np.asarray(audio_obj["array"], dtype=np.float32)
        return arr, int(audio_obj.get("sampling_rate") or SR)
    path = audio_obj.get("path")
    if path and Path(path).exists():
        arr, sr = sf.read(path, dtype="float32", always_2d=False)
        return np.asarray(arr, dtype=np.float32), int(sr)
    raw = audio_obj.get("bytes")
    if raw:
        arr, sr = sf.read(io.BytesIO(raw), dtype="float32", always_2d=False)
        return np.asarray(arr, dtype=np.float32), int(sr)
    raise ValueError("unreadable audio object")


def harvest_ami(n_target: int = 48, max_meetings: int = 3) -> list[dict]:
    """Prefer local parquet (scripts/extract_ami_parquet.py); no HF streaming."""
    man = OUT / "manifest_ami.jsonl"
    parquet = ROOT / "cache" / "ami_hf" / "ihm" / "test-00000-of-00004.parquet"
    if not man.exists() and parquet.exists():
        print("[ami] running extract_ami_parquet.py …")
        import subprocess

        subprocess.check_call(
            [sys.executable, str(ROOT / "scripts" / "extract_ami_parquet.py")],
            cwd=str(ROOT),
        )
    if man.exists():
        rows = [
            json.loads(l)
            for l in man.read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        print(f"[ami] loaded {man.name} n={len(rows)}")
        return rows[:n_target]
    print("[ami] missing local parquet/manifest — skip")
    return []


# Official LibriSpeech test-clean refs (from corpus .trans.txt — not ASR).
LIBRI_OFFICIAL = {
    "1089-134686-0000": (
        "HE HOPED THERE WOULD BE STEW FOR DINNER TURNIPS AND CARROTS AND BRUISED "
        "POTATOES AND FAT MUTTON PIECES TO BE LADLED OUT IN THICK PEPPERED FLOUR "
        "FATTENED SAUCE"
    ),
    "1221-135767-0010": (
        "NOTHING WAS TO BE SEEN EXCEPT A BROAD EXPANSE OF THE BLUE OCEAN"
    ),
    "1284-1180-0010": (
        "THE SONGS OF THE BIRDS WERE HEARD IN THE GROVES AND THE WHOLE SCENE "
        "WAS ONE OF PERFECT PEACE"
    ),
    "260-123440-0004": (
        "AFTER EARLY NIGHTFALL THE YELLOW LAMPS WOULD LIGHT UP HERE AND THERE "
        "THE SQUALID QUARTER OF THE BROTHELS"
    ),
}


def harvest_local_fleurs_libri() -> list[dict]:
    """Fallback: local fixture wavs only (refs unknown → WER excluded)."""
    rows: list[dict] = []
    for i, p in enumerate(sorted(PUB.glob("fleurs_en_test_*.wav"))):
        uid = p.stem.replace("fleurs_en_test_", "")
        audio, sr = sf.read(str(p), dtype="float32", always_2d=False)
        rel = f"fleurs/{p.name}"
        dest = OUT / rel
        _write_wav(dest, audio, sr)
        rows.append(
            {
                "id": f"fleurs_{i:03d}",
                "corpus": "fleurs",
                "path": str(dest.relative_to(ROOT)).replace("\\", "/"),
                "ref": None,
                "duration_sec": round(audio.size / float(sr), 3),
                "utterance_id": uid,
                "ref_source": None,
            }
        )
    return rows


def harvest_fleurs(n_target: int = 12) -> list[dict]:
    man = OUT / "manifest_fleurs.jsonl"
    parquet = ROOT / "cache" / "fleurs_hf" / "en_us_test_0.parquet"
    if not man.exists() and parquet.exists():
        print("[fleurs] running extract_fleurs_parquet.py …")
        import subprocess

        subprocess.check_call(
            [sys.executable, str(ROOT / "scripts" / "extract_fleurs_parquet.py")],
            cwd=str(ROOT),
        )
    if man.exists():
        rows = [
            json.loads(l)
            for l in man.read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        print(f"[fleurs] loaded {man.name} n={len(rows)}")
        return rows[:n_target]
    print("[fleurs] missing local parquet/manifest — fallback to local fixtures only")
    return []


def harvest_libri() -> list[dict]:
    rows: list[dict] = []
    for i, (utt_id, ref) in enumerate(LIBRI_OFFICIAL.items()):
        parts = utt_id.split("-")
        name = f"librispeech_test_clean_{parts[0]}_{parts[1]}_{parts[2]}.wav"
        src = PUB / name
        if not src.exists():
            print(f"[libri] missing {name}")
            continue
        audio, sr = sf.read(str(src), dtype="float32", always_2d=False)
        rel = f"libri/{name}"
        dest = OUT / rel
        _write_wav(dest, audio, sr)
        ref_disp = ref.lower().capitalize() + "."
        rows.append(
            {
                "id": f"libri_{i:03d}",
                "corpus": "libri",
                "path": str(dest.relative_to(ROOT)).replace("\\", "/"),
                "ref": ref_disp,
                "ref_raw": ref,
                "duration_sec": round(audio.size / float(sr), 3),
                "utterance_id": utt_id,
                "ref_source": "librispeech_official_trans",
                "ood": True,
            }
        )
    print(f"[libri] n={len(rows)} (OOD stress; not main metric)")
    return rows


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    AMI_DIR.mkdir(parents=True, exist_ok=True)
    FLEURS_DIR.mkdir(parents=True, exist_ok=True)
    LIBRI_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    rows.extend(harvest_ami(48, 3))
    fleurs = harvest_fleurs(12)
    if fleurs:
        rows.extend(fleurs)
    else:
        rows.extend(harvest_local_fleurs_libri())
    rows.extend(harvest_libri())
    man = OUT / "manifest.jsonl"
    with man.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    by: dict[str, int] = {}
    for r in rows:
        by[r["corpus"]] = by.get(r["corpus"], 0) + 1
    with_ref = sum(1 for r in rows if r.get("ref"))
    print(f"wrote {man} n={len(rows)} by={by} with_ref={with_ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
