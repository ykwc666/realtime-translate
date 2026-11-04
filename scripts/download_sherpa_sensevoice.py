"""Download sherpa SenseVoice int8 + Silero VAD via hf-mirror (国内)."""
from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "models" / "sherpa-sensevoice"
VAD_OUT = ROOT / "models" / "silero_vad.onnx"
MIRROR = "https://hf-mirror.com"

# 优先 hf-mirror 直链（避开 GitHub / xet）
FILES = [
    (
        OUT / "model.int8.onnx",
        [
            f"{MIRROR}/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/model.int8.onnx",
            f"{MIRROR}/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09/resolve/main/model.int8.onnx",
        ],
    ),
    (
        OUT / "tokens.txt",
        [
            f"{MIRROR}/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/tokens.txt",
            f"{MIRROR}/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09/resolve/main/tokens.txt",
        ],
    ),
    (
        VAD_OUT,
        [
            f"{MIRROR}/csukuangfj/vad/resolve/main/silero_vad.onnx",
            f"{MIRROR}/onnx-community/silero-vad/resolve/main/onnx/model.onnx",
            # 末选：GitHub 国内加速
            "https://ghfast.top/https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx",
            "https://mirror.ghproxy.com/https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx",
        ],
    ),
]


def download_one(urls: list[str], dest: Path, min_size: int = 1000) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size >= min_size:
        print(f"skip {dest.name} ({dest.stat().st_size} bytes)")
        return

    last_err: Exception | None = None
    for url in urls:
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            print(f"GET {url}")
            # HEAD for size (best-effort)
            expected = None
            try:
                head = urllib.request.Request(
                    url, method="HEAD", headers={"User-Agent": "realtime-translate/1.0"}
                )
                with urllib.request.urlopen(head, timeout=30) as resp:
                    cl = resp.headers.get("Content-Length")
                    expected = int(cl) if cl else None
            except Exception:
                expected = None

            start = 0
            mode = "wb"
            headers = {"User-Agent": "realtime-translate/1.0"}
            if tmp.exists() and expected and 0 < tmp.stat().st_size < expected:
                start = tmp.stat().st_size
                mode = "ab"
                headers["Range"] = f"bytes={start}-"
                print(f"  resume from {start}")

            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=300) as resp, open(tmp, mode) as f:
                total_i = expected
                done = start
                while True:
                    chunk = resp.read(1024 * 512)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if total_i:
                        print(f"\r  {dest.name}: {min(100, done * 100 // total_i)}%", end="", flush=True)
                print()

            if expected and tmp.stat().st_size != expected:
                raise RuntimeError(f"size {tmp.stat().st_size} != {expected}")
            if tmp.stat().st_size < min_size:
                raise RuntimeError(f"too small: {tmp.stat().st_size}")
            tmp.replace(dest)
            print(f"ok {dest} ({dest.stat().st_size} bytes)")
            return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"  fail: {exc}")
            time.sleep(1)
    raise RuntimeError(f"all mirrors failed for {dest.name}: {last_err}")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    # clean incomplete github archive leftovers if any
    junk = ROOT / "cache" / "sherpa-sensevoice.tar.bz2.part"
    if junk.exists():
        junk.unlink()

    download_one(FILES[0][1], FILES[0][0], min_size=50_000_000)
    download_one(FILES[1][1], FILES[1][0], min_size=10_000)
    download_one(FILES[2][1], FILES[2][0], min_size=100_000)
    print("SHERPA_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
