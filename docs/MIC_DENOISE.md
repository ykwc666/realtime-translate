# 麦侧 GTCRN 降噪（P1-6）

日期：2026-11-04

## 状态

- 模型：`models/gtcrn_simple.onnx`（≈523KB，已下载）
- 封装：`app/denoise.py` → `OnlineSpeechDenoiser`
- 接入：`run_vad` / duplex 麦线程；环回不加
- **默认 `MIC_DENOISE=False`**（干净合成 wav 无收益；实麦风扇/键盘噪再开）

## 用法

```powershell
.\.venv\Scripts\python.exe scripts\download_gtcrn.py   # 已有可跳过
# config.py: MIC_DENOISE = True
python run.py --mode vad
```

流式帧移 256 samples（16ms @16kHz）；首帧可能空输出，随后 1:1。

## 待测

实麦开/关对比末字完整率与误触发；合成 `ab_weekday` 不必开。
