# Gate realtime 尺子报告

wav: `D:\xuanjian\realtime-translate\cache\ab_weekday\accept_phrases.wav`
测法: 分进程 A/B 交替 ×5，statistics.median / 最近秩 p90；repeat=2

## campaign 1

- n: 有声 5 / 总行 5
- p50: 2.570s → 2.541s (probe on → off)
- p90: 2.718s → 2.569s
- 5 轮 Δ: 1.91% / 5.78% / 1.13% / 3.29% / 0.96%
- median Δ: 1.91%

## campaign 2

- n: 有声 5 / 总行 5
- p50: 2.570s → 2.492s (probe on → off)
- p90: 2.594s → 2.630s
- 5 轮 Δ: 1.57% / 2.78% / 2.31% / 5.86% / 2.30%
- median Δ: 2.31%

## 尺子复现

两次 campaign 的 p50_off 相对差 = **2.00%**（验收 ≤3%）

## 复现命令

```bat
cd /d D:\xuanjian\realtime-translate
.venv\Scripts\python.exe scripts\gate_realtime_regression.py --rounds 5 --repeat 2
```


结果: **GATE_REALTIME_OK**
