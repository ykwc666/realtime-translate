# 任务书：连续说话不丢（B1）+ 时间译法（B2）

仓库：`D:\xuanjian\realtime-translate`
基线：`dialogue-kpi-v2-freeze`（`275e432`）
性质：**实麦体感**。MSLT 冻结读数原则上不变（评测 `enable_tts=False`，不走麦静音）。

---

## 0. 一句话

你说「翻译时我继续说会丢」——不是 VAD 坏了，是 **TTS 播报期间把麦克风 mute + drain 了**：正在说的、已经进队列的，一律扔掉。

---

## 1. 证据

`app/tts_queue.py` 播每一句前：

```python
if self.mute_cb:
    self.mute_cb(True)   # → MicStream.mute(True)
```

`app/audio_io.py`：

```python
def mute(self, muted: bool = True) -> None:
    if muted:
        self._muted.set()
        self.drain()          # 清空已排队块
# callback 里：muted 则直接 return，新语音也不入队
```

`start.bat` 已要求戴耳机；戴耳机时 **不需要** 为防回授而关麦。关麦是给「扬声器外放 TTS 又被麦录回去」用的，却把连续对话掐死了。

另：`PLAYBACK_MUTE_PAD_SEC=0.50` 在 unmute 前再静音睡 0.5s，雪上加霜。

句尾 ASR+MT 仍在音频线程同步跑（约 0.6–1s），队列深 50 块≈5s，一般够缓冲；**主丢字窗口是整段 TTS 播报**。

---

## 2. B1 改法（本轮主刀）

### 2.1 配置

`app/config.py` 新增：

```python
# 播 TTS 时是否静音麦克风。对话主路径默认 False（戴耳机）。
# 扬声器外放 TTS 且会回灌麦时再开 True。
TTS_MUTE_MIC = False
```

### 2.2 行为

`tts_queue.py`：仅当 `TTS_MUTE_MIC` 为 True 时调用 `mute_cb(True/False)` 与 pad sleep。

`FINALIZE_WAIT_TTS` 保持 `False`（已是）。VAD 三阈值 / 2pass / 降噪不动。

### 2.3 验收

| # | 检查 | 期望 |
|---|---|---|
| A | pytest | ≥177 全绿 |
| B | `git diff` | 不碰 `asr_stream.py`；`config` 只加 `TTS_MUTE_MIC` |
| C | 实麦（戴耳机） | 说句 A → 译文开始播时立刻说句 B → B 应能出识别/译文，不再整句消失 |
| D | MSLT 两集 | `asr_ok`/`mt_ok` 落在冻结容差内（zh_en 零差；en_zh ±1） |
| E | 文档 | `KPI_FREEZE` 已知限制补一句：连续说丢字主因是 TTS 关麦；`TTS_MUTE_MIC=False` 后以实麦为准 |

---

## 3. B2 时间译法（可同轮，小补丁）

现象：`现在是16点31分` → `It's 1631.`

在 `mt_postedit`（zh→en）加确定性规则，例如：

- `(\d{1,2})\s*点\s*(\d{1,2})\s*分` → `\1:\2`（英侧 `It's 16:31`）
- 避免误伤「一点都不」：要求「点」后确有分钟数字

单测 3 条；双集 eval 报 `mt_ok` 差，en_zh 允许 ±1，zh_en 不许因本补丁掉分（只改善）。

若时间规则要动很多词表 → 拆下一轮，本轮可只交 B1。

---

## 4. 明确不做

- 不改 VAD 阈值、不开 MIC_DENOISE
- 不调 ASR_2PASS / prefer_2pass
- 不把 finalize 整段搬线程（下轮再做，若 B1 后仍丢再开）
- 不要求用户「说完等译文」

---

## 5. 若你认为我错了

1. 实麦时 `TTS_MUTE_MIC=False` 仍丢字 → 贴终端：TTS 播报区间是否还有 `[utt]`；若有则不是 mute 问题  
2. 若不戴耳机、扬声器外放 TTS → 开 `TTS_MUTE_MIC=True` 是预期，回授与连续说不可兼得，写进 README
