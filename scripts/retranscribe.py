"""薄封装：python scripts/retranscribe.py latest|path"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import setup_env  # noqa: E402
from app.retranscribe import retranscribe_session  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    setup_env()
    ap = argparse.ArgumentParser(description="会后重译（SenseVoice → MT → 双语稿）")
    ap.add_argument("session", nargs="?", default="latest", help="latest 或会话目录")
    ap.add_argument("--live", type=str, default="", help="覆盖 live.jsonl 路径")
    ap.add_argument("--src-lang", type=str, default="", help="强制源语 zh|en")
    args = ap.parse_args(argv)
    retranscribe_session(
        args.session,
        live_path=args.live.strip() or None,
        force_src_lang=args.src_lang.strip() or None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
