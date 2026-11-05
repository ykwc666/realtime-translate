# 空句三分解 + 吞句修复 + VAD 弱信号 + 闸门收紧（2026-11-05）

工作区：`D:\xuanjian\realtime-translate`。未 commit。未换 ASR 模型。未改 `prefer_2pass_asr` / `VAD_SILENCE_SEC` / 投机 / TTS。

---

## 一句话结论

修完后主集（AMI）**pipeline ok 37.5%** vs **MT-only 93.8%**（紧闸门），差距约 **−56 pp**，仍主要是 **ASR/VAD**。  
最大剩余桶：**vad_never_open 弱信号漏检（TEN 已救一部分）+ 高 WER/语种串扰**。空 ASR 从 39 → **16**。

---

## Phase A：39 空句分解（只测）

落盘：`docs/empty_utt_decompose.jsonl`、`docs/empty_utt_decompose_summary.json`

| drop_reason | n | 说明 |
|-------------|---|------|
| vad_never_open | 20 | silero 从未开句（ami_001 类） |
| emitted_swallowed | 19 | VAD 弹出段但未 finalize（ami_000 类） |
| mt_skip | 2 | 有 src 无 tgt |
| finalize_empty | 0 | — |
| produced | 23 | 有产出 |

空句（非 produced）= **41**；其中无 src 的空识别 ≈ 20+19=**39**，与上轮一致。每句唯一归因。

---

## Phase B：吞句诊断 + 修复

### 诊断（ami_000）

`docs/ami000_swallow_diagnosis.json`

**根因**：VAD 已 `on_vad_candidate→WAIT` 时 `engine.in_speech` 仍为假；随后 `_begin_utt()` **清空 arbiter + holding_cut** → 段无声消失，`finalize_stream` 从未调用。  
不是 empty-abort，也不是补全窗口卡死。

### 修复（`app/dual_track.py` + `config.ENDPOINT_HOLD_TIMEOUT_SEC=3.0`）

1. VAD 出段且引擎未开句 → **先 begin 并 seed buf，再进 arbiter hold**  
2. `_begin_utt(preserve_hold=True)` 保 hold  
3. hold 墙上超时 3s 强制 CUT  
4. 空 ASR 返回 `DualTrackStats`（记 finalize_empty），禁止 `return None` 吞掉  

### 验收

| 项 | 结果 |
|----|------|
| ami_000 | 有产出：`we're going onnmeet up。` → `我们继续见面` |
| 合成三件套 | `docs/synth_swallow_fix_regress.json`：accept 4 句 / tail 2 / pause 1，文本未崩 |
| pytest | **82 passed** |

---

## Phase C：VAD 弱信号 A/B

落盘：`docs/vad_weak_signal_ab.json`（评测集：20 never_open + 23 normal + 合成3 + 静音/白噪/键盘）

| candidate | rescued | normal_ok | false_open | synth_same |
|-----------|---------|-----------|------------|------------|
| baseline silero 0.85 | 0/20 | 23/23 | 0 | 3/3 |
| silero thr 0.5 | 7 | 22 | 0 | 3 |
| silero thr 0.3 | 4 | 21 | 0 | 3 |
| silero thr 0.2 | 1 | 17 | 0 | 3 |
| **ten** | **6** | **23** | **0** | **3** |
| gain×3 VAD-only | 15 | 23 | **1** | 3 |
| gain×4 VAD-only | 17 | 23 | **1** | 3 |

**默认改为 `VAD_BACKEND=ten`**（依据本表 ten 行：有救回、零误开、合成/正常不坏）。  
gain 因噪声误开不用；silero 降阈伤害 normal_ok。

---

## Phase D：闸门收紧 + 重算分账

### 新闸门（`scripts/eval_ledger.py` model_judge_mt）

1. **lang_flip**：pipeline `src` 含 CJK → wrong  
2. **asr_wer**：pipeline src vs ref WER > **0.45** → wrong  
3. **content_density**：CJK / EN content-word 数 < **1.5** → wrong（mt_only 密度 p10≈2.0，min≈1.19；取 1.5）  

对照：`docs/ledger_gate_compare.jsonl`（同批 src/tgt 上 loose vs tight）。

示例（上轮被放行）：  
`ref=…JIGGERY POKERY…` / `src=下个一ven钟…` → **wrong / lang_flip**。

### 重算分账（紧闸门 + Phase B/C 后管线）

| corpus | pipeline ok | MT-only ok | 差 | WER 含空 | WER 仅检出 |
|--------|-------------|------------|----|----------|------------|
| **ami** | **37.5%** (18/48) | **93.8%** (45/48) | **−56.3 pp** | 0.609 | 0.414 (n=32) |
| **fleurs** | **66.7%** (8/12) | **91.7%** (11/12) | **−25.0 pp** | 0.324 | 0.324 (n=12) |
| libri OOD | 25% | 100% | — | 1.215 | 1.215 |

空 ASR：**16/64**（上轮 39/64）。  
fragment（本轮检出 utt）：short_rate 3.6%，func_end 5.4%。

对比口径说明：上轮「AMI pipeline 8.3% / mt_only 95.8%」是 **松闸门 + 旧吞句/silero**；本表是 **紧闸门 + 吞句修复 + TEN**。方向一致（先修 ASR），幅度以本表为准。

---

## 15 句复核

`docs/ledger_review_15_tight.jsonl`（model-judge，含 reason）

---

## 默认变更一览

| 项 | 旧 | 新 | 依据 |
|----|----|----|------|
| `ENDPOINT_HOLD_TIMEOUT_SEC` | （无） | **3.0** | Phase B |
| `VAD_BACKEND` | silero | **ten** | Phase C 表 ten 行 |
| `VAD_SILERO_THRESHOLD` | 0.85 | 0.85 | 不降阈 |
| judge gates | 仅长度/CJK/重复 | +lang_flip +asr_wer +density | Phase D |

---

## 下一个最大桶

1. 剩余 **~16** 句 VAD 仍不开 / 开了仍高 WER  
2. 流式 ASR 质量（oneshot offline 仍明显更好，但本轮不改 `prefer_2pass`）  
3. 勿再堆 postedit 刷分
