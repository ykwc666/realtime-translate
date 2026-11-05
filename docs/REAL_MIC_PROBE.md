# 实麦自检埋点（只记录，不调参）

## 字段（每句一行 JSONL）

| 字段 | 含义 |
|---|---|
| `utt_idx` | 句序 |
| `audio_sec` | 送入 ASR 的时长 |
| `open_peak` / `open_rms` | 开说那一窗原始峰值 / RMS（未加权） |
| `open_prob` | TEN 窗概率；当前 sherpa API 拿不到 → 恒 `null` |
| `emit_path` | `model_endpoint` / `silence_forced` / `flush` |
| `cut_reason` | 仲裁 `dec.reason`，或 `vad_endpoint` / `max_utt` / `asr_loop` / `hold_timeout` / `flush` / `empty_abort` / `unchanged` |
| `trailing_silence_sec` | `endpoint.trailing_silence_sec`（非引擎侧静音钟） |
| `n_partials` / `src` / `tgt` | partial 次数、识别、译文 |

落盘：`data/probe/<YYYY-MM-DD-HHMM>/*.jsonl` + `summary.json`（已 gitignore）。
会话结束（Ctrl+C / 流结束 / `pipe.close()`）打印汇总块。

## 汇总样例（dualsim · accept_phrases.wav）

```
=== 实麦自检 ===
utts=5  empty_abort=0  emit_model=5  emit_silence_forced=0  flush=0
open_peak  p10=0.120 p50=0.172 p90=0.323
open_rms   p10=0.041 p50=0.052 p90=0.095
cut_reason cut_now=5
trailing_silence 全为 0: yes
--- 逐句 ---
#1 1.6s peak=0.172 rms=0.052 path=model_endpoint reason=cut_now src='你好。' tgt='Hello.'
…
```

## 死分支证据（本轮只记不修）

1. **`emit_silence_forced_count == 0`**：是。  
   dualsim 五句全部 `emit_path=model_endpoint`；endpoint 计数器 `emit_silence_forced_count=0`。  
   对应 `SherpaVadEndpoint.feed` 里 `elif self._speech_started:` 强制切句分支（见 `app/vad_stream.py` 约 330 行）：`if self._vad.is_speech_detected:` 把 **method** 当 bool 用时恒真，elif 不进。

2. **`trailing_silence_sec` 恒 0**：是。  
   同上，`_silence_samples` 只在该 elif 里累加；五句 `trailing_silence_sec=0.0`，`trailing_silence_always_zero=true`。

## 闸门实测

| 闸门 | 结果 |
|---|---|
| G0 | 改前 `102 passed, 1 failed`（`test_interrupt_resume_20_cycles` 偶发）；改后 `103 passed`（同用例本次绿）。无新增失败。 |
| G1 | `--skip-a5 --corpus ami,fleurs` → AMI **27/48** WER **0.4433**；FLEURS **11/12** WER **0.1352**（与 TEN 0.4 checkpoint 一致，±0） |
| G2 | `git diff app/config.py` 空；`VAD_TEN_THRESHOLD=0.4` 等红线未动 |
| G3 | 无麦用 **dualsim**（live 切分路径；`--mode file` 仍是 segment+oneshot，不经 DualTrack）跑 `cache/ab_weekday/accept_phrases.wav`，退出打印上列汇总 |
| G4 | 同 wav、模型已热：baseline 2712ms / probe 2703ms，**Δ −0.31%**（< 3%） |

## 用法

```cmd
cd /d D:\xuanjian\realtime-translate
.venv\Scripts\python.exe run.py --mode vad
```

实麦五句判读表见 `docs/CURSOR_TASK_REAL_MIC_PROBE.md` §6。
