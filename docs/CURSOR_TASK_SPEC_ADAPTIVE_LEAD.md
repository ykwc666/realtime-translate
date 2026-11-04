# 给 Cursor 的任务提示词：长句投机提前量自适应

用法：工作区打开 `D:\xuanjian\realtime-translate`，把下面整段复制进 Cursor 对话框。
前置：已接受二遍并行 3+2 线程的方向，但要求先用真实 wav 补验收，再叠这项改动。

---

任务：降低长句收句后的 ASR 等待，改动仅允许作用于“投机定稿何时启动”。先补真实音频验收，再做自适应提前量；不要顺手优化其他模块。

## 0. 环境与边界

- 当前目录是真实仓库 D:\xuanjian\realtime-translate，解释器统一用 .venv\Scripts\python.exe。
- 工作区还有 MT/TTS/VAD 等其他未提交改动，不要修改、格式化、提交或回退它们。
- 本任务只允许改 app/config.py、app/dual_track.py、scripts/bench_paced.py，以及新增相关测试和文档。
- 不新增依赖，不 commit。

## 1. 当前数据与诊断

二遍 ASR 已改成并行，默认 ASR_THREADS_ONLINE=3、ASR_THREADS_OFFLINE=2。
微基准 6 段文本一致，最佳并行 3+2 的 wall 中位 323ms，串行 6+6 为 472ms。
但 paced 仍只剩三句合成句，验收样本偏少。

最新 paced-2pass-par3-2：

- 长句「你好延迟大概有多少」：cut=450ms，asr_done=701ms，mt_done=747ms。
- 「今天天气怎么样」：458ms，基本贴住 450ms 收句地板。
- 「现在是星期二」：450ms，ASR/MT 已完全藏进静音窗口。

所以不要再压 ASR 微基准，也不要全局降低 VAD_SILENCE_SEC。剩余问题之一是长句：
app/dual_track.py 的 _maybe_speculate() 固定等静音 250ms 才启动，长句二遍解码来不及在 450ms CUT 前完成，
CUT 后仍等待约 251ms。目标是把长句的投机启动时间按预计解码耗时提前，降低 mean/p90/max。

## 2. 先补当前并行配置的真实音频验收

给 scripts/bench_paced.py 增加可选参数 --online-threads、--offline-threads、--2pass-parallel on|off，
只在 benchmark 启动前覆盖 config，不改生产默认。

用同一版本代码分别跑：

- 串行 6+6：--spec on --silence 0.45 --2pass-parallel off --online-threads 6 --offline-threads 6
- 并行 3+2：--spec on --silence 0.45 --2pass-parallel on --online-threads 3 --offline-threads 2

音频至少覆盖 cache/ab_weekday/accept_phrases.wav 和 cache/ab_weekday/tail_duosimo.wav。
逐句比较 src/tgt、句数、latency_perceived_ms、t_asr_done_ms。
若并行 3+2 出现文本/分段回归或真实音频明显变慢，停止后续改动并报告，不要继续叠自适应。

## 3. 实现自适应投机提前量

只改 _maybe_speculate() 的触发时机，不改 _take_speculate() 的等待、快照有效性和回退逻辑。

要求：

1. 新增 ASR_SPECULATE_ADAPTIVE_LEAD=True；False 时逐字保持当前固定 250ms 行为。
2. 新增最小触发静音配置，默认 0.08s；自适应触发不得早于它。
3. 根据当前 buf 的语音时长估计本次 transcribe_with_2pass 墙钟耗时，并令：
   触发静音 ≈ VAD_SILENCE_SEC − 预计解码秒数 − 安全余量，
   再夹在 [最小触发静音, 原 ASR_SPECULATE_SILENCE_SEC] 内。短句不应比现状更晚，长句应更早。
4. 预计耗时的系数必须来自现有 scripts/bench_2pass_ab.py 的逐段数据或 docs/bench_2pass_ab_raw.txt，
   用标准库做简单估算即可。打印每段 audio_sec 与实际 wall，以及最终公式；不要凭空填系数。
5. 如果预计解码时间超过整个静音窗口，就从最小触发静音开始；不要声称这种长句能完全藏住。
6. 仍只允许一个 _spec job；语音恢复作废旧快照的规则不变。
7. 保留现有 paused 提前触发兼容逻辑，但任何路径都不能绕过最小触发静音。
8. 启动日志增加 audio_sec、预计 decode ms、实际触发 silence；hit 日志已有 waited，保留。
9. 抽出一个无模型依赖的纯函数计算触发阈值，并补单元测试：
   adaptive=False 完全等于旧值；长句不晚于短句；结果始终在上下界内；零长度/异常输入安全。

不要改 VAD_SILENCE_SEC、EndpointArbiter、prefer_2pass_asr、prefer_final_asr、merge_overlap_text、MT、TTS。

## 4. A/B 验证

先跑 .venv\Scripts\python.exe -m pytest tests -q，记录通过数。

固定并行 3+2、VAD silero、silence=0.45，对 fixed 与 adaptive 做交替 A/B/A/B（至少两轮，最好三轮），
不要只按“全部 baseline 再全部 adaptive”的顺序，避免温度和缓存偏差。

音频：

- cache/ab_weekday/accept_phrases.wav
- cache/ab_weekday/tail_duosimo.wav
- cache/ab_weekday/pause_xingqi_er.wav（安全性对照）

每组输出逐句表：src、t_cut、t_asr_done、t_mt_done、latency_perceived、speculate_hit、spec waited。
汇总 mean / median / p90 / max，并单独列出最长句。验收条件：

- 句数、每句 src/tgt 与 fixed 完全一致，尤其 pause_xingqi_er 不得改变分段。
- 长句 max 或 p90 明显下降，目标长句从约 747ms 降到 600ms 左右或更低。
- 短句中位数回退不得超过 50ms。
- 若收益不稳定或文本有差异，把 ASR_SPECULATE_ADAPTIVE_LEAD 默认设为 False，并如实报告。

## 5. 交付

1. 真实 wav 上“串行 6+6 vs 并行 3+2”的补充验收表。
2. 预测公式及其逐段拟合依据。
3. fixed vs adaptive 的逐句与汇总数据。
4. 最小 diff、测试结果、是否建议默认开启。
5. 明确列出投机提前带来的额外 CPU 解码次数/作废次数；如果没有统计，就补统计，不能只报延迟收益。
