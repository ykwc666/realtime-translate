# 给 Cursor 的任务提示词：持久流稳定性门槛 + 端到端真实首音

用法：工作区打开 `D:\xuanjian\realtime-translate`，把下面整段复制进 Cursor 对话框。
目标：保留已经验证的持久流收益，但先证明打断/续播稳定，再把设备 callback 首音接入完整链路；本轮不预设新的延迟优化。

---

任务：把 TTS 的真实设备首音接入 DualTrackStats 和 paced bench，并补齐持久 OutputStream 的打断后续播验收。先修正确性与计时口径，不要顺手优化 ASR、VAD、MT 或 TTS 参数。

## 0. 环境与边界

- 当前目录是真实仓库 D:\xuanjian\realtime-translate，解释器统一用 .venv\Scripts\python.exe。
- 工作区有大量其他未提交改动。不要回退、格式化、提交或改写无关文件，不要 commit。
- 本任务只允许改：app/tts_play.py、app/tts_queue.py、app/dual_track.py、scripts/bench_paced.py、相关测试，以及新建本任务文档/JSONL。
- 不新增依赖，不改模型，不改 ASR_2PASS_PARALLEL、ASR_THREADS_ONLINE/OFFLINE、ASR_SPECULATE_ADAPTIVE_LEAD、VAD_SILENCE_SEC、MT、TTS 音色等默认值。
- TTS_PERSISTENT_OUTPUT 当前默认 True，保持默认；只有发现无法低-risk 修复的正确性问题才建议关闭，并必须给真实复现数据。

## 1. 已确认的事实与当前缺口

上一轮真实设备 A/B 已证明持久流有收益：

- ttfa_callback 中位约 -69ms；speak_wall 中位约 -319ms。
- open ≈29ms，close/drain ≈219ms，是原 oneshot 的主要额外成本。
- 当时 A/B 报 underrun=0，全量 53 passed。
- 详见 docs/TTS_PERSISTENT_OUTPUT_AB.md；不要重复论证是否该复用流。

但还有三个缺口：

1. app/dual_track.py 的 _translate_final() 在译文入队前就设置 t_first_audio_ms 和 latency_perceived_ms；它们实际是“停说到译文就绪”，不是设备首音。
2. scripts/bench_paced.py 固定 TranslatePipeline(enable_tts=False) 和 DualTrackEngine(play=False)，所以 paced 数据完全没有覆盖 TTS。
3. PlayTiming.ttfa_callback_ms 是相对单次 speak/play 的值，而且通常等播放返回后才能读到。FINALIZE_WAIT_TTS=False 时，DualTrackEngine 已经 reset_utt，晚到的 TTS 事件可能写错句或丢失。TtsQueue 的 prefetch 路径也不能只从 play_array 开始重新算用户首音。

本轮主指标必须新增为“停说到设备第一次写出本句非零 PCM”，旧指标保留用于历史对照。

## 2. 第一阶段：持久流正确性门槛（先于接统计）

先写测试并真实运行，确认下面两个风险是否存在；不要仅凭阅读代码宣布有或没有 bug。

### A. 打断后下一句必须完整播放

当前 stop() 会向持久 PCM 队列放入 None 哨兵。重点验证旧哨兵、旧 PCM 或旧 callback 是否可能影响下一次播放。

新增真实设备回归测试/脚本，至少覆盖：

1. 播放一段约 0.8–1.2s、含非零样本的音频，在 100–150ms 时 stop()。
2. 立即播放下一句短音频，再连续播放第三句。
3. 上述“长句打断→短句→短句”循环至少 20 次，persistent on 跑；persistent off 作对照。
4. 对每个未被打断的新句断言：first callback 有有效时间戳、实际写出样本数符合预期（含 tail pad）、没有提前结束、没有上一句残留、没有串句。
5. stop 本身仍需快速返回；不能用全局 sd.stop() 修问题。

