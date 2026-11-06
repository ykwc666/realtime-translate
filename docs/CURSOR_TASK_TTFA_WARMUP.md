# Cursor 任务书：接通 device_ttfa + 启动加载进度

> 本文件是唯一需求来源。按顺序做 A、B 两件事，各自独立提交。
> 任何"顺手优化"都不在范围内；不确定就先报告，不要自己扩权。

---

## 0. 开工前先认三个事实（别重测、别推翻）

1. 实麦已通过验收：`data/sessions/20261106-153237`，7/7 句有译文，`cut_reason: cut_now=7 empty_abort=1`，`trailing_silence` 全 0。
2. `pytest` 基线是 **144 passed**，不许减少；不许改已有断言让它变绿。
3. 启动耗时基线：字幕服务 1.5s 起、模型加载完 21.1s、TTS 预热完 37.5s、`双轨实机 VAD：` 出现在 37.5s。

## 1. 不变量（碰了就回滚，不接受"更好了"）

- 不碰 `VAD_TEN_THRESHOLD=0.4`、`VAD_SILENCE_SEC=0.45`、`VAD_SPEECH_RMS_MIN=0.035`。
- 不改 25s 块长，不改 `ASR_LOOP_*` / `ASR_SPEC_*` / `prefer_2pass`。
- 不新增或修改 TM / postedit 规则。
- 不改字幕布局、文案、去重与排序逻辑（`app/subtitle.py`、`scripts/subtitle_server.py`）。
- 不为让数字好看去改测量口径；不加 `sleep`；不加假打点。
- 不改 `README.md` / `start.bat`（`7403afa` 刚定稿，若确实需要改，先停下来问）。

---

## 2. 任务 A：把 `latency_device_ttfa_ms` 接通

### A.1 现象（有证据，不用再找）

`--mode vad` 跑 7 句，`[utt]` 行 `device_ttfa=None`；落盘也一样：

```
data/sessions/20261106-153237/live.jsonl   7 行，latency_device_ttfa_ms 全为 null
```

但链路是通的、只是没落地：

| 位置 | 代码 | 作用 |
|---|---|---|
| `app/tts_play.py:363-365` | `timing.first_callback_t` / `callback_valid=True` | 播放回调真的发生了 |
| `app/tts_play.py` `TtsPlayEvent` | 携带 `first_callback_t` | 事件已封装 |
| `app/dual_track.py:311-337` | `_on_tts_play_event()` | 会写 `st.latency_device_ttfa_ms` |
| `app/dual_track.py:1204` | `_register_utt_stats(out)` | 在 `[utt]` 打印之后才登记 |
| `app/harvest.py:100-102` | `emit_from_stats()` → `row_from_stats()` | finalize 时立刻拍快照 |
| `app/session_record.py:122-133` | `emit_live()` → `row_from_stats()` | **同样是 finalize 时立刻拍快照** |

**最可能的原因**：`row_from_stats()` 在 finalize 那一刻就把 stats 拍成 dict 了，此时 TTS 还没开始播，`latency_device_ttfa_ms` 必然是 `None`，之后即使回填也回不到已写出的行。

**注意两条落盘路径都要覆盖**：`data/harvest/*.jsonl`（`--harvest`）和 `data/sessions/<id>/live.jsonl`（`--mode vad` 默认就写）。只修一条不算完成——实麦验收看的是后者。

### A.2 步骤

1. **先证明**：在 `_on_tts_play_event()` 里临时加一行打印（带 `utt_id` 和 `cb_ms`），跑一段 dualsim，确认回调**确实到达**、且到达时间在 finalize **之后**。把这段打印贴进报告，然后删掉临时打印。
2. **再修**：允许两种实现，二选一，说明理由：
   - **（推荐）延后一行落盘**：`HarvestWriter` 增加一条 pending 缓冲——emit 时先把上一条 flush 出去，再挂起当前条；`close()` 时 flush 尾条。天然让上一句的 TTFA 已经回填。
   - **回填**：finalize 后不立即 emit，等 TtsPlayEvent 到达再 emit，并对最后一条在 `close()` 补 flush。
3. **硬约束**（两种实现都必须满足）：
   - `live.jsonl` 行数 == 该次 utt 数，**不许出现重复行、不许补写第二行**；
   - 行顺序与 utt 顺序一致；
   - 字段名、字段含义、`json` 结构一律不变（下游已在用）；
   - 进程退出（Ctrl+C）时最后一行必须落盘。

### A.3 禁止

- 不改 TTS 播放逻辑、不引入等待/超时、不让 finalize 阻塞等 TTS。
- 不改 `latency_perceived_ms`（`mt_ready`）的计算方式。
- 不把 `device_ttfa` 改成"从 finalize 算起"之类换口径的做法。
- 若诊断结论是"该模式本来就没有设备回调"，**允许就此收手**：不造假数字，改在 `docs/` 里写清结论 + 在 `[TTS-q]` 行补一个 `cb=` 字段。这种情况必须在报告里明确说明。

