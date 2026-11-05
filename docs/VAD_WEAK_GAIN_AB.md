# VAD_WEAK_GAIN_MAX A/B（TEN + RMS=0.035）

弱峰窗（peak < 0.03）才放大送入 VAD/RMS；ASR 仍吃原音频。

| gain | rescued | normal_ok | false_open | synth_same |
|------|---------|-----------|------------|------------|
| 0.0 | 0/16 | 25/25 | 0 | 3/3 |
| 2.0 | 2/16 | 25/25 | 0 | 2/3 |
| 3.0 | 8/16 | 25/25 | 0 | 2/3 |
| 4.0 | 15/16 | 25/25 | 1 | 2/3 |
| 6.0 | 16/16 | 25/25 | 1 | 2/3 |

**脚本自动推荐**：`None`（严格 `synth_same==3` 未过）

**曾试默认 gain=3.0 → 已回滚**

| 阶段 | AMI pipeline ok | FLEURS | empty n_utts |
|------|-----------------|--------|--------------|
| before（gain=0） | **20/48 = 41.7%** | 9/12 = 75% | 16 |
| after（gain=3） | **12/48 = 25.0%** | 11/12 = 91.7% | 8 |

空检是少了，但正常 AMI 句被切碎：翻到 ok +7、**掉出 ok −13**。  
噪声门虽过，**ledger 紧闸门否决**。默认保持 `VAD_WEAK_GAIN_MAX = 0`；代码路径保留便于以后更严的门控（如连续弱峰帧计数）再试。

对照：`docs/ledger_eval_summary_before_weak_gain.json` vs `docs/ledger_eval_summary_after_weak_gain.json`。

落盘：`docs/vad_weak_gain_ab.json`
