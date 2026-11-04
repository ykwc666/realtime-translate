"""Download Piper zh/en voices via hf-mirror plain HTTP."""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOICE_DIR = ROOT / "models" / "piper"
MIRROR = "https://hf-mirror.com/rhasspy/piper-voices/resolve/main"

# path relative to piper-voices repo root
VOICES = {
    "zh": (
        "zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx",
        "zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx.json",
    ),
    "en": (
        "en/en_US/lessac/medium/en_US-lessac-medium.onnx",
        "en/en_US/lessac/medium/en_US-lessac-medium.onnx.json",
    ),
}


def download_file(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 1024:
        print(f"  skip: {dest.name} ({dest.stat().st_size} bytes)")
        return
    print(f"  GET {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "realtime-translate/1.0"})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=300) as resp, open(tmp, "wb") as f:
        total = resp.headers.get("Content-Length")
        total_i = int(total) if total else None
        done = 0
        while True:
            chunk = resp.read(1024 * 512)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total_i:
                print(f"\r  {dest.name}: {done * 100 // total_i}%", end="", flush=True)
        print()
    if total_i and tmp.stat().st_size != total_i:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"size mismatch {dest.name}")
    tmp.replace(dest)


def main() -> int:
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    for lang, (onnx_rel, json_rel) in VOICES.items():
        print(f"== {lang}")
        for rel in (onnx_rel, json_rel):
            name = Path(rel).name
            download_file(f"{MIRROR}/{rel}", VOICE_DIR / name)
    print("PIPER_VOICES_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
