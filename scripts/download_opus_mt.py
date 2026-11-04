"""Download Opus-MT via hf-mirror plain HTTP (skip xet / unused formats)."""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
MIRROR = "https://hf-mirror.com"

# MarianMT 推理只需这些；跳过 tf/rust 大文件
NEEDED = [
    "config.json",
    "generation_config.json",
    "pytorch_model.bin",
    "source.spm",
    "target.spm",
    "tokenizer_config.json",
    "vocab.json",
]

REPOS = {
    "opus-mt-zh-en": "Helsinki-NLP/opus-mt-zh-en",
    "opus-mt-en-zh": "Helsinki-NLP/opus-mt-en-zh",
}


def download_file(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  skip exists: {dest.name} ({dest.stat().st_size} bytes)")
        return
    print(f"  GET {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "realtime-translate/1.0"})
    # 直接写目标文件，避免 Windows 上 .part rename 被占用
    with urllib.request.urlopen(req, timeout=180) as resp, open(dest, "wb") as f:
        total = resp.headers.get("Content-Length")
        total_i = int(total) if total else None
        done = 0
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total_i:
                pct = done * 100 // total_i
                print(f"\r  {dest.name}: {pct}% ({done}/{total_i})", end="", flush=True)
        print()
    if total_i and dest.stat().st_size != total_i:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"size mismatch for {dest.name}: {dest.stat().st_size if dest.exists() else 0} != {total_i}")


def main() -> int:
    for name, repo in REPOS.items():
        local = MODELS / name
        local.mkdir(parents=True, exist_ok=True)
        print(f"== {repo} -> {local}")
        for fname in NEEDED:
            url = f"{MIRROR}/{repo}/resolve/main/{fname}"
            download_file(url, local / fname)
        print(f"  done: {name}")
    print("OPUS_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
