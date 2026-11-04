"""Download sherpa-onnx metadata-patched ten-vad.onnx (not upstream TEN raw)."""
from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "models" / "ten-vad.onnx"
URLS = [
    "https://ghproxy.net/https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/ten-vad.onnx",
    "https://ghfast.top/https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/ten-vad.onnx",
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/ten-vad.onnx",
]


def main() -> int:
    DEST.parent.mkdir(parents=True, exist_ok=True)
    if DEST.exists() and DEST.stat().st_size > 50_000:
        print(f"skip {DEST} ({DEST.stat().st_size} bytes)")
        return 0
    last: Exception | None = None
    for url in URLS:
        tmp = DEST.with_suffix(".onnx.part")
        try:
            print(f"GET {url}")
            req = urllib.request.Request(url, headers={"User-Agent": "realtime-translate/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as f:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            if tmp.stat().st_size < 50_000:
                raise RuntimeError(f"too small: {tmp.stat().st_size}")
            tmp.replace(DEST)
            print(f"ok {DEST} ({DEST.stat().st_size} bytes)")
            return 0
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(f"fail: {exc}")
            if tmp.exists():
                tmp.unlink(missing_ok=True)
    print(f"download failed: {last}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
