# CURSOR TASK: re-arm 第二枪降为 offline-only，CUT 时与 online finalize 合并

日期：2026-11-04
前置数据：docs/spec_rearm_accept.json、scripts/diag_spec_rearm.py
复制分割线以下内容给 Cursor。

---

任务：把 speculate re-arm（restart job）的解码从「整段 2pass 重解码」降为「仅 offline 路」，CUT 时再与近乎免费的 online finalize 合并，让气口句的 asr_wait 从 ~335–428ms 降到 ≤50ms。只做这一件事。

## 0. 环境与规矩

- 当前目录就是真实仓库 D:\xuanjian\realtime-translate，解释器用 .venv\Scripts\python.exe。
- 不动 VAD_SILENCE_SEC、arbiter、MT、TTS、prefer_2pass_asr 语义。
- 上轮 66 passed 是基线，改完必须 ≥66（含新增测试）。
- 不新增依赖。

## 1. 已知事实（上轮 trace + 数据已坐实，不要重查）

- 根因已修：holding_cut 分支现在也走 `_note_chunk_energy`，cancel_speech 能触发，re-arm 能重启（`restart=True` 免 skip_headstart）。
- **但经济账是负的**：pause_xingqi_er 现在 `starts=2 restarts=1 hits=1` 而 `asr_wait_med=335/428ms`，比修复前（miss→串行 final，250–336ms）更差，且多烧一整次 2pass。
- 原因：re-arm 用 `transcribe_with_2pass(snap)` 做整段重解码——2.4s 音频约 785ms（Paraformer oneshot ~500ms 是大头，offline ~250–300ms 与之并行取 max）。静音窗口只有 CUT(450) − 触发(80) = 370ms，装不下 785ms，CUT 后 drain 335–428ms。
- 而串行 fallback `finalize_stream` 便宜得多：流式状态已增量解码完毕，online finalize ≈ 免费，只剩 offline 路 ~250ms。
- 结论：**第二枪的 Paraformer oneshot 是纯浪费**——它要重做的事，CUT 时用流式状态免费就能拿到。

## 2. 设计（照此实现，别自行扩围）

1. config.py 新增 `ASR_SPECULATE_REARM_MODE = "offline"`，可选值 `"offline"`（默认）/ `"full"`（回退到上轮行为）/ `"off"`（restart 不再起第二枪）。加注释说明三种模式语义。
2. `StreamingAsr` 加一个只跑 offline 路的方法（如 `transcribe_offline_only(audio)`）：只调 `_transcribe_sherpa_offline`，返回 offline 文本与耗时，**不跑 online oneshot**。
3. restart job（`restart=True` 且 mode=offline）在后台线程跑 `transcribe_offline_only`，job 里记 `kind="offline"` 和 `offline_text`、`offline_ms`。首枪（非 restart）行为一字不改，仍是完整 2pass。
4. `_take_speculate` 对 `kind="offline"` 的 job 命中时，**不能直接当最终结果返回**——返回一个带 `offline_text` 的轻量标记（如 `AsrResult` 之外的专用结构或 `res.offline_hint`），由 `_finalize_utt` 的 CUT 路径：
   - 用现有流式状态跑 online finalize（沿用 `finalize_stream` 里的 online 部分，别整段重解码）；
   - `prefer_2pass_asr(online_finalize_text, offline_text)` 合并——**与现在 finalize_stream 的合并语义完全一致**；
   - `ms` 记账保持 `online.ms + offline.ms` 之和，offline_ms 用投机 job 的耗时，历史口径不变。
5. offline job 未在 CUT 时完成：走现有 miss 语义回退完整 `finalize_stream`（作废的 offline 解码就是成本，不额外补救）。
6. miss_grow 判定对 offline job 同样生效（speech 增长→作废），不变。

## 3. 验收（原始输出全部贴回来）

1. pytest ≥ 66 passed，新增单测覆盖：① offline 命中时 CUT 路径确实「online finalize + prefer_2pass 合并」且文本等于直接 finalize_stream 的结果（可用假 ASR 断言调用序列）；② offline job 未完成回退 finalize_stream；③ mode="full" 行为与上轮一致；④ mode="off" 不起第二枪。
2. 跑 scripts/bench_spec_rearm_accept.py（或等价），硬判据：
   - pause_xingqi_er 两个 rep：`asr_wait_med ≤ 50ms`，`starts=2 restarts=1 hits=1`，src 仍为「现在是星期啊」；
   - accept_phrases / tail_duosimo / stop_zheshi_xingqi / cont_tuesday：counters 与 asr_wait 与上轮 spec_rearm_accept.json 一致（首枪路径不许被波及）；
   - audio_thread：pause wav 的 max_ms 与 over_100ms 不得高于上轮（382/485ms、2/37）——offline-only 比 full 2pass 省一半 CPU，理应变好；
   - **统计每 wav 的投机 CPU 总耗时**（各 job 墙钟之和）：pause wav 应比上轮下降 ≥40%。
3. mt_ready 澄清（必须做）：上轮汇报写了「mt_ready 770→440」，但 CUT 地板 450ms 使 440 不可能，且 spec_rearm_accept.json 里根本没有 mt_ready 字段。本轮在同一批 run 里逐句记录 pause_xingqi_er 的 mt_ready（speech_end→译文就绪），给出修复前（用 git stash 或开关 ASR_SPECULATE_REARM_MODE="off" 模拟）/full/offline 三组对照。若复现不出 440，直说上轮数字有误。

## 4. 硬约束

- 首枪（非 restart）行为、`prefer_2pass_asr`、miss_grow 语义、`ms` 记账口径全部不变。
- 不为合并逻辑新增任何字符串启发式。
- 不动 MT / TTS / VAD / arbiter / probe。
- diff 集中在 config.py / asr_stream.py / dual_track.py / 新测试，越小越好。

## 5. 交付

1. diff + 设计说明（offline 命中时 CUT 路径的数据流一段话）。
2. 修复前后 counters 与 asr_wait 对照表（逐 wav）。
3. 投机 CPU 总耗时前后对照。
4. mt_ready 三模式对照与 440 疑点的澄清结论。
5. 一句话回答：若未来 VAD 降到 0.35，offline-only 第二枪（~250–300ms，窗口 350−80=270ms）在气口句上的预期 asr_wait 是多少？（用本轮实测的 offline 耗时估算，不用真跑 0.35。）
