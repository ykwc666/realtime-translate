from __future__ import annotations

import time as _time

PROCESS_T0 = _time.perf_counter()  # 进程内最早可用时刻

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
    parser.add_argument(
        "--no-record",
        action="store_true",
        help="关闭会后旁路录音（默认 vad/duplex 开启）",
    )
    parser.add_argument(
        "--post",
        type=str,
        default="",
        help="会后重译：latest 或 data/sessions/<id> 路径",
    )
    parser.add_argument(
        "--live",
        type=str,
        default="",
        help="会后对照用的 live/harvest jsonl（默认 session/live.jsonl）",
    )
    parser.add_argument(
        "--subtitle",
        action="store_true",
        help="开启双语字幕状态写盘（默认 data/live/subtitle.json）",
    )
    parser.add_argument(
        "--subtitle-path",
        type=str,
        default="",
        help="字幕 JSON 路径（配合 --subtitle；默认 data/live/subtitle.json）",
    )
    parser.add_argument(
        "--subtitle-serve",
        action="store_true",
        help="本机起字幕页 http://127.0.0.1:8765/（隐含开启 --subtitle）",
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

    # 会后重译：不占实时锁、不启麦
    if args.post.strip():
        from app.retranscribe import retranscribe_session

        retranscribe_session(
            args.post.strip(),
            live_path=args.live.strip() or None,
        )
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

    print("[boot] 开始导入依赖 …", flush=True)
    from app.mic_probe import MicProbe
    from app.pipeline import TranslatePipeline

    session_recorder = None
    if args.mode in ("vad", "duplex", "dualsim") and not args.no_record:
        from app.session_record import SessionRecorder

        session_recorder = SessionRecorder(
            mode=args.mode,
            src_lang="zh",
            tgt_lang="en",
        )

    if args.subtitle_serve:
        args.subtitle = True

    subtitle_writer = None
    subtitle_httpd = None
    if args.subtitle:
        from app.subtitle import DEFAULT_SUBTITLE_PATH, SubtitleWriter

        sp = args.subtitle_path.strip()
        sub_path = sp if sp else str(DEFAULT_SUBTITLE_PATH)
        subtitle_writer = SubtitleWriter(sub_path)
        if args.subtitle_serve:
            from scripts.subtitle_server import serve_in_thread

            subtitle_httpd, _ = serve_in_thread(state_path=subtitle_writer.path)

    pipe = None
    try:
        pipe = TranslatePipeline(
            enable_tts=not args.no_tts,
            harvest=harvest,
            mic_probe=MicProbe(),
            session_recorder=session_recorder,
            subtitle_writer=subtitle_writer,
            process_t0=PROCESS_T0,
        )

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
        if pipe is not None:
            pipe.close()
        if subtitle_httpd is not None:
            try:
                subtitle_httpd.shutdown()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    raise SystemExit(main())