### A.4 验收闸门（用现有 10 分钟回放，不劳烦人说话）

```bat
cd /d D:\xuanjian\realtime-translate

:: 改动前基线（先跑，存一份）
.venv\Scripts\python.exe run.py --mode dualsim --file cache\soak\soak_10min.wav --harvest --source-desc ttfa_before

:: 改动后（同一个 wav，同样参数）
.venv\Scripts\python.exe run.py --mode dualsim --file cache\soak\soak_10min.wav --harvest --source-desc ttfa_after
```

harvest 的真实输出路径在启动时打印：`[harvest] 开始采集 → data\harvest\<日期>-NNN.jsonl`，**以这行为准**（不是 `data/sessions/*/live.jsonl`）。把两次的 NNN 路径记下来做对比。

| 闸门 | 判据 |
|---|---|
| A-1 | `latency_device_ttfa_ms` 非空率 **≥ 80%**，且 **`harvest.jsonl` 与本次 `sessions/<id>/live.jsonl` 两个文件都要达标** |
| A-2 | 行数与 `ttfa_before` **相同**（≈94），无重复行 |
| A-3 | `latency_perceived_ms` 逐条对比，偏差 **≤ 10ms** |
| A-4 | `src` 逐条相同（允许 ≤1 条差异，出现必须解释） |
| A-5 | `pytest` **≥144 passed**，0 failed |

同时报告 TTFA 实测分布：`p50 / p90 / max`（单位 ms，从"说完"到"扬声器出第一帧"）。

---

## 3. 任务 B：启动加载分段打点

### B.1 现状

`app/pipeline.py:61-94` 现在只在这几处打印：

```
加载模型中（CPU）ASR=... MT=... …      ← 之后静默 21 秒
TTS 预热（中英）…
TTS 预热完成 zh=..ms en=..ms
模型就绪，用时 37.5s
```

问题：前 21 秒只有一行，用户以为程序卡死。ASR 内部的 8s + SenseVoice 的 12s 完全没有可见性。

### B.2 要求

在**加载边界**补打印，格式统一 `[load] <阶段> 就绪 <耗时>s`，必须 `flush=True`：

| 打点位置 | 打印内容（示例） |
|---|---|
| `app/asr_stream.py` `_init_sherpa_paraformer_online()` 返回后 | `[load] paraformer 在线识别就绪 8.3s` |
| `app/asr_stream.py` `_init_sherpa_sensevoice()` 返回后（仅 2pass 开启时） | `[load] sensevoice 2pass 就绪 12.6s` |
| `app/translate.py` `_init_ct2()` 返回后 | `[load] MT 中↔英 就绪 0.9s` |

约束：

- 耗时用 `time.perf_counter()` 现算，**不许写死**。
- 加载顺序、线程结构、懒加载策略一律不动，只加打印。
- `双轨实机 VAD：partial=0.75s，静音=0.45s，句尾整句 MT（中途不译）；Ctrl+C 退出` 这一行**逐字符保持原样**（README 引用了它）。
- 已有的 `加载模型中…` / `TTS 预热…` / `模型就绪，用时 …` 三行保持原样，不许合并、不许删。

### B.3 禁止

- 不许并行化加载、不许预热前置、不许把模型换成更小的。
- 不许为"看起来快"把 `模型就绪` 的时间改成只统计后段。

### B.4 验收闸门

`--mode vad` 会在打印 banner 后立刻开麦，所以用超时截断采集即可：

```bat
cd /d D:\xuanjian\realtime-translate
.venv\Scripts\python.exe run.py --mode vad > cache\boot_log.txt 2>&1
:: 出现「双轨实机 VAD：」后立刻 Ctrl+C
```

跑 **3 次**，报三次的时间线（`[load]` 各行 + `模型就绪，用时` + banner 出现时刻）：

| 闸门 | 判据 |
|---|---|
| B-1 | 3 次 `模型就绪，用时` 均 **≤ 40.0s** |
| B-2 | `[load]` 三条齐全、顺序为 paraformer → sensevoice → MT |
| B-3 | 每次启动相对上次的 `模型就绪` 波动 ≤ 3s（不能有偶发 60s） |
| B-4 | `pytest` **≥144 passed** |

---

## 4. 提交

两个 commit，分开，不要把 A 和 B 混在一起：

```
fix(record): persist device TTFA after playback callback (harvest + session live)
feat(boot): staged [load] progress lines during model init
```

`_start_test.bat` 是本地临时文件（untracked），**不要提交、不要删**。

## 5. 汇报格式（缺一项就算没做完）

1. A：诊断结论一句话（回调是否到达、几点到达）+ 你选了哪种实现 + 为什么。
2. A：`ttfa_before` vs `ttfa_after` 的四个闸门逐项数字（非空率 / 行数 / perceived 偏差 / src 差异）。
3. A：TTFA 的 p50 / p90 / max。
4. B：三次启动的完整时间线（贴原始日志行）。
5. 两个 commit 的 hash。
6. `pytest` 尾行原样贴出。

任何一项没过，就停下来说明，不要"调参数直到过"。
