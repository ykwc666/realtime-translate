# Cursor 任务：空句三分解归因 + 引擎吞句修复 + VAD 弱信号 + 判定闸门收紧

> 复制下方分割线以下全部内容给 Cursor。工作区：`D:\xuanjian\realtime-translate`

---

任务：上轮 ledger 发现 39/64 句 pipeline 产出为空。我已逐层定位，空句不是单一原因。本轮先分解归因，再修两个确定性问题，最后收紧判定闸门重算分账表。不要换 ASR 模型。

## 0. 环境

- 当前目录是真实仓库 `D:\xuanjian\realtime-translate`，解释器 `.venv\Scripts\python.exe`。
- 不要 commit，改动留工作区。不下载新模型（`models/ten-vad.onnx` 已在本地）。

## 1. 我已实测定位的事实（直接采信，不用重复验证）

对 39 个空句中的代表做了逐块追踪（`DualTrackEngine.feed_chunk` 全程 spy）：

| 文件 | ref | 现象 | 归因 |
|---|---|---|---|
| ami_000 | YEAH WE'RE GONNA MEET UP | endpoint **开过句、弹出了段**，但 `finalize_stream` **从未被调用**，产出 None | 段被吞（arbiter hold 不释放 或 空录音早弃路径） |
| ami_001 | I MEAN WE'RE NOT EVEN THERE YET | endpoint **从未开句**（`in_speech` 从未为真） | silero VAD 漏检 |
| ami_005 | OH UM I DON'T KNOW | 同上 | silero VAD 漏检 |

音频层面：39 个空句的文件 RMS p50=0.0053，正常句 p50=0.0135——空句明显更安静，但：

- **RMS 门限不是瓶颈**：`VAD_SPEECH_RMS_MIN` 从 0.035 一路降到 0.004，开句结果一字不差（5/8 开，ami_001/005/007 恒不开）。是 silero 模型本身在这些文件上不报语音。
- `segment_audio` 能给这些文件切出段，是因为它有**兜底**：`vad_stream.py:347-350`，无检出且音频 ≥ MIN_SPEECH_SEC 时整段兜底返回。流式路径没有这个兜底。

判定闸门口径问题（必须修）：`scripts/eval_ledger.py` 的 `model_judge_mt` 只查长度/CJK/重复。它把这句判了 ok：

```
ref=SO EVEN IF IT DOESN'T WORK YOU CAN JIGGERY POKERY AROUND AND MAKE IT WORK
src=下个一ven钟dodoesn't you you degree，ort it around and make it wo
verdict=ok  reason=length/cjk/repetition gates pass
```

所以上轮报告里所有 ok_rate（pipeline 8.3%、mt_only 95.8%）都是**被闸门放宽过的代理值**，方向可信、幅度不可信。分账表必须用收紧后的闸门重算。

## 2. Phase A：39 空句逐句归因（只测不改）

在 `scripts/eval_ledger.py`（或新脚本 `scripts/decompose_empty_utts.py`）给每个文件打**唯一** `drop_reason`：

- `vad_never_open`：全程 `endpoint.in_speech` 未为真（ami_001 类）
- `emitted_swallowed`：endpoint 弹出了段，但 finalize 未被调用（ami_000 类）
- `finalize_empty`：finalize 被调用但返回空文本
- `mt_skip`：src 非空但 tgt 空（过短跳过 MT 等）
- `produced`：有产出

对 64 句全量输出分解表（按 corpus 分组）。验收：39 个空句每一句都有且只有一个归因，计数与明细一致。

## 3. Phase B：修"弹出后被吞"（ami_000 类）

1. 先用 monkey-patch trace 确定 ami_000 的段走了哪条路：`arbiter.on_vad_candidate` 返回什么？是不是 hold 之后永不释放？还是进了 `VAD_EMPTY_ABORT_SEC=2.0` 的早弃（`dual_track.py:1053-1064`）？把结论写进报告，**先诊断后改**。
2. 修复原则：**VAD 已弹出段的句子必须有终局**——要么 finalize 产出（哪怕 ASR 空，也记 `finalize_empty`），要么显式 drop 并计数。禁止无声消失。
3. 若是 arbiter hold 永不释放：加一个 hold 超时（比如 3s 无新文本变化则强制释放 finalize），可配置，默认开。
4. 验收：ami_000 类文件有产出或有明确 drop 记录；合成集（`cache/ab_weekday/accept_phrases.wav`、`tail_duosimo.wav`、`pause_xingqi_er.wav`）的文本、分段、延迟全部不回归；pytest 全过。

## 4. Phase C：VAD 弱信号漏检（ami_001/005/007 类）

候选三个，逐个 A/B（不许同时上）：

1. **silero threshold 下调**：查 `SherpaVadEndpoint` 的 silero 配置里 threshold 当前值，往下试（如 0.5→0.3/0.2）。
2. **VAD_BACKEND=ten**：TEN-VAD 模型已在 `models/ten-vad.onnx`，切换只需配置。
3. **前置增益**：进 VAD 前对 chunk 做固定增益（×3/×4）或 RMS 归一化到 0.03，注意：只进 VAD 的信号可加增益，进 ASR 的音频是否同样处理要分别评估，别放大噪声底导致误开。

评测集：39 空句（望救回）+ 25 正常句（不许变坏）+ 合成三件套（accept/tail/pause，文本不许变）+ 至少 5 段纯静音/风扇噪声（不许误开——自己合成：静音、白噪、键盘声）。

指标：空句恢复率、误开率、正常句 WER/文本不变、合成集 `latency_perceived_ms` 不涨。

只许在有明确收益且无误开时改默认，写清依据。若三个候选都救不回且无思路，如实说，不硬改。

## 5. Phase D：判定闸门收紧 + 重算分账

1. `model_judge_mt` 增加两条（en→zh 方向）：
   - src 侧：pipeline 判定时，若 src 含 CJK 字符（英文语音翻中文路径下 src 不该有汉字），直接 wrong，reason=`lang_flip`。
   - tgt 侧：内容词覆盖——把 ref 去停用词后的内容词与 tgt 做词级对齐（可用简单词干/同义小表，别上新依赖），覆盖率低于阈值判 wrong，阈值先用数据校准（画一下 ok 句与 wrong 句的覆盖率分布再定），并写出你定的值与依据。
2. 用新闸门**重跑全部 64 句**（pipeline 与 mt_only 都重判），输出新旧判定逐句对照。
3. 重算分账表（pipeline vs mt_only，按 corpus 分组）——这是本轮交付的头条数字。
4. WER 两种口径都报：含空句（空=1.0）与仅检出子集。

## 6. 硬约束

- 不改 `prefer_2pass_asr` / `merge_overlap_text` / `take_speakable`。
- 不动 `VAD_SILENCE_SEC=0.45`、投机（speculate）、TTS、双通道。
- 不换 ASR 模型、不加依赖、不下载模型。
- 判定仍一律标注 `model-judge`，逐句带 reason；不许写"人工判定"。
- `latency_perceived_ms` 语义不变。

## 7. 交付

1. Phase A 分解表（39 空句每句一个归因）。
2. Phase B 的诊断结论 + diff + 合成集回归数据。
3. Phase C 三候选 A/B 表 + 是否改默认的决定。
4. Phase D 新旧闸门对照 + 重算的分账表（含两种 WER 口径）。
5. 一句话结论：修完后 pipeline 与 mt_only 在主集上的差距还剩多少，下一个最大桶是什么。
6. 15 句待人工复核清单（新闸门下最典型的 wrong/borderline/变好各 5 句，含 ref/src/tgt/reason）。
