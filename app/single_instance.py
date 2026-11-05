"""防止多个 run.py VAD/双工实例（回声 / 重复 TTS）。"""
from __future__ import annotations

import atexit
import sys
from pathlib import Path
from typing import BinaryIO

from . import config

_lock_fh: BinaryIO | None = None


def acquire_run_lock(label: str = "run") -> bool:
    """对 cache/run.lock 非阻塞独占锁；忙则返回 False。"""
    global _lock_fh
    if not bool(getattr(config, "SINGLE_INSTANCE_LOCK", True)):
        return True
    if _lock_fh is not None:
        return True
    path = Path(config.CACHE_DIR) / "run.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+b")  # noqa: SIM115
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            if fh.read(1) == b"":
                fh.write(b"0")
                fh.flush()
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        print(
            f"已有实例在跑（{path}）。同时只能一个 {label}；"
            "若残留锁，关掉旧进程后重试。",
            file=sys.stderr,
        )
        return False
    _lock_fh = fh
    atexit.register(release_run_lock)
    return True


def release_run_lock() -> None:
    global _lock_fh
    fh = _lock_fh
    _lock_fh = None
    if fh is None:
        return
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        fh.close()
    except OSError:
        pass
