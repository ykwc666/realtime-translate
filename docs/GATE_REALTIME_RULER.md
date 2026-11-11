# Gate realtime 尺子报告

wav: `D:\xuanjian\realtime-translate\cache\ab_weekday\accept_phrases.wav`
测法: 分进程 A/B 交替 ×5，statistics.median / 最近秩 p90；repeat=2

## campaign 1

- n: 有声 5 / 总行 5
- p50: 2.649s → 2.634s (probe on → off)
- p90: 2.742s → 2.699s
- 5 轮 Δ: 0.01% / 1.67% / 4.34% / 0.27% / 0.59%
- median Δ: 0.59%

## campaign 2

- n: 有声 5 / 总行 5
- p50: 2.625s → 2.610s (probe on → off)
- p90: 2.703s → 2.665s
- 5 轮 Δ: 1.55% / 3.40% / 0.14% / 1.48% / 2.78%
- median Δ: 1.55%

## 尺子复现

两次 campaign 的 p50_off 相对差 = **0.91%**（验收 ≤3%）

## 复现命令

```bat
cd /d D:\xuanjian\realtime-translate
.venv\Scripts\python.exe scripts\gate_realtime_regression.py --rounds 5 --repeat 2
```


结果: **GATE_REALTIME_OK**
