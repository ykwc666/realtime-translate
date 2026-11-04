# 给 Cursor 的任务提示词：安全提前收句，而不是盲降 0.35

用法：工作区打开 `D:\xuanjian\realtime-translate`，把下面整段复制进 Cursor 对话框。
目标：先证明 0.45 的静音地板还能不能安全下探。只做测量与只读探针，不改默认、不为了命中延迟指标强切。

---

任务：量化 VAD 静音门槛从 0.45 下探的安全边界，并测出「投机结果已就绪时提前收句」的理论收益。先做固定阈值 A/B，再做只读 early-cut probe；本轮不要实现生产版提前收句，也不要改任何默认值。

## 0. 环境与边界

- 当前目录是真实仓库 D:\xuanjian\realtime-translate，解释器用 .venv\Scripts\python.exe。
- 工作区有大量未提交改动。不要回退、格式化、提交或改写无关文件，不要 commit。
- 允许改：app/dual_track.py（只加只读 probe/日志与必要测试）、scripts/bench_paced.py（只加实验参数与输出字段）、相关测试，以及新建 docs。
- 不改生产默认：VAD_SILENCE_SEC、VAD_BACKEND、ASR_*、MT_*、TTS_* 全部保持现状。
- 不新增依赖，不换模型，不调用网络下载。
- 不做无关重构，不顺手优化 TTS/MT/ASR。

## 1. 已知事实

1. 当前默认 VAD_SILENCE_SEC=0.45；真实设备端到端中位 device TTFA：
   - accept_phrases 619ms，mt_ready 450ms；
   - tail_duosimo 560/596ms。
   剩余最大头就是 450ms 静音地板，不是 TTS。
2. 历史结论已经写过：0.35「气口更险」，不默认；TEN-VAD 0.35 曾出现多句粘成一句。
   所以不能再只跑一遍 0.35 就宣称可升默认。
3. 投机定稿的触发不是固定在 250ms，而是 speculate_trigger_silence_sec()：
   - 自适应公式下，最短 80ms 就可能启动；
   - 典型短句 audio_sec≈1.4，预测二遍解码约 330ms；0.45 时剩约 370ms，0.35 时只剩约 270ms；
   - 二遍解码实测短句约 266–350ms、长句约 500ms。
   因此固定降到 0.35 可能让投机从命中变成超时，device TTFA 不降反升。
4. 现有 `bench_paced.py --tts on --persistent on` 已能输出 mt_ready、device TTFA、speculate_hit、active underrun。扩展它，不要另写一套口径不同的 bench。

## 2. Phase A：固定阈值 A/B，先看会不会碎句/粘句

对以下 wav 跑固定阈值对照。全部用 Silero，不改 VAD_BACKEND：

- cache/ab_weekday/accept_phrases.wav
- cache/ab_weekday/tail_duosimo.wav
- cache/ab_weekday/pause_xingqi_er.wav
- cache/ab_weekday/stop_zheshi_xingqi.wav
- cache/ab_weekday/cont_tuesday.wav
- cache/ab_weekday/latency_phrases_rt.wav

阈值取 0.45 / 0.40 / 0.35，交错运行，避免设备热机偏差。每组至少跑 2 次；`bench_paced.py --silence` 已支持覆盖。

先做 `--tts off` 的分段扫描（便宜、无播放干扰），再做 `--tts on --persistent on` 的端到端子集（至少 accept、tail、pause_xingqi_er、stop_zheshi_xingqi）。

每个 wav 以 0.45 的结果为参考，逐项判定：

- merge（粘句）：2 条及以上参考 src 被合成 1 条。
- split（碎句）：1 条参考 src 被拆成 2 条及以上。
- tail_loss：参考尾字/尾词丢失。
- text_mismatch：分段数相同但 src 文本不一致。
- count_delta：句数变化。

要输出原始 src 列表，不能只报句数。ASR 文本比较忽略首尾空白和标点，不要忽略汉字。

同时报告每个阈值：

- t_cut_ms（应接近 450/400/350）；
- mt_ready、device TTFA 的 mean/median/p90/max；
- speculate_hit 比例；
- t_asr_done_ms − t_cut_ms（CUT 后还要等 ASR 多久）；
- spec_counters（starts/hits/miss_timeout/cancel_speech）；
- audio-thread over_100ms。

硬判据：任一 wav 出现 merge、split、tail_loss，或 pause_xingqi_er / stop_zheshi_xingqi / cont_tuesday 的 src 与 0.45 不一致，该阈值即判定为不安全，不得升默认。

## 3. Phase B：只读 early-cut probe，求安全提前收句上界

