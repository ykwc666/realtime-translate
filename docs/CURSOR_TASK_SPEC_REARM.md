# CURSOR TASK: 投机取消后重整（speculate re-arm）— 修复气口句的 miss_grow 悬挂

日期：2026-11-04
前置数据：docs/endpoint_silence_ab.jsonl、docs/endpoint_early_cut_probe.jsonl

复制分割线以下内容给 Cursor。

---

任务：修复「投机 job 在句中气口启动、说话恢复后悬挂作废、且从不重启」的问题，让带气口的句子在最终收句时也能命中投机。只做这一件事，不要顺手改 VAD 阈值、arbiter、MT、TTS。

## 0. 环境与规矩

- 当前目录就是真实仓库 D:\xuanjian\realtime-translate，可以直接运行。
- 解释器统一用 .venv\Scripts\python.exe。
- 默认保持 VAD_SILENCE_SEC=0.45、ENDPOINT_EARLY_CUT_PROBE=False，本轮一律不动。
- 不新增依赖，不装包。
- 上轮 64 passed 是基线，改完必须 ≥64。

## 1. 已知事实（不用重复验证）

上一轮 endpoint A/B 里，气口句 pause_xingqi_er（src「现在是星期啊」）在三个静音阈值下全部：

- `starts=1, hits=0, miss_grow=1, cancel_speech=0`
- CUT 后 `asr_wait_after_cut ≈ 250–290ms`（串行 final 兜底）

代码事实（app/dual_track.py）：

- `_maybe_speculate()`（约 381 行）在 `sil < min_sil` 时会 `_clear_speculate(reason="speech")`（约 389 行），理论上说话恢复应触发 cancel_speech。
- `_take_speculate()`（约 444 行）在 CUT 时检查快照后的增量：语音增长 → miss_grow。
- 实测 cancel_speech=0 证明：**说话恢复期间清理路径从未执行**。旧 job 从气口一直活到 CUT，才被 miss_grow 判死，然后整句回退串行 final。

推测的可能机制（需要你用 trace 证实或推翻其一）：
- 恢复的语音太短/太轻，engine 侧 RMS 钟没归零，VAD 侧 silence 计数也没归零，导致清理条件 `sil < min_sil` 一直不成立；
- 或 `_maybe_speculate` 的调用点（约 805、861 行）在恢复期间因 `in_speech` / `_holding_cut` 状态没被执行到；
- 或别的——以 trace 数据为准。

## 2. 第一步：只读诊断（不改生产代码）

新建 scripts/diag_spec_rearm.py：跑 pause_xingqi_er.wav 和 cont_tuesday.wav，逐 chunk 记录并打印一行：

`utt | t_ms | chunk_rms | in_speech | endpoint.in_speech | holding_cut | vad_sil | engine_sil | spec_state(idle/running/cleared) | spec_n0`

要求：
- 用现有 DualTrackEngine 真实路径喂块（不要另起简化管线），trace 用环境变量或 debug 开关门控，跑完即关。
- 用这份 trace 回答三个问题：
  1. spec 是在哪个时刻、什么 sil 值下启动的（气口开始后多少 ms）？
  2. 说话恢复的那个 chunk，vad_sil 和 engine_sil 各是多少？清理路径为什么没走到？
  3. 从恢复到 CUT 之间，有没有任何一次 `_maybe_speculate` 被调用？
- 交付一段根因结论（引用 trace 行号），明确说是上面哪个推测、还是新机制。

## 3. 第二步：修复（只在确认根因后动手）

目标语义：**说话恢复 → 旧 spec job 立刻作废（cancel_speech+1）→ 下一段静音到达触发条件 → 用新的完整 buffer 快照重启一枪**。

1. 修清理路径：不管根因是哪种，说话恢复必须可靠地清掉旧 job。可以用 endpoint 的 speech 状态变化、engine RMS 归零、或 chunk RMS 超阈值任一可靠信号，选你 trace 证实有效的那一个，并说明为什么。
2. 重启：`_maybe_speculate` 现有逻辑在 `self._spec is None` 时本来就会重启——确认清理修好后重启自然成立，不需要额外代码就别加。
3. 防抖：新增 `ASR_SPECULATE_MAX_STARTS = 3`（config.py），单句重启次数超限就不再起（计数器照记），避免口吃/呼吸密集的极端句子无限烧 CPU。
4. 旧线程不许 join、不许杀：清掉引用让它自然跑完丢弃即可。注意同一句内 token 不变，确认 `_take_speculate` 拿到的永远是**最新** job（`self._spec` 被新 job 覆盖即可），旧的不会被误取。
5. `spec_counters` 新增 `restarts` 计数（清理后同句内再起算一次 restart）。

## 4. 验收实验（原始输出全部贴回来）

1. `.venv\Scripts\python.exe -m pytest tests -q` ≥ 64 passed。
2. 跑 `.venv\Scripts\python.exe scripts\bench_endpoint_early_cut_ab.py --phase all --reps 2`（或等价子集，必须含 pause_xingqi_er / stop_zheshi_xingqi / cont_tuesday / accept_phrases / tail_duosimo）。
3. 硬判据：
   - pause_xingqi_er：`starts=2, restarts=1, hits=1`（最终收句命中），`asr_wait_after_cut` 中位从 ~250–290ms 降到 **≤50ms**，src 仍为「现在是星期啊」不变。
   - accept_phrases / tail_duosimo：hit 率、srcs、句数与上轮基线完全一致（那两个 cancel_speech=1 的正常长句行为不变：accept 应仍是 starts=5 hits=4 cancel_speech=1）。
   - 所有 wav 的 starts/句 ≤ 3，audio_thread median 与 over_100ms 不劣化（±10% 内）。
   - 若某句 miss_grow 仍发生，必须给出 trace 解释，不许静默放过。
4. 新增单测：模拟「speech → 静音触发 spec → 语音恢复 → 静音 → CUT」的喂块序列，断言 cancel_speech=1、restarts=1、CUT 时拿到的是第二次的完整快照（可用假 ASR 记录每次收到的 audio 长度）。

## 5. 硬约束

- 不动 VAD_SILENCE_SEC、arbiter、endpoint 阈值、prefer_2pass_asr、MT、TTS、persistent 流。
- 不动 `_take_speculate` 的 miss 判定语义（silence 增长仍算 hit，speech 增长仍算 miss）。
- 不为修这个 bug 重构 speculate 模块结构；diff 越小越好。
- 诊断脚本的 trace 门控默认关闭，不污染正常运行日志。

## 6. 交付

1. 根因段落（trace 证据 + 行号）。
2. diff（应当集中在 dual_track.py / config.py / 新诊断脚本 / 新单测）。
3. 修复前后的 counters 对照表（每个 wav 一行：starts/restarts/hits/cancel_speech/miss_grow/asr_wait_after_cut 中位）。
4. 一句话回答：如果最终静音只有 350ms（未来若降阈值），这个 re-arm 修复让气口句在 350 时刻的命中率变成多少？（用 trace 里第二次 spec 的启动时刻和解码耗时估算，不用真跑 0.35。）
