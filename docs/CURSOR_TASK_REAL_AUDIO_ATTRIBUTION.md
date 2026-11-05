# Cursor 任务：真实语音的"可分账"评测 + 两个确定性缺陷修复

> 复制下方分割线以下全部内容给 Cursor。工作区：`D:\xuanjian\realtime-translate`

---

任务：先把真实语音的评测尺子做成能分清 ASR 错还是 MT 错，再修当前数据里已经看得见的两个确定性缺陷（MT 重复幻觉、碎片切句）。不要换 ASR 模型，不要动延迟参数。

## 0. 环境

- 当前目录就是真实仓库 `D:\xuanjian\realtime-translate`，可直接运行。
- 解释器统一用 `.venv\Scripts\python.exe`。
- 不要 commit，改动留在工作区。
- 不新增依赖、不下载新模型。

## 1. 上轮 harvest 的三个口径问题（必须先承认，再修）

上轮 50 句结论是 ok 6% / awkward 38% / wrong 56%，但这个数字不能直接当"真实语音质量"，原因有三：

1. **测试集成分严重偏域外**：`cache/harvest_public` 总 297s，其中 LibriSpeech 240s（占 81%），内容是 1916 年小说（`sodality`、`ciborium`、`species`、`blessed sacrament` 这类词）。本产品是通话/会议翻译，拿文学朗读当主指标等于拿错卷子。
2. **判定基准是 `vs ASR src`**（见 `docs/harvest_judge_2026-11-04-public-en.jsonl` 的 `judge_note`）。当 src 本身被截断成半句（如 `Confession will be heard all the。`），无论 MT 怎么译都算 wrong。这个口径测不出"ASR 的锅"和"MT 的锅"各占多少。
3. **没有参考转写**，所以 WER 从未被测量过，全都是推测。

## 2. 我已实测的现场证据（可直接引用，不用重复验证）

用当前管线的 streaming + 2pass 跑三个源文件：

**AMI 会议（far-field，真实域）**
```
[0] 7.8s | our wd work get that just across ones in all the corporers for example probabone zero zero is very high information vala
[1] 6.8s | even though there all the other same topics where doesn't occur yeah cause this would
[2] 5.7s | Yeah, in, in a sense, I mean, this is a bit like the what like document frequency over total frequency
```
- 段 0 错误率极高；`vala` 是被切断的 `value`（切在词中间）。
- 段 1 在 harvest 里被切成两句，且丢了 `occur`：`even though there all the other same topics，where doesn't。` —— 同一段音频，切法不同，文本不同。

**LibriSpeech（朗读，域外度中等）**
```
[0] 8.6s | do for dinner turnipse and carrots sent bruised potatoes and fat mutton pieces to be laddled out and thick pepppered flower fat and sauce
[1] 5.8s | After early nightfall, the yellow lamps would light up here and there. the squalid quarter of the brothels
```
- 段 1 识别几乎完美（只把小写和句读丢了）。但 harvest 里这句的译文是 **`黄灯照亮了天上和天上`** —— 后半句 `the squalid quarter of the brothels` 整段丢失，还幻觉出"天上和天上"。
- **这是决定性证据：MT 在 src 基本正确时也会崩。**

**碎片段（"又断"的直接来源）**
```
0.9s | came neare
1.8s | rationtionally is waiting on you
2.1s | Hello, Bertie, any good in your mind
```

**重复幻觉（5 例，全部出现在长或脏输入上）**
| 索引 | 译文片段 |
|---|---|
| 05 | 他希望有炖菜吃晚饭、**菜菜菜**、萝卜、胡萝卜和土豆 |
| 14 | 另一方程式开始**慢慢地慢慢地慢慢地慢慢地慢慢地慢慢地** |
| 29 | 连**靠靠靠靠着**, 画着他的书写者 |
| 08 | **黄灯照亮了天上和天上** |
| 13 | 月亮上浮露着白白**的**白白 |

成因已定位：`app/translate.py:120-128` 的 `_translate_ct2` 用 `beam_size=1`（贪心），无 `no_repeat_ngram_size`、无 `repetition_penalty`。本机 CT2 4.8.4 的 `translate_batch` 支持这两个参数。

## 3. Phase A：重建可分账的尺子

### A1 语料重构（主集必须是域内）
- 主集：**AMI 会议音频**（in-domain，有官方人工转写与说话人标注），≥40 句。用 hf-mirror 取 `edinburghcstr/ami` 或同类镜像，挑 2~3 个会议、不同说话人。
- 次集：FLEURS 英文朗读，≥10 句（有转写，好对齐）。
- 压力集：LibriSpeech / 小说朗读，**只作 out-of-domain 参考，明确标注，不计入主指标**。
- 每句采集记录必须新增 `corpus` 字段（`ami` / `fleurs` / `libri`）和 `ref`（参考转写）。上轮 `data/harvest/*.jsonl` 没有这两个字段，所以永远算不清账。