只加探针，不改变切句行为。默认仍按 0.45 收句；探针只记录「如果 0.35 切，会发生什么」。

在 DualTrackEngine 每 100ms 块处理后，当仍在说话、trailing silence 落在 0.30–0.45 区间时，按 50ms 粒度记录快照：

- utt_id、当前 silence_sec、buffer audio_sec；
- 投机 job 是否存在、是否 done；
- 如果 done：spec 结果文本、结果耗时；
- 当前 streaming partial 文本；
- 之后是否在本句 0.45 之前重新检测到语音（气口恢复）；
- 最终 0.45 收句后的 src、t_cut、speculate_hit、t_asr_done。

实现方式自由，但必须满足：

1. 探针只读，不触发/取消投机，不改变 `_maybe_speculate`、endpoint 或 arbiter 的决策。
2. 每句最多记录有限条快照（例如 0.30/0.35/0.40/0.45 四个点），禁止每块无限写日志。
3. 新增一个实验开关，例如 `ENDPOINT_EARLY_CUT_PROBE=False`；默认关闭，bench 用命令行或环境变量开启。
4. 写 docs/endpoint_early_cut_probe.jsonl，每行一个 utt 或一个快照，字段清晰；不要污染 paced_bench.jsonl 的旧字段。
5. 探针不能显著拖慢音频线程；报告 probe 开启前后 feed_chunk 的 median/max。

对每句计算 oracle 指标：

- ready_at_350：0.35 时投机是否 done；
- same_text_at_350：该投机文本（规范化后）是否等于最终 0.45 的 src；
- resumed_before_450：0.35–0.45 之间是否重新出现语音；
- safe_350 = ready_at_350 且 same_text_at_350 且 not resumed_before_450；
- earliest_safe：0.30–0.45 中最早同时满足「ready + same_text + 之后无恢复语音」的检查点；
- 如果 0.35 不安全但 0.40 安全，单独统计 0.40。

注意：same_text 只是 oracle 分析，不是生产判定规则。不要用最终答案倒推生产门控，也不要在实时路径里偷看未来。

## 4. 分类与下一步门槛

按句子类型分组统计：

- 短句（<1.6s）；
- 长句（≥1.6s）；
- 带气口/停顿（pause_xingqi_er、stop_zheshi_xingqi、cont_tuesday）。

每组输出 n、ready%、same_text%、resumed%、safe%。

只有当同时满足以下条件，才建议进入“生产版 early-cut”开发：

1. 固定 0.35 在所有气口 wav 上不碎句、不粘句、不丢尾字；
2. 或固定 0.35 虽不安全，但 oracle 在气口集上 false-safe = 0，且短句 safe_350 ≥80%；
3. 提前收句带来的 device TTFA 中位改善 ≥80ms；
4. speculative hit 不下降，或下降后 CUT 等待没有把收益吃掉；
5. probe 开启时 audio-thread 没有明显超支。

任一条件不满足，就停止 VAD 下探这条线，明确写“0.45 是当前安全地板”，不要为了指标硬做。

## 5. 必跑实验

1. `.venv\Scripts\python.exe -m pytest tests -q`，记录真实通过数；新增 probe 单测，用合成序列验证 0.30/0.35/0.40 快照和 safe 计算，不依赖真实音频。
2. Phase A 的分段扫描和端到端 A/B，按上面的 wav 清单跑。
3. Phase B 用 `--tts off` 跑全部 wav 的 probe（避免播放干扰），再用 accept/tail/pause 三个 wav 加 `--tts on --persistent on` 复核 device TTFA。
4. 如 0.40 比 0.35 更接近安全线，额外把 0.40 纳入结论，但不改默认。

## 6. 交付

1. 固定阈值 A/B 表：每个 wav 的句数、src 列表、merge/split/tail_loss、mt_ready、device TTFA、spec_hit、CUT 后 ASR 等待。
2. probe 表：短句/长句/气口三组的 ready/same_text/resumed/safe 比例，以及 earliest_safe 分布。
3. 一段明确结论：0.35 能否安全使用；若不能，0.40 是否可用；若都不能，确认 0.45 地板。
4. 若满足第 4 节门槛，只给出生产版 early-cut 的最小设计草案，不在本轮实现；若不满足，直接关闭这条线。
5. 所有原始记录写入 docs/endpoint_silence_ab.jsonl 和 docs/endpoint_early_cut_probe.jsonl，总结写入 docs/ENDPOINT_EARLY_CUT_AB.md。
6. 最后列出仍需人耳确认的项：气口句是否被切碎、尾字是否完整、连续两句是否合并。不要把 ASR 文本相同等同于听感完全无问题。
