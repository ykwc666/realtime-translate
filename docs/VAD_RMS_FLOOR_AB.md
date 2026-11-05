# VAD_SPEECH_RMS_MIN A/B（TEN 后端）

对照集：当前 `ledger_eval_detail.jsonl` 中 `n_utts=0` 空检 + pipeline ok 子集 + 噪声/合成。

| floor | rescued | normal_ok | false_open | synth_same |
|-------|---------|-----------|------------|------------|
| 0.035 | 0/16 | 25/25 | 0 | 3/3 |
| 0.02 | 3/16 | 25/25 | 1 | 3/3 |
| 0.015 | 3/16 | 25/25 | 1 | 3/3 |
| 0.01 | 12/16 | 25/25 | 1 | 3/3 |
| 0.005 | 15/16 | 25/25 | 2 | 3/3 |
| 0.0 | 16/16 | 25/25 | 3 | 3/3 |

**推荐**：`None` — no candidate met gates — keep VAD_SPEECH_RMS_MIN=0.035

落盘：`docs/vad_rms_floor_ab.json`

## 解读

压低绝对 RMS 地板能救回空检（0.01→12/16），但 **keyboard 噪声一律 false_open≥1**。  
下一步改走 **弱峰自适应 VAD 增益**（`VAD_WEAK_GAIN_*`，峰 <0.03 才放大送入 VAD），见 `docs/VAD_WEAK_GAIN_AB.md`。
