# 给 Cursor 的任务提示词：二遍 ASR 并行无收益（线程争抢）

用法：在 Cursor 里把工作区切到 `D:\xuanjian\realtime-translate`（真实目录，`.venv` / `models` / `cache` 都在），
把下面整段复制进对话框。多行没问题（之前单行限制只针对 headless CLI）。
跑完把它的数值表和结论拿回来，再决定下一刀。

---

任务：查清二遍 ASR 并行为何没有收益，并用体感口径验证修正。只做这一件事，不要顺手优化别的。

## 0. 环境与规矩

- 当前目录就是真实仓库 D:\xuanjian\realtime-translate，可以直接运行。
- 解释器统一用 .venv\Scripts\python.exe。
- 基线是 git 提交 f15b53f，里面已经包含 ASR_2PASS_PARALLEL 并行补丁。不要 commit，改完留在工作区。
- 不要新增任何依赖，不要装包。

## 1. 已知事实（不用重复验证，直接接着做）

收句关键路径上有两次解码：流式 Paraformer 的 oneshot 重解码 + 离线 SenseVoice 重解码，
结果用 prefer_2pass_asr 合并。入口在 app/asr_stream.py：

- transcribe_with_2pass()（约 302 行，投机定稿走这里）
- finalize_stream()（约 319 行，正常收句走这里）
- 两者共用 _run_2pass_pair()，ASR_2PASS_PARALLEL=False 时回到串行

上一轮把它们改成并行后，实测没有净收益：6 句真实音频，串行中位 611ms，并行中位 619ms，
文本六句完全一致。关键线索是并行后每一路自己的耗时都涨了约 1.6 倍
（719→972ms、638→1137ms），也就是两路并发反而互相拖慢。

怀疑原因：app/config.py 的 ASR_THREADS = 6 同时喂给了两个 recognizer
（app/asr_stream.py 第 180 行 SenseVoice、第 201 行 Online Paraformer），
两路并行就是 12 个 ORT 线程在抢核。请先自己查一下本机物理核/逻辑核数，把这个判断坐实或推翻。

## 2. 代码改动（只允许这两处）

1. app/config.py：把线程数拆成两个独立项 ASR_THREADS_ONLINE（流式 Paraformer）和
   ASR_THREADS_OFFLINE（SenseVoice），默认都等于现在的 ASR_THREADS（6），默认行为完全不变。
   ASR_THREADS 保留作兜底。加注释说明：并行解码时两路会抢核，需要分别压。
2. app/asr_stream.py：_init_sherpa_paraformer_online() 改用 ONLINE，_init_sherpa_sensevoice() 改用 OFFLINE。

除此之外不动任何生产代码。

## 3. 新建微基准 scripts/bench_2pass_ab.py

- 用 app.audio_io.load_wav 读 cache\ab_weekday\accept_phrases.wav 和 cache\ab_weekday\tail_duosimo.wav，
  用 app.vad_stream.segment_audio 切段。
- 每组 (threads_online, threads_offline, parallel) 重新构造 StreamingAsr（线程数只在 __init__ 里读），
  每段跑 --reps 次（默认 3），记录 finalize/transcribe_with_2pass 的墙钟中位数与最小值，
  以及返回 AsrResult.ms（两路耗时之和）。
- 校验并行与串行文本是否逐段一致，不一致必须打印出来。
- 命令行支持 --threads 多个值（如 6 4 3 2）、--reps、--parallel on|off|both。
- 输出 markdown 表：threads / mode / 中位墙钟 / 最小 / model ms 之和。

## 4. 实验顺序（每组原始输出都要贴回来）

1. 先跑 .venv\Scripts\python.exe -m pytest tests -q，确认仍是 24 passed。
2. 微基准：--threads 6 4 3 2 --reps 3 --parallel both。
3. 用体感口径复核最优组合：scripts/bench_paced.py --spec on --silence 0.45 --tag <标签>，
   分别在「串行 6 线程」和「并行 + 最优线程数」下各跑一次，对比 latency_perceived_ms。
   这是真正的判据，微基准只是定位手段。
4. 只有数据支持时才改默认值：把 ASR_THREADS_ONLINE / ASR_THREADS_OFFLINE 的默认设成最优组合，
   并写清依据（哪张表哪一列）。

## 5. 硬约束

- 不改 prefer_2pass_asr / merge_overlap_text / take_speakable 等任何字符串启发式。
- 不改 ms = online.ms + offline.ms 的记账方式，历史数据要可比。
- 不碰 VAD、翻译、TTS、UI。
- 不做无关重构、不改格式、不重命名既有符号。
- 如果怎么调线程并行都没有净收益，直说「并行该关」，把 ASR_2PASS_PARALLEL 默认改回 False 并给出依据，
  不要为了留这个功能硬找理由。

## 6. 交付

1. 两个文件的 diff（应该很小）。
2. 微基准完整表 + bench_paced 的对照数字。
3. 一句话结论：并行在每路几线程时才有净收益，或者为什么该关。
4. 单独回答：调小 ASR_THREADS_ONLINE 会不会影响说话期间流式 partial 的耗时（feed_stream 每块 ms）？
   这个路径在说话期间一直在跑，你测了没有，数据是多少。
