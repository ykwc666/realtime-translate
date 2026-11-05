# Cursor 任务：会后重译（record → 整段 SenseVoice → MT → 双语稿）

## 0. 为什么是这条线

已经量过的三个数（AMI；`docs/ledger_asr_oneshot.json` / `VAD_TEN_THRESHOLD_AB.md`）：

| 路径 | WER |
|---|---|
| 整段 SenseVoice offline（oneshot，不切句） | **0.157** |
| 整段 2pass | 0.200 |
| 经 VAD 切句管线（当前默认 TEN 0.4） | **0.443** |

约 2.8×。差距不在模型、不在 MT，在「切句把模型听全的机会切掉了」。
（早期 loop 修复前管线曾见 ~0.59，勿与当前默认混用。）
实时路径必须切（要低延迟），**但录下来的会议不必**：录完再整段跑，切句损失直接归零。

所以这条线的定位是一句话：**实时是草稿，会后是定稿。**
不做新模型、不碰阈值、不改实时路径——只是把已经存在的能力接成一条可交付的链路。

---

## 1. 红线

- 不改 `app/config.py` 任何 VAD / ASR 默认值；不改 `dual_track` 切句逻辑
- 不换 ASR 模型、不动 `prefer_2pass` / 合并策略
- 不加新依赖（`soundfile`、`sherpa_onnx`、`numpy` 已在）
- 实时路径性能不得下降（录音必须是旁路，不能阻塞 `feed_chunk`）

---

## 2. P0：会话录音（旁路，默认开）

`run.py --mode vad` / `--mode duplex` 时，把**降噪前**的麦克风原始块写到：

```
data/sessions/<YYYYMMDD-HHMMSS>/
  mic_raw.wav      16kHz mono PCM16（soundfile.SoundFile 流式写）
  live.jsonl       实时句旁路（与 harvest 同字段子集：src/tgt/t_wall…），供 --post 对照
  meta.json        {mode, src_lang, tgt_lang, started_at, duration_sec,
                    sample_rate, denoise: bool, git_commit,
                    config: {VAD_TEN_THRESHOLD, VAD_SILENCE_SEC,
                             VAD_SPEECH_RMS_MIN, ASR_SPEC_REJECT_STALE, ...}}
```

- CLI：`--no-record` 关闭；默认开（产品行为），启动时打印 `录音 → <path>`
- 必须写在 `denoise.process(chunk)` **之前**（离线不依赖实时降噪）
- 写入失败只警告一次并继续跑（不能因为磁盘满中断翻译）
- 本轮范围：**仅麦克风**（`vad` / `duplex` 的 mic）；不做 `loopback_raw`
- TTS 漏进麦会进会后稿：验收时建议戴耳机
- `data/sessions/` 加进 `.gitignore`

验收：跑 10 秒，`mic_raw.wav` 可被 `load_wav()` 读回，时长误差 < 0.2s，`feed_chunk` 单块耗时增幅 < 5%。

## 3. P1：`app/retranscribe.py`

```cmd
python run.py --post latest
python run.py --post data\sessions\20261105-1602
```

也可以在 `scripts/retranscribe.py` 暴露同样的入口（薄封装，逻辑放 `app/`）。

### 3.1 分块

- 目标块长 **25s**；在目标点 ±3s 内找**能量最低的 200ms**作为切点（避免切在字中间）
- **不重叠**（重叠会产生重复文本，去重比切准更难）
- 静音占比 > 95% 的块直接丢弃，记 `dropped_silence`
- 这样即使 VAD 判定错了，也只影响"块边界好看不好看"，不影响"内容有没有进模型"

### 3.2 识别

`StreamingAsr.transcribe_offline_only(chunk)`（SenseVoice）。返回空 → 丢弃并计数，**不要**塞占位文本。

### 3.3 断句与时间戳

- 若 SenseVoice 输出自带标点，直接按 `。？！；.!?;` 切
- 否则过 `app/punct.py` 的 `add_punctuation()` 再切
- 时间戳按**字符数比例**在块内分摊；`report.json` 里注明 `timestamps: "approximate"`

> 不要假装做了 forced alignment。近似就写近似。

### 3.4 翻译

逐句走与实时**同一条** MT 链路（`Translator` + postedit + TM），确保同一句话实时/会后译文一致；不一致时以会后为准并在 jsonl 里标 `mt_path`。

### 3.5 产物

```
transcript.md     表：t0–t1 | 原文 | 译文
transcript.srt    双语（原文一行、译文一行）
transcript.jsonl  {idx, t0, t1, src, tgt, chunk_idx, asr_ms, mt_ms}
report.json       {duration, chunks, dropped_silence, segments, asr_ms, mt_ms,
                   timestamps:"approximate", live_compare:{...}}
```

默认用 session 内 `live.jsonl` 做对照；也可用 `--live <path.jsonl>` 覆盖。
按文本相似度对齐，输出 `live_missed`（实时没有、会后补回）与 `live_wrong`。

---

## 4. 闸门（每条先用当前代码验证能过，过不了说明闸门写错了）

| 闸门 | 判据 | 说明 |
|---|---|---|
| G1 不丢内容 | 在 `data/ledger_corpus` 的 AMI 48 + FLEURS 12 上，**空输出 = 0 条**；字符数/参考 ≥ **0.85** 仅作辅证（主防空，质量看 G2） | 直接对打管线空检桶 |
| G2 不低于 oneshot | 先用 `scripts/eval_asr_oneshot.py` 量出 `WER_oneshot`，会后链路 ASR 层 WER ≤ `WER_oneshot + 0.02` | **自校准，不写死数字**；分块边界必然有点损失，0.02 是容差 |
| G3 速度可用 | 60s 音频在 CPU 上 ≤ 45s（< 0.75× 实时） | 会后不要求实时，但不能不可用 |
| G4 可复现 | 同一 session 连跑两次，`transcript.jsonl` 逐字一致 | 排除并发/随机 |
| G5 不伤实时 | `pytest -q` 与改动前同数字；`eval_ledger --skip-a5` 仍 **AMI 27/48 · 0.4433**、FLEURS **11/12 · 0.1352** | 录音是旁路，实时结果必须一位不差 |

**G2 的正确用法**：先跑 oneshot 拿基线 → 再跑会后 → 比差值。不要拿 0.157 当硬指标，
那是"不分块"的理论下限，分块后必然略差。

---

## 5. 交付物

1. 提交：`feat(post): session recording + offline retranscribe to bilingual transcript`
2. `docs/OFFLINE_RETRANSCRIBE.md`：设计、五道闸门实测数字、一次真实 session 的前后对比
3. README 增一节「会后重译」（三条命令 + 产物路径 + 时间戳是近似这句要写清）
4. 用 `cache/ab_weekday/accept_phrases.wav` 或一段真实 10 分钟录音，贴一份 `transcript.md` 样例

---

## 6. 不做

- 不做实时字幕 UI（下一轮，等这条线的"定稿源"稳定了再接屏）
- 不做说话人分离、不做翻译记忆扩容、不做会议摘要
- 不为了让 G2 好看去调分块参数反复刷
