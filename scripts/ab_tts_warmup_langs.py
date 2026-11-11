"""A/B: TTS warmup both (zh+en) vs en-only.

Gate: en-only median warmup ≤ both median − 5.0s.
Also verify zh lazy-load still works after en-only warmup.
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402

setup_env()

from app.tts_play import TtsPlayer  # noqa: E402


def _median(xs: list[float]) -> float:
    return float(statistics.median(xs))


def _run_arm(langs: tuple[str, ...], rounds: int) -> list[float]:
    times: list[float] = []
    for i in range(rounds):
        tts = TtsPlayer()
        t0 = time.perf_counter()
        costs = tts.warmup(langs)
        wall = time.perf_counter() - t0
        times.append(wall)
        detail = " ".join(f"{k}={costs.get(k, 0):.0f}ms" for k in langs)
        print(f"  [{'+'.join(langs)}] round {i + 1}: wall={wall:.2f}s ({detail})")
        tts.close()
    return times


def main() -> int:
    rounds = 3
    print("=== arm A: zh+en ===")
    both = _run_arm(("zh", "en"), rounds)
    print("=== arm B: en-only ===")
    en_only = _run_arm(("en",), rounds)

    med_both = _median(both)
    med_en = _median(en_only)
    delta = med_both - med_en
    print(
        f"\nmedian both={med_both:.2f}s  en-only={med_en:.2f}s  "
        f"delta={delta:.2f}s (need en≤both−5.0 → delta≥5.0)"
    )

    # lazy-load zh after en-only（对照：冷路径）
    tts = TtsPlayer()
    tts.warmup(("en",))
    t1 = time.perf_counter()
    _ = tts.synthesize("测试", "zh", save_wav=False)
    zh_lazy = (time.perf_counter() - t1) * 1000
    tts.close()
    print(f"zh lazy-load after en-only: {zh_lazy:.0f}ms (ok if no exception)")

    # bg preload + wait：开口路径不应再付一次完整加载
    tts = TtsPlayer()
    tts.warmup(("en",))
    tts.preload_background(("zh",))
    assert tts.wait_preload(timeout=120.0)
    t2 = time.perf_counter()
    _ = tts.synthesize("测试", "zh", save_wav=False)
    zh_after_bg = (time.perf_counter() - t2) * 1000
    tts.close()
    print(f"zh synthesize after bg preload: {zh_after_bg:.0f}ms")

    ok = med_en <= (med_both - 5.0) and zh_after_bg < max(1500.0, zh_lazy * 0.35)
    print(f"GATE: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
