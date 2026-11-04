"""Download sherpa-onnx CT-Transformer zh-en punctuation (int8).

Progress prints every MB. Prefers direct GitHub (use VPN if needed), then mirrors.
Resumable via cache/punct_dl.tar.bz2.
"""
from __future__ import annotations

import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST_DIR = ROOT / "models" / "sherpa-punct-ct-zh-en"
MODEL_NAME = "model.int8.onnx"
ARCHIVE = "sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8.tar.bz2"
# Release asset ≈ 61.7 MB compressed
EXPECTED_MIN_BYTES = 50_000_000
CACHE_TAR = ROOT / "cache" / "punct_dl.tar.bz2"
URLS = [
    # Direct first — user VPN / 梯子
    f"https://github.com/k2-fsa/sherpa-onnx/releases/download/punctuation-models/{ARCHIVE}",
    f"https://mirror.ghproxy.com/https://github.com/k2-fsa/sherpa-onnx/releases/download/punctuation-models/{ARCHIVE}",
    f"https://ghproxy.net/https://github.com/k2-fsa/sherpa-onnx/releases/download/punctuation-models/{ARCHIVE}",
    f"https://ghfast.top/https://github.com/k2-fsa/sherpa-onnx/releases/download/punctuation-models/{ARCHIVE}",
]


def _p(msg: str) -> None:
    print(msg, flush=True)


def _download(url: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = out.stat().st_size if out.exists() else 0
    headers = {"User-Agent": "realtime-translate/1.0"}
    mode = "wb"
    if existing > 0:
        headers["Range"] = f"bytes={existing}-"
        mode = "ab"
        _p(f"resume from {existing / (1 << 20):.1f} MB")

    req = urllib.request.Request(url, headers=headers)
    # connect + read timeouts (seconds)
    with urllib.request.urlopen(req, timeout=120) as resp, out.open(mode) as f:
        # 206 = partial content; 200 = full (ignore Range)
        if existing > 0 and getattr(resp, "status", 200) == 200:
            _p("server ignored Range — restarting full download")
            f.close()
            out.unlink(missing_ok=True)
            with out.open("wb") as f2:
                _stream_copy(resp, f2, start=0)
            return
        _stream_copy(resp, f, start=existing)


def _stream_copy(resp, f, *, start: int) -> None:  # noqa: ANN001
    n = int(start)
    last_mb = n // (1 << 20)
    total = resp.headers.get("Content-Length")
    # When resuming, Content-Length is remaining bytes
    total_hint = ""
    if total and total.isdigit():
        end = n + int(total) if start else int(total)
        total_hint = f" / ~{end / (1 << 20):.0f} MB"
    while True:
        chunk = resp.read(256 * 1024)
        if not chunk:
            break
        f.write(chunk)
        n += len(chunk)
        mb = n // (1 << 20)
        if mb > last_mb:
            last_mb = mb
            _p(f"  {n / (1 << 20):.1f} MB{total_hint}")
    f.flush()
    _p(f"  download done {n / (1 << 20):.1f} MB")


def _extract(tar_path: Path, dest: Path) -> None:
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    _p(f"extract {tar_path.name} …")
    with tarfile.open(tar_path, "r:bz2") as tf:
        tf.extractall(DEST_DIR.parent / "_punct_extract")
    extract_root = DEST_DIR.parent / "_punct_extract"
    found = list(extract_root.rglob(MODEL_NAME))
    if not found:
        raise FileNotFoundError(MODEL_NAME)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    found[0].replace(dest)
    # cleanup extract tree
    import shutil

    shutil.rmtree(extract_root, ignore_errors=True)
    _p(f"ok {dest} ({dest.stat().st_size / (1 << 20):.1f} MB)")


def main() -> int:
    dest = DEST_DIR / MODEL_NAME
    if dest.exists() and dest.stat().st_size > 1_000_000:
        _p(f"skip {dest} ({dest.stat().st_size} bytes)")
        return 0

    last: Exception | None = None
    for url in URLS:
        try:
            _p(f"GET {url}")
            _download(url, CACHE_TAR)
            size = CACHE_TAR.stat().st_size
            if size < EXPECTED_MIN_BYTES:
                raise RuntimeError(f"archive too small: {size} (need ≥{EXPECTED_MIN_BYTES})")
            _extract(CACHE_TAR, dest)
            return 0
        except Exception as exc:  # noqa: BLE001
            last = exc
            _p(f"fail: {exc}")
            # keep partial CACHE_TAR for resume on next URL/attempt
    _p(f"download failed: {last}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