如果复现失败，用最小改动修复队列/代次生命周期。可以考虑给 PCM 与 sentinel 都附代次，或其他等价方案，但 callback 内仍不得阻塞、打印或拿锁。不要大范围重写播放架构。

### B. 空闲回调不能污染 underrun

持久流无数据补零是正常空闲，不应和“本句播放中供数不足”混为一谈。

- 明确定义 active underrun：只在本句处于 active、仍需 PCM 时计数。
- 流创建后尚未开播、上一句 sentinel 之后、下一句尚未提交时的空闲补零，不计入本句 underrun；如需要可另记 idle_callback/starvation 计数。
- 播放结束后空闲至少 300ms，再播放下一句；预载单 buffer 的新句 active underrun 应为 0。
- 多子句场景仍要保留真实供数不足的计数，不能为了全 0 直接删除指标。

同时检查异常回退：sd.play 阻塞回退无法提供真实 callback 首音时，对应字段应为 None/未知，不得用 sd.wait() 返回时间冒充首音。空音频或全静音也不能伪造非零首音。

只有测试证明存在问题时才改生产代码；若当前实现能通过，保留实现并给出测试证据。

## 3. 第二阶段：接通真实首音事件

设计一个小的、按 utterance 关联的 TTS 播放事件，从 TtsPlayer 经 TtsQueue 传到 DualTrackStats。要求如下：

1. sounddevice callback 内只记录 time.perf_counter() 的绝对时间戳、样本数和状态；不要在音频 callback 里直接调用引擎、打印或等待。
2. PlayTiming 增加可选的 first_callback_t（perf_counter 绝对时间）；需要时也记录 first_pcm_ready_t。相对毫秒字段可以保留，但端到端计算必须用同一个 perf_counter 时钟的绝对时间戳。
3. TtsJob 记录 submit_t；事件至少携带 utt_id、seq、version、submit_t、first_pcm_ready_t、first_callback_t、persistent、stream_reused、stream_open_ms、active underruns、expected/written samples、是否被打断。
4. TtsQueue 在工作线程完成 speak/play 后发事件即可；first_callback_t 应是 callback 当时记录的值，不是工作线程事后读取的时间。
5. prefetch 路径也必须产生同样事件，不能因为没有走 speak() 就缺首音。
6. DualTrackEngine 按 utt_id 保存/更新对应的 DualTrackStats，使 FINALIZE_WAIT_TTS=False、reset_utt 已执行后到达的事件仍更新原句结果，不写入新一句。可以用有界映射或等价机制，避免无限增长。
7. TTS 关闭、播放被有效打断、空 PCM、无真实 callback 时，真实首音字段保持 None，不填 0，不用播放结束时间兜底。

### DualTrackStats 字段与兼容

保留旧字段及旧口径，避免历史 JSONL 不可比：

- latency_perceived_ms：继续表示 speech_end → 译文就绪；文档和打印中明确改名为 mt_ready/perceived_mt 的展示标签，但 JSON 旧字段保留。
- t_first_audio_ms：当前已是译文入队前的旧口径。保留作 legacy 字段并标注 deprecated，不得静默改义；可新增 t_mt_enqueue_ms 存同一含义，逐步迁移展示。

新增明确字段（命名可做小调整，但含义必须清楚）：

- t_tts_pcm_ms：speech_end → 首段 PCM 就绪。
- t_first_audio_callback_ms：speech_end → 设备第一次写出本句非零 PCM。
- latency_device_ttfa_ms：与上一项相同的端到端主指标；若不想重复存储，可只保留一个并在 JSON 输出另一个派生字段。
- mt_ready_to_callback_ms：译文就绪 → 设备首音，便于看 TTS+排队+设备成本。
- tts_active_underruns、tts_samples_expected、tts_samples_written、tts_persistent、tts_stream_reused、tts_interrupted。

正常未打断且有非零播放的句子，device TTFA 不应早于同句 MT ready；若出现负值或明显倒序，先修时钟/关联关系，不要筛掉异常样本。

## 4. 第三阶段：paced bench 支持真实 TTS

