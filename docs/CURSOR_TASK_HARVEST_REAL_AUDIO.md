# CURSOR TASK: 真实语料采集 + 回放评测 — 打破自产自销的质量尺子

日期：2026-11-04
前置：MT_EN_ZH_QUALITY_STEP1/STEP2（60/60、broken=0、P95 0.43ms、82 passed）
复制分割线以下内容给 Cursor。

---

任务：为实时翻译管线建一个「真实语料采集 → 离线回放评测」闭环。MT 质量尺子目前全部来自自写的句子（规则、评测集、holdout 同一作者），措辞泛化已验证，**分布泛化没有**。本轮不做任何模型/规则改动，只建采集与评测工具。附带修一个污染验收信号的 flaky 测试。

## 0. 环境与规矩

- 当前目录就是真实仓库 D:\xuanjian\realtime-translate，解释器用 .venv\Scripts\python.exe。
- 基线 82 passed，改完必须 ≥82（含 flaky 修复后全量套件连续 3 次全绿）。
- 不新增依赖，不动 MT 模型/postedit/TM/ASR/VAD/TTS 生产逻辑。

## 1. 背景（已坐实，不要重查）

- en→zh 60 句上 raw ct2 通过率仅 ~28%（17 ok），72% 靠 postedit+TM 规则层修复。规则层在这把尺子上已饱和。
- 评测集、规则、holdout 同源（都是你写的），只能证明措辞泛化，证明不了真实语音分布上的泛化。
- `--mode loopback` 已可用（系统输出→识别→翻译），这是真实英文语料进入系统的现成通道。

## 2. 第一部分：会话采集器（scripts/harvest_session.py）

做一个轻量采集模式（可独立运行，也可作为 run.py 的 --harvest 开关接入现有 loopback/duplex 模式）：

1. 逐句落盘到 `data/harvest/<日期>-<序号>.jsonl`，每行记录：
   `t_wall | src（final ASR 文本）| tgt（发给 TTS 前的最终译文）| mt_raw（postedit 前）| spec_hit | asr_wait_ms | latency_perceived_ms | latency_device_ttfa_ms | mode | 音源描述（启动参数传入）`
2. 已有字段直接复用 DualTrackStats/投机计数器，**不要另造计时**；拿不到的记 null，禁止伪造。
3. 落盘要防抖：只在句子 finalize 时写一行，崩溃前已写的行不能丢（逐行 flush）。
4. 采集器对延迟零侵入：加日志的路径不许新增关键路径上的同步 IO（用队列或采集完批量写均可，说明选择）。
5. 启动时打印明确的「开始采集」提示与输出文件路径。

## 3. 第二部分：离线回放评测（scripts/judge_harvest.py）

1. 读采集 jsonl，输出待判表格（src / mt_raw / tgt / 空白人判列），生成 `docs/harvest_judge_<日期>.md`。
2. 提供 `--ingest` 参数：把人判结果（ok/awkward/wrong 三档，awkward=能懂但不自然）读回，汇总：
   - MT 质量：三档占比、按 mt_raw≠tgt 分组（规则修过 vs 没修过）——**这个分组是核心输出**，它直接回答「规则层在真实语料上的覆盖率」
   - 延迟：perceived 与 device_ttfa 的 P50/P90/max
   - ASR 侧顺带统计：src 为空/过短/明显截断的比例（这是采集器免费送的 ASR 真实质量数据）
3. 判定标准写进生成的 md 头部，保证两次会话判得一致。

## 4. 第三部分（附带）：修 flaky 测试

`tests/test_tts_interrupt.py::test_play_array_stop_cuts_short` 在全量套件满负载下偶发失败（单跑 3/3 过，本轮全量 1 failed）。要求：
- 只修测试的稳健性（放宽时限/重试/隔离负载敏感断言），**不许改 tts_play 生产代码**。
- 修完全量套件连跑 3 次必须全绿，把 3 次结果贴回来。

## 5. 硬约束

- 本轮不改任何生产翻译/识别/播放逻辑；采集器若必须碰 app/ 代码，只允许加旁路 hook（说明位置与理由）。
- 采集文件格式就是契约，写清楚后再动手；后续人判工具依赖它。

## 6. 交付

1. 采集器与回放评测的使用说明（CMD 命令行，用户侧操作不超过两步）。
2. 用 cache/ab_weekday 的 wav 走一遍采集器做冒烟（模拟 loopback 输入即可），贴 3 行样例 jsonl 与汇总表。
3. flaky 测试修复的 diff 与 3 次全量结果。
4. 一段话说明：如果真实语料上「规则修过组」的 wrong 占比显著高于自写集，下一步该往哪走（你的判断）。

## 7. 用户侧操作（我来安排，你不用做）

真实采集由用户执行：开 loopback 模式播 10 分钟真实英文会议/访谈音频。你的冒烟测试用现有 wav 替代即可。
