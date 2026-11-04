"""Download sherpa-onnx GTCRN speech enhancement (gtcrn_simple.onnx ≈523KB)."""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "models" / "gtcrn_simple.onnx"
URLS = [
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/speech-enhancement-models/gtcrn_simple.onnx",
    "https://mirror.ghproxy.com/https://github.com/k2-fsa/sherpa-onnx/releases/download/speech-enhancement-models/gtcrn_simple.onnx",
    "https://ghfast.top/https://github.com/k2-fsa/sherpa-onnx/releases/download/speech-enhancement-models/gtcrn_simple.onnx",
]
MIN_BYTES = 100_000


def main() -> int:
    if DEST.exists() and DEST.stat().st_size >= MIN_BYTES:
        print(f"skip {DEST} ({DEST.stat().st_size} bytes)")
        return 0
    DEST.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for url in URLS:
        try:
            print(f"GET {url}")
            req = urllib.request.Request(url, headers={"User-Agent": "realtime-translate/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp, DEST.open("wb") as f:
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
            size = DEST.stat().st_size
            if size < MIN_BYTES:
                raise RuntimeError(f"too small: {size}")
            print(f"ok {DEST} ({size / 1024:.1f} KB)")
            return 0
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(f"fail: {exc}")
            DEST.unlink(missing_ok=True)
    print(f"download failed: {last}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
