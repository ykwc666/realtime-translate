# Cursor 任务：双人对话 KPI 基线（MSLT）

**只量数，不改热路径、不动 VAD 三阈值、不开 MIC_DENOISE。**

## 目标

对 `data/dialogue_corpus/manifest.jsonl`（60 条：zh_en 30 + en_zh 30）跑一遍现有 dual-track 管线，**两个方向分开报**：

| 指标 | 含义 |
|---|---|
| ASR 错率 | zh→en 用字错率 CER；en→zh 用词错率 WER（对照 `src_ref`） |
| 译文可用率 | 对照 `tgt_ref` 的粗闸门 ok 比例（能直接用 ≈ ok） |
| 半句/碎切 | 单条 wav 产出 `n_utts≥2` 比例；`audio_sec<1s` 短句比例；ASR 覆盖率 &lt;0.6 的「残缺」比例 |

## 交付

1. `scripts/eval_dialogue_mslt.py`（可复现）
2. `docs/DIALOGUE_BASELINE_MSLT.md`（分方向表 + 最大短板一句话）
3. 明细：`data/eval/dialogue_mslt/detail.jsonl`（已 gitignore `data/eval/`）

## 禁止

改 `app/config.py` 默认、改 VAD、改切句逻辑「先修再测」。本轮纯基线。
