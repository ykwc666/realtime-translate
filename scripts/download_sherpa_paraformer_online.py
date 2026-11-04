"""Download sherpa streaming Paraformer (zh-en int8) via mirrors."""
from __future__ import annotations

import socket
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "models" / "sherpa-paraformer-online"
MIRROR = "https://hf-mirror.com"

# Prefer individual files from HF mirror; fallback to release tarball mirrors
FILES = [
    (
        OUT / "encoder.int8.onnx",
        [
            f"{MIRROR}/csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en/resolve/main/encoder.int8.onnx",
            f"{MIRROR}/tintitu/sherpa-streaming-paraformer-zh-en-int8/resolve/main/encoder.int8.onnx",
        ],
        100_000_000,
    ),
    (
        OUT / "decoder.int8.onnx",
        [
            f"{MIRROR}/csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en/resolve/main/decoder.int8.onnx",
            f"{MIRROR}/tintitu/sherpa-streaming-paraformer-zh-en-int8/resolve/main/decoder.int8.onnx",
        ],
        40_000_000,
    ),
    (
        OUT / "tokens.txt",
        [
            f"{MIRROR}/csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en/resolve/main/tokens.txt",
            f"{MIRROR}/tintitu/sherpa-streaming-paraformer-zh-en-int8/resolve/main/tokens.txt",
        ],
        1000,
    ),
]

TARBALL_URLS = [
    "https://ghfast.top/https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-paraformer-bilingual-zh-en.tar.bz2",
    "https://mirror.ghproxy.com/https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-paraformer-bilingual-zh-en.tar.bz2",
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-paraformer-bilingual-zh-en.tar.bz2",
]


def _log(msg: str) -> None:
    print(msg, flush=True)


def _get(url: str, dest: Path, min_size: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    # resume if partial exists
    start = tmp.stat().st_size if tmp.exists() else 0
    headers = {"User-Agent": "realtime-translate/1.0"}
    if start > 0:
        headers["Range"] = f"bytes={start}-"
        _log(f"GET {url} (resume {start / 1e6:.1f} MB)")
    else:
        _log(f"GET {url}")

    # connect timeout short; read timeout per blocking read
    socket.setdefaulttimeout(30)
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
    except Exception:
        if start > 0 and "Range" in headers:
            # server may not support Range — restart
            _log("  resume unsupported, restart")
            if tmp.exists():
                tmp.unlink()
            start = 0
            headers.pop("Range", None)
            req = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=60)
        else:
            raise

    mode = "ab" if start > 0 else "wb"
    with resp, open(tmp, mode) as f:
        done = start
        while True:
            try:
                chunk = resp.read(1024 * 1024)
            except socket.timeout as e:
                raise TimeoutError(f"read stalled at {done / 1e6:.1f} MB") from e
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            print(f"\r  {done / 1e6:.1f} MB", end="", flush=True)
    print(flush=True)
    if tmp.stat().st_size < min_size:
        raise RuntimeError(f"too small: {tmp.stat().st_size}")
    tmp.replace(dest)
    _log(f"  saved {dest.name} ({dest.stat().st_size} bytes)")


def download_files() -> bool:
    ok = True
    for dest, urls, min_size in FILES:
        if dest.exists() and dest.stat().st_size >= min_size:
            _log(f"skip {dest.name} ({dest.stat().st_size} bytes)")
            continue
        last: Exception | None = None
        for url in urls:
            try:
                _get(url, dest, min_size)
                break
            except Exception as e:
                last = e
                _log(f"  fail: {e}")
                part = dest.with_suffix(dest.suffix + ".part")
                # keep .part for resume; only delete bad final
                if dest.exists():
                    dest.unlink()
        else:
            _log(f"MISSING {dest.name}: {last}")
            ok = False
    return ok


def download_tarball() -> bool:
    tar_path = OUT / "pkg.tar.bz2"
    OUT.mkdir(parents=True, exist_ok=True)
    last = None
    for url in TARBALL_URLS:
        try:
            _get(url, tar_path, min_size=50_000_000)
            break
        except Exception as e:
            last = e
            _log(f"tarball fail: {e}")
    else:
        _log(f"tarball missing {last}")
        return False

    _log(f"extract {tar_path}")
    with tarfile.open(tar_path, "r:bz2") as tf:
        tf.extractall(OUT)
    nested = next(OUT.glob("sherpa-onnx-streaming-paraformer*"), None)
    if nested and nested.is_dir():
        for name in ("encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt"):
            src = nested / name
            if src.exists():
                src.replace(OUT / name)
    return (OUT / "encoder.int8.onnx").exists() and (OUT / "decoder.int8.onnx").exists()


def main() -> int:
    _log(f"out={OUT}")
    if download_files():
        _log(f"PARAFORMER_ONLINE_OK {OUT}")
        return 0
    _log("file mirrors failed, try tarball…")
    if download_tarball():
        _log(f"PARAFORMER_ONLINE_OK {OUT}")
        return 0
    _log("PARAFORMER_ONLINE_FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
