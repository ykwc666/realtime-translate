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

**时间戳按字符比例在块内分摊，是近似，不是 forced alignment。**

## 设计要点

- 目标块长 25s，在 ±3s 内找 200ms 能量最低处切开；不重叠
- 静音块可丢，但**整段不会交空**（弱电平 AMI 不会被误丢光）
- ASR：`StreamingAsr.transcribe_offline_only`（SenseVoice）
- MT：与实时同一 `Translator`（含 postedit/TM）
- 本轮只录麦克风；TTS 漏麦会进定稿 → 验收戴耳机

## 闸门实测（2026-11-05）

| 闸门 | 结果 |
|---|---|
| G1 不丢内容 | AMI+FLEURS 60 条 **empty=0**；char_ratio&lt;0.85 仅 2（辅证） |
| G2 ≤ oneshot+0.02 | `wer_post=0.1381` = `wer_oneshot`，**Δ 0.0** |
| G3 速度 | 13.9s 音频热模型 wall≈1.05s（RTF≈0.075 ≪ 0.75） |
| G4 可复现 | 同 session 连跑两次原文/译文 **逐字一致** |
| G5 不伤实时 | `pytest` **103 passed**；`app/config.py` 未改；红线阈值未动 |

样例：`data/sessions/20261105-160805/transcript.md`（`accept_phrases.wav` 灌录）。
