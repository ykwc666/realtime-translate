# 给 Cursor 的任务提示词：TTS 真实首音与输出流开销

用法：工作区打开 `D:\xuanjian\realtime-translate`，把下面整段复制进 Cursor 对话框。
目标：先量准“首个 PCM 就绪”和“设备真正开始播放”的差别，再决定是否复用输出流。

---

任务：诊断并消除 TTS 每句播报打开/关闭 OutputStream 的额外开销。先测量，达到门槛才允许改播放架构；不要顺手调整 ASR、VAD 或 MT。

## 0. 边界

- 当前目录是真实仓库 D:\xuanjian\realtime-translate，解释器用 .venv\Scripts\python.exe。
- 工作区有其他未提交改动，不要回退、格式化或提交无关文件。
- 本任务只允许改 app/tts_play.py、app/tts_queue.py（确有必要时）、scripts/bench_tts_stream_ab.py、相关测试和新文档。
- 不新增依赖，不 commit，不改 Piper 模型和音色。

## 1. 已知线索

app/tts_play.py 当前有两条播放路径：

- play_array()：每次新建 sd.OutputStream，播完关闭。
- _speak_clauses()：多子句时也每次新建 OutputStream。

现有 docs/tts_stream_ab.jsonl 的 batch 数据中，speak_wall_ms 减去 synth_or_wall_ms 和 play_sec×1000 后，
每句仍有约 154–379ms 差额。先核对 play_sec 是否已包含 TTS_PLAY_TAIL_PAD_SEC；
只有确认口径后才能把差额归因于设备流开关/回调/关闭，不要直接下结论。

另外，TtsResult.ttfa_ms 目前表示“首个 PCM 可交给设备”，不是首个设备回调实际写出非零样本。
这两个指标必须分开，不能混称首音。

## 2. 第一阶段：补齐真实时间戳与基线

扩展 scripts/bench_tts_stream_ab.py 和必要的 TTS 内部元数据，分别记录：

- ttfa_pcm_ms：speak 开始 → 第一段非空合成 PCM 就绪。
- stream_open_ms：打开输出流耗时；流复用时记首次与后续每次播报。
- ttfa_callback_ms：speak 开始 → OutputStream callback 第一次实际写出本句非零 PCM。
- close_or_drain_ms：最后样本写出 → 播放调用完全返回。
- expected_play_sec（含 tail pad）、actual_callback_play_sec、under-run 次数、异常/状态。

要求：

1. 兼容保留现有 ttfa_ms 字段含义；新增字段，不要静默改变旧数据口径。
2. 回调里只做时间戳、拷贝和计数，不打印、不阻塞、不拿锁。
3. 英文/中文、单句/多子句都覆盖，沿用现有 PHRASES。
4. 每种模式至少 reps=3，顺序交错，避免把首轮设备初始化当成每句固定成本。
5. 实际使用本机输出设备跑，不能仅用 --no-play 推断设备首音。

如果确认“每句额外开销”中位数 <80ms，且 PCM 就绪到首次设备回调也 <80ms，
停止第二阶段，只提交测量结果，不做输出流复用重构。

## 3. 第二阶段：可选的持久输出流

只有第一阶段证明开销显著时才做。

1. 新增 TTS_PERSISTENT_OUTPUT 开关；False 时必须完全走现有路径。
2. TtsPlayer 持有按 output_device + sample_rate 创建的持久 OutputStream，warmup 或首次 speak 时打开，
   后续播报复用；sample_rate/device 改变时才安全重建。提供明确 close()，进程退出不能挂住。
3. 持久流回调从 PCM 队列取数据；无数据时补零，不能 busy-wait、不能在回调里阻塞。
4. 保留现有多子句“合成第一段就开播、后续子句并行合成”和 TtsQueue 预取收益。
5. stop()/invalidate 改为只作废当前 TTS 代次并清空待播 PCM，尽快输出静音；
   不要为了停 TTS 调用全局 sd.stop() 影响其他流。记录打断延迟。
6. 保留 TTS_PLAY_TAIL_PAD_SEC，不能因流复用截掉尾音节。
7. 多线程只在一个 TtsPlayer 内串行播放，现有 _io_lock 语义不变。

## 4. A/B 与验收

固定同一批文本，对 persistent off/on 做交错 A/B/A/B，至少两轮。输出逐句表和 mean/median/p90/max：

- ttfa_pcm、ttfa_callback、stream_open、close_or_drain、speak_wall。
- 实际回调播放样本数 vs 预期样本数，under-run 次数。
- stop/invalidate 后到停止非零输出的延迟。

验收条件：

- ttfa_callback 或每句收尾开销有明确改善；若只改善 PCM 时间而设备首音没改善，不算通过。
- 所有文本的实际播放样本数符合预期（含 tail pad），无截尾、无串句、无上一句残留。
- 多子句 TTS_STREAM_PLAY 的收益不能丢。
- 打断应在一个较小的回调周期量级内生效；若出现sd.stop全局副作用或设备流无法恢复，默认关闭 persistent。
- 跑 .venv\Scripts\python.exe -m pytest tests -q，记录真实通过数；补时间戳、开关回退和打断测试。
- 保存合成 PCM 供复查，不要仅凭“没有报错”就宣称听感/尾音正常；列出需要人工试听的项目。

## 5. 交付

1. 基线时间戳表，解释原来 154–379ms 差额到底是什么。
2. persistent off/on 的真实设备 A/B 表。
3. 最小 diff 和测试结果。
4. 是否建议默认开启；若收益不足或稳定性不够，默认 False。
5. 明确说明：现在能否把 DualTrackStats.t_first_audio_ms 当真实首音？若不能，列出接入真实回调还缺哪一步。
