# 会后重译（定稿）

实时路径是草稿；会话旁路录音后，整段 SenseVoice → 同款 MT → 双语稿。

## 用法

```powershell
# 实时（默认录音 → data/sessions/<时间戳>/）
python run.py --mode vad
# 关闭录音：python run.py --mode vad --no-record

# 会后
python run.py --post latest
python run.py --post data\sessions\20261105-160805
# 或
python scripts\retranscribe.py latest
```

产物（写在同一 session 目录）：

| 文件 | 内容 |
|---|---|
| `mic_raw.wav` | 降噪前麦录音 16k mono PCM16 |
| `live.jsonl` | 实时句旁路 |
| `meta.json` | 会话元数据 |
| `transcript.md` / `.srt` / `.jsonl` | 会后双语定稿 |
| `report.json` | 耗时、分块、`timestamps: approximate`、live 对照 |

**时间戳**：块内多句时在字符比例种子附近找能量谷切分（`report.timestamps=energy_split`）；
找不到谷则回退字符比例。**不是** forced alignment。A/B：`scripts/ab_post_timestamps.py`。

## 设计要点

- 目标块长 25s，在 ±3s 内找 200ms 能量最低处切开；不重叠
- 静音块可丢，但**整段不会交空**（弱电平 AMI 不会被误丢光）
- ASR：`StreamingAsr.transcribe_offline_only`（SenseVoice）
- MT：与实时同一 `Translator`（含 postedit/TM）
- 本轮只录麦克风；TTS 漏麦会进定稿 → 验收戴耳机

## 闸门实测（2026-11-05）

| 闸门 | 结果 |
|---|---|
| G1 不丢内容 | AMI+FLEURS 60 条 **empty=0**；char_ratio&lt;0.85 = 2 → `g1_ok`（≤2） |
| G2_single_chunk | `wer_post=0.1381` = oneshot，**Δ 0.0**（语料均 &lt;25s，只验证单块） |
| G3 速度 | 13.9s 音频热模型 wall≈1.05s（RTF≈0.075 ≪ 0.75） |
| G4 可复现 | 同 session 连跑两次原文/译文 **逐字一致** |
| G5 不伤实时 | postedit 锚定后 `eval_ledger --skip-a5`：AMI **27/48 · WER 0.4433**，FLEURS **11/12 · 0.1352**（与修前同）；`app/config.py` 未改 |

## 闸门修订（postedit 锚定后）

### 根因
`postedit_zh_en` / `postedit_en_zh` 曾用 `re.search` 子串命中就整句返回 canned，复合句后半段被静默删掉（实时+会后共用）。

### 修复
1. canned → `re.fullmatch`  
2. 源句含子句分隔符时跳过 canned（中文 `，,；;`；英文仅分号 / `, and|but|…`，避免 `No, …` 误杀）  
3. canned 词数 &lt; hyp 50% → 退回 hyp  

### G1 / G2 / G6（`scripts/gate_offline_retranscribe.py`）

| 闸门 | 结果 |
|---|---|
| G1 | empty=0，char_ratio&lt;0.85 共 2 条（逐条打印 ref/hyp/ratio），`g1_ok=true` |
| G2_single_chunk | 注明「只对单块成立」；Δ=0.0 |
| G6 多块 | 前 30 条+0.6s 间隔拼 **129s**，**chunks=6**，WER 0.1684 vs oneshot 0.1276，**Δ=+0.0408 ≤ 0.06**；边界 ±100ms RMS≈0 |

### live_compare
仅当 `meta.mode ∈ {vad,duplex}` 且 session 内 `live.jsonl` 非空；否则 **`null`**（不读 `data/harvest/`）。

### 单元测试
`tests/test_postedit_anchor.py` + `tests/test_retranscribe.py` + 原有 `test_mt_postedit.py`：**25 passed**。

### 实麦端到端（mode: vad）

```powershell
python run.py --mode vad --no-tts
# 说完 Ctrl+C
python run.py --post latest
```

Session：`data/sessions/20261105-163258/`（`meta.mode=vad`，`live_compare` 有值）。

`transcript.md` 要点：

| 原文 | 译文 |
|---|---|
| 你好，延迟大概有多少？ | Hello. How much delay is it? |
| 今天天气怎么样？ | How's the weather today? |
| 现在是星期二？ | It's Tuesday. |
| …支付链路的审批会，大概2个小时。 | …payment chain… two hours. |
| 我们需要把这个方案再对一遍，然后发给产品。 | We need to repeat this programme… |
| 帮我把这个文件发给他，再确认一下时间。 | Send him this document… |

复合句「天气 + 星期二」在会后被标点拆成两句，**两边都保留**（不再被 postedit 删掉「星期二」）。
