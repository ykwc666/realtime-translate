from __future__ import annotations

import argparse
import sys

from app.config import setup_env


def main(argv: list[str] | None = None) -> int:
    setup_env()
    parser = argparse.ArgumentParser(description="中英半双工实时翻译（本地 CPU）")
    parser.add_argument(
        "--mode",
        choices=["ptt", "vad", "file", "dualsim", "loopback", "duplex"],
        default="ptt",
        help=(
            "ptt=按Enter; vad=麦→英; loopback=环回对端→中; "
            "duplex=麦+环回; file/dualsim=离线"
        ),
    )
    parser.add_argument("--file", type=str, default="", help="file/dualsim 的 wav 路径")
    parser.add_argument("--no-tts", action="store_true", help="只识别+翻译，不播报")
    parser.add_argument(
        "--list-loopback",
        action="store_true",
        help="列出 WASAPI 环回设备后退出",
    )
    parser.add_argument(
        "--list-output",
        action="store_true",
        help="列出播放设备（供 TTS_OUTPUT_DEVICE_NAME）后退出",
    )
    parser.add_argument(
        "--harvest",
        action="store_true",
        help="会话采集到 data/harvest/*.jsonl（见 app/harvest.py 契约）",
    )
    parser.add_argument(
        "--source-desc",
        type=str,
        default="",
        help="--harvest 时必填：音源描述（写入每行）",
    )
    args = parser.parse_args(argv)

    if args.list_loopback:
        from app.audio_io import list_loopback_devices

        for name in list_loopback_devices():
            print(name)
        return 0
    if args.list_output:
        from app.audio_io import list_output_devices

        for idx, name, ch in list_output_devices():
            print(f"{idx}\t{ch}ch\t{name}")
        return 0

    harvest = None
    if args.harvest:
        if not args.source_desc.strip():
            print("--harvest 需要 --source-desc", file=sys.stderr)
            return 2
        from app.harvest import HarvestWriter, next_harvest_path

        harvest = HarvestWriter(
            next_harvest_path(), mode=args.mode, source_desc=args.source_desc.strip()
        )

    # 实时模式：只允许单进程
    if args.mode in ("vad", "loopback", "duplex", "ptt"):
        from app.single_instance import acquire_run_lock

        if not acquire_run_lock(args.mode):
            if harvest is not None:
                harvest.close()
            return 3

    from app.pipeline import TranslatePipeline

    try:
        pipe = TranslatePipeline(enable_tts=not args.no_tts, harvest=harvest)

        if args.mode == "file":
            if not args.file:
                print("file 模式需要 --file path.wav", file=sys.stderr)
                return 2
            pipe.run_file(args.file, play=not args.no_tts)
        elif args.mode == "dualsim":
            if not args.file:
                print("dualsim 模式需要 --file path.wav", file=sys.stderr)
                return 2
            pipe.run_dualsim(args.file, play=not args.no_tts)
        elif args.mode == "vad":
            pipe.run_vad()
        elif args.mode == "loopback":
            pipe.run_loopback()
        elif args.mode == "duplex":
            pipe.run_duplex()
        else:
            pipe.run_ptt()
        return 0
    finally:
        if harvest is not None:
            harvest.close()


if __name__ == "__main__":
    raise SystemExit(main())
