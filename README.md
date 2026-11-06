# realtime-translate

## 一、这是什么

对着麦克风说中文，屏幕上出英文字幕、耳机里念英文；会后再出一份双语稿。  
全本地、CPU 跑、不联网。资源默认在 `D:\xuanjian\realtime-translate`。

## 二、三条命令

### 1）实时字幕（主用法）

```bat
cd /d D:\xuanjian\realtime-translate
.venv\Scripts\python.exe run.py --mode vad --subtitle-serve
```

浏览器打开 <http://127.0.0.1:8765/> ，按 F11 全屏。  
戴耳机（外放会被麦克风收回去）；Ctrl+C 退出；退出后字幕页停在最后状态。

成功时你会看到：终端出现 `双轨实机 VAD：...`（或同类 VAD 启动行），浏览器里说话时灰字逐字增长、停顿后变白并沉到屏底。

### 2）会后重译（把刚录的会变成双语稿）

```bat
cd /d D:\xuanjian\realtime-translate
.venv\Scripts\python.exe run.py --post latest
```

产物：`data/sessions/<id>/transcript.md`（双语）、`transcript.srt`、`transcript.jsonl`。  
注意：只对 `vad` / `duplex` 录的会话有效；`dualsim` 回放不落 `mic_raw`，不能用 `--post`。

成功时你会看到：终端打印重译进度，并写出上述文件；`report.json` 里有句数与对照摘要。

### 3）离线 wav（不进麦克风，直接喂文件）

```bat
cd /d D:\xuanjian\realtime-translate
.venv\Scripts\python.exe run.py --mode file --file 路径.wav --no-tts
```

成功时你会看到：终端逐句打出 `[utt] src=... | tgt=...`，无 TTS 播报。

也可双击仓库根目录的 `start.bat`（等同第 1 条：自动开字幕页 + VAD）。

## 三、屏幕怎么读

- **灰字**：正在说（partial）  
- **白色大字**：刚定稿的一句（final）  
- **上面的小字**：之前说过的历史（旧 → 新，最多 5 行）  

英文只在句子说完后整句出现，不会逐字出（MT 是句尾触发的）。

## 四、已知局限

- **语言方向**：实时只做中→英；英→中走 `--mode loopback`（对端声卡）。  
- **识别质量**：会议远场语料 AMI **27/48**、安静朗读 FLEURS **11/12**（句子级松闸门）。AMI 主要失分在远场识别上限，不是翻译。WER **0.4433–0.4485**（约 1/60 条流式 ASR 不可复现）。  
- **延迟**：dualsim 实测 `mt_ready` 中位约 **600ms**（含 ASR 等句尾）；实麦受设备影响。  
- **字幕历史**最多 5 行，超出裁掉最旧的。  
- **会后稿时间戳**按字符比例估算（`report.json` 里标 `approximate`），不是强制对齐。  
- **长稳**：38.5 分钟音频、约 18× 速、加载后稳态 **+2.2 MB/min**、无线程增长；未做 1× 连续 1 小时实测。详见 `docs/SOAK_LONG.md`。  
- **一句话说清**：单句超过 15 秒会被强切，静音 0.45 秒收句。

## 五、装起来（新机器 / 换机器时才需要）

`.venv` 与 `models\`、`cache\modelscope\` 都在本机目录，换机器要重新准备：

```bat
cd /d D:\xuanjian\realtime-translate
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
.venv\Scripts\python.exe scripts\download_models.py
.venv\Scripts\python.exe scripts\download_opus_mt.py
.venv\Scripts\python.exe scripts\download_piper_voices.py
.venv\Scripts\python.exe scripts\convert_opus_ct2.py
.venv\Scripts\python.exe scripts\download_sherpa_sensevoice.py
```

国内源约定：pip 用清华；FunASR / SenseVoice 走 ModelScope；Opus-MT / Piper 可用 `https://hf-mirror.com`。

### 已下载模型路径

| 组件 | 来源 | 路径 |
|------|------|------|
| 流式 ASR Paraformer | ModelScope | `cache\modelscope\models\iic--speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online` |
| VAD fsmn-vad | ModelScope | `cache\modelscope\models\iic--speech_fsmn_vad_zh-cn-16k-common-pytorch` |
| Opus-MT zh→en | hf-mirror | `models\opus-mt-zh-en` |
| Opus-MT en→zh | hf-mirror | `models\opus-mt-en-zh` |
| TTS Piper 中/英 | hf-mirror | `models\piper\zh_CN-huayan-medium.onnx` / `en_US-lessac-medium.onnx` |
| Opus-MT CT2 int8 | 本地转换 | `models\opus-mt-*-ct2` |
| SenseVoiceSmall | ModelScope | `cache\modelscope\...\SenseVoiceSmall` |
| SenseVoice int8 ONNX (sherpa) | hf-mirror | `models\sherpa-sensevoice\` |
| Silero VAD onnx | hf-mirror | `models\silero_vad.onnx` |

默认：`ASR=sherpa_paraformer_online`，`MT=ct2`，`TTS=Piper`（见 `app/config.py`）。

## 六、开发

测速记账：

```bat
cd /d D:\xuanjian\realtime-translate
.venv\Scripts\python.exe scripts\bench.py --tag sensevoice-ct2 --note "说明这次改了什么"
```

历史见 `docs/OPTIMIZATION_LOG.md` 与 `docs/bench_history.jsonl`。

**当前冻住基线**：`docs/BASELINE_FINAL_ONLY.md`（final_only，句尾整句 MT）。

其它常用命令：

```bat
cd /d D:\xuanjian\realtime-translate

REM 半双工：说完按 Enter
.venv\Scripts\python.exe run.py --mode ptt

REM 实机 VAD（同时只开一个进程）
.venv\Scripts\python.exe run.py --mode vad

REM 双轨仿真（网上/本地 wav → 16k mono）
.venv\Scripts\python.exe run.py --mode dualsim --file path\to\audio.wav --no-tts
.venv\Scripts\python.exe scripts\bench_dualsim.py --wav path\to\audio.wav --tag dualsim-xxx --note "说明"
.venv\Scripts\python.exe scripts\ab_completion_rounds.py

REM 只识别+翻译，不播报
.venv\Scripts\python.exe run.py --mode ptt --no-tts

REM 指定会话做会后重译
.venv\Scripts\python.exe run.py --post data\sessions\20261105-160805
```

说明：

- TTS 用 **Piper 离线合成**，不联网。  
- 半双工：播报时会静音麦克风，减轻回声。  
- 仿真测断句/收句：把音频转成 **16kHz mono wav** 后跑 `dualsim`。  
- **基线说明**：`docs/BASELINE_FINAL_ONLY.md`  
- **待优化清单**：`docs/OPTIMIZATION_ROADMAP.md`（其中 REINA 中途段为历史，以 BASELINE 为准）  
- **实麦回归**：`docs/REAL_MIC_REGRESSION_20261106.md`  
- **长稳 soak**：`docs/SOAK_LONG.md`  
- **会后重译细节**：`docs/OFFLINE_RETRANSCRIBE.md`  
- **字幕契约**：`docs/SUBTITLE.md`