### A2 参考转写怎么拿（按优先级，拿不到就剔除，禁止造）
1. podscripter fixtures 仓库里的 `tests/fixtures/audio/en/<name>.expected.json`（仓库说明明确写了每个音频配一份）。
2. 上游数据集自带转写：FLEURS 按文件名里的 id 对齐；AMI 用官方 annotations（可按 meeting id + 时间片对齐）。
3. 任何一句拿不到参考转写 → 从 WER 统计里剔除，并在报告里写明**覆盖率**（例如"60 句中 48 句有参考，WER 覆盖率 80%"）。
4. **绝对禁止**用 ASR 输出、或用模型自己生成"参考答案"。宁可样本少。

### A3 指标定义
- `asr_wer`：句级 WER（用 `jiwer` 若已有，否则自己算 Levenshtein，不要新增依赖）。
- `fragment_rate`：时长 < 1.0s 的句子占比；以及 src 以功能词结尾（`the/a/an/to/of/and/that/he/she/was/is`）的占比 —— 这就是"断"的量化。
- `mt_faithful`：对照**参考转写**判 ok / awkward / wrong，逐句写一行理由。基准不再是 ASR src。

### A4 分账实验（本轮最重要的一张表）
把有参考转写的那些句子，**直接用参考文本走 MT**（绕过 ASR），用同一套判定 → 得到 `MT-only 通过率`。

```
pipeline 通过率（ASR→MT）
MT-only 通过率（ref→MT）
两者之差 = ASR 的贡献
```
要求按 `corpus` 分组各出一行。这张表直接回答"到底该先修 ASR 还是先修 MT"。

### A5 ASR 三路对照（只测不改）
在同一集合上分别用：`online-only`（流式 Paraformer finalize）、`offline-only`（SenseVoice）、`merged`（现行 `prefer_2pass_asr`），出三组 WER 与人判通过率。

目的：判断现行的字符串合并启发式是否在**选错的那一路**。只出数据，不改 `prefer_2pass_asr`。

## 4. Phase B：修 MT 重复幻觉

1. `app/config.py` 新增三个开关，默认先保持旧行为（不要一上来改默认）：
   - `MT_DECODE_BEAM = 1`
   - `MT_NO_REPEAT_NGRAM = 0`
   - `MT_REPETITION_PENALTY = 1.0`
2. `app/translate.py` 的 `_translate_ct2` 透传这三个参数。CT2 若因版本报 `TypeError`，捕获后回退旧调用并打一行 `[MT decode fallback]`。
3. 新建 `scripts/scan_repetition.py`：扫描任意 harvest/judge jsonl，检测译文中 ≥3 次连续重复片段（如 `菜菜菜`、`慢慢地` ×6），输出每句是否命中和总体 `repetition_rate`。要能对已有 `docs/harvest_judge_2026-11-04-public-en.jsonl` 直接复算，作为基线。
4. A/B 至少四组：`greedy`（现状）/ `beam3` / `beam3+no_repeat3` / `beam3+no_repeat3+penalty1.1`。
5. **延迟硬门**：final 路径 MT P95 ≤ 120ms（现状约 26ms）。若 beam3 超支，允许"长输入才开 beam"或"partial 走 greedy、final 走 beam"，但必须在报告里说明策略。
6. `latency_perceived_ms` 语义不许改。

## 5. Phase C：碎片切句

1. 现状：0.9–2.1s 碎片 + 半词结尾（`vala` / `came neare`）。
2. 先诊断再改：回答"`vala` 是被 VAD 切掉的，还是模型自己截断的"——用 `cache/harvest_public/ami_en2001b_1993_2020.wav` 复现，打印该处切点的时间戳与前后波形能量。
3. 若确认是切点问题：新增 `VAD_MIN_UTT_SEC`（默认 1.0，可关），不足且无终止标点的句子并入下一段，不要独立出句。
4. 验收：`fragment_rate` 下降，WER 与通过率不降，`device TTFA` 中位不得上涨超过 30ms。

## 6. 硬约束

- 不改 `prefer_2pass_asr` / `merge_overlap_text` / `take_speakable` 的任何逻辑（A5 只测不改）。
- 不动 `VAD_SILENCE_SEC=0.45`、投机（speculate）相关逻辑、TTS、双通道。
- 不换 ASR 模型、不下载新模型、不加依赖。
- 所有"人判"必须诚实标注为 **model-judge**，逐句附理由；不得写"人工判定"。
- 报告里的百分比必须能由落盘 jsonl 复算，不许只有汇总数字。

## 7. 交付

1. 五张表：①按 corpus 的 WER ②fragment_rate ③分账表（pipeline vs MT-only）④A5 三路 ASR 对照 ⑤Phase B 四组 A/B（含 repetition_rate 与 MT 延迟）。
2. Phase C 的切点诊断结论（`vala` 是谁切的）。
3. 一句话结论：这批真实语音上，ASR 与 MT 各占多少责任，先修哪个。
4. **待人工复核清单 15 句**：最典型的 wrong 5 句 + borderline 5 句 + 修复后变好的 5 句，每条附 src / ref / tgt / 判定理由，供我逐条抽查。
5. 默认值只许改有明确数据支持的项，并写清依据来自哪张表哪一列。