扩展 scripts/bench_paced.py：

- 增加 --tts on|off，默认 off，保证旧用法不变。
- 增加 --persistent on|off|空，空表示沿用配置默认。
- --tts on 时才创建 TtsPlayer、启用 DualTrackEngine(play=True)，使用真实输出设备；保持 FINALIZE_WAIT_TTS=False，不能把采集线程重新阻塞到整段播放结束。
- bench 结束汇总前必须等待本批 TTS 事件完成（通过队列 idle/显式事件同步），并确保 late event 已合并到对应结果；不能用固定 sleep 猜事件已到。
- 每句同时输出：src/tgt、旧 latency_perceived_ms、t_cut/t_asr_done/t_mt_done、t_tts_pcm_ms、latency_device_ttfa_ms、mt_ready_to_callback_ms、open、active underruns、samples expected/written、speculate_hit。
- 汇总 mean/median/p90/max 时，旧 MT-ready 与新 device-TTFA 分开统计，不得混为一列。
- JSONL 保留旧字段并追加新字段；记录 wav、tts/persistent、线程、adaptive spec 等配置。
- 保持原有 audio-thread per-chunk 统计，真实 TTS 下也报告 max 和 over_100ms，观察 CPU 争用。

## 5. 必跑实验

1. 先跑针对性测试，再跑 .venv\Scripts\python.exe -m pytest tests -q，贴真实通过数。
2. 跑第 2 节的 20 次“打断→续播”真实设备压力测试，persistent on；报告失败次数、stop 延迟、后续句样本完整率和 active underruns。
3. paced 真实设备 A/B，固定当前 ASR/ speculative 默认：
   - cache\ab_weekday\accept_phrases.wav，--tts on --persistent off
   - 同一 wav，--tts on --persistent on
   - cache\ab_weekday\tail_duosimo.wav，同样 off/on 各一次
4. 若 device TTFA 的 off/on 中位差波动超过 50ms，再交错复跑一轮；不要只挑最好的一次。
5. 对照同 wav 的 --tts off 结果，检查 src/tgt、句数和分段是否一致；真实 TTS 若导致 MT-ready 或 feed_chunk 退化，要如实报告，不能只展示 TTFA 收益。

不要求本轮再把 450ms VAD 门槛调低，也不要根据单句结果改任何默认值。

## 6. 验收标准

- 所有正常、非打断、非空播放都有真实 first_callback_t，且在 reset_utt 后仍能归到正确 utt_id。
- 打断后的下一句连续 20 轮无丢失、无旧哨兵提前结束、无串句；若当前代码做不到，修复后才进入 A/B 结论。
- 空闲补零不计 active underrun；真实多子句供数不足仍能被记录。
- 旧 latency_perceived_ms 数值口径与历史一致；新 device TTFA 不填假值、不与 PCM-ready 混用。
- persistent off 仍完整可用；on/off 文本和句数一致，tail pad 与多子句流水收益不丢。
- 全量测试通过；若原仓库已有与本任务无关的失败，单独列出，不能静默跳过。

## 7. 交付

1. 正确性测试结果：是否复现旧 sentinel/空闲 underrun 问题，改了什么，20 轮压力测试数据。
2. 最小 diff 摘要，重点说明 late event 如何按 utt_id 回填，为什么不会写错句。
3. paced 的 MT-ready vs device-TTFA 完整表，persistent off/on 对照及 mean/median/p90/max。
4. 保存到 docs/END_TO_END_TTFA_AB.md，原始记录写 docs/end_to_end_ttfa_ab.jsonl。
5. 用一张延迟账本总结当前剩余大头：VAD cut、CUT 后 ASR 等待、MT、TTS PCM、设备排队/回调分别是多少。
6. 最后只给“下一步最值得测的 1 个方向”，不要在本轮顺手实现。另列出必须由人耳确认的连续两句、尾音节、打断后下一句、多子句长句四项；不要把样本数正确等同于听感已验收。
