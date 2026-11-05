# 给 Java 后端同学的项目学习指南

> 假定你熟悉 Spring / 接口 / 线程池 / 消息队列，Python 只写过一点业务脚本。  
> 目标：看完能在脑子里画出「一句话从麦克风到耳机」怎么走，并知道该从哪个文件读起。

---

## 1. 一句话：这项目是啥？

**本机中英通话翻译器**：你说中文 → 耳机出英文；对面说英文（系统播放声）→ 你听中文。

- 全在 **本机 CPU** 跑（无云、无 GPU 也行）。
- **半双工为主**：一句说完再译再播（不是同声传译那种边说边出字）。
- 入口：`run.py`；核心包：`app/`。

可以把它想成一个 **没有 HTTP、却一直在跑的本地流水线服务**：输入是音频块，输出是扬声器声音。

---

## 2. 先用 Java 世界做类比

| 本项目概念 | 大致像 Java 里什么 | 别误会成什么 |
|------------|-------------------|--------------|
| `run.py` | `main` / 启动类 | 不是 Controller |
| `app/config.py` | `application.yml` + 一堆常量 | 改了要重启进程 |
| `TranslatePipeline` | 组装 Bean 的 `Application` / Facade | 真正干活的是 Engine |
| `DualTrackEngine` | 核心状态机 / 编排器 | 每路音频一条 Engine |
| `MicStream` / `LoopbackStream` | 从消息中间件 **poll 消息** | 每条「消息」是 100ms 音频 |
| `feed_chunk()` | 消费一条消息并推进状态 | 热路径，别在这里同步写盘 |
| VAD（静音检测） | 「这条业务何时完结」的判定 | 相当于超时关单 |
| ASR | 非结构化输入 → 文本 | 像 OCR，但是语音 |
| MT（翻译） | 纯函数服务：`String in → String out` | 可加缓存（本项目叫 TM） |
| TTS | 文本 → 音频播放 | 像异步发短信，有队列 |
| `TtsQueue` | `Executor` + 有界队列 | 播报别堵采集线程 |
| `data/*.jsonl` / harvest | 业务日志 / 审计流水 | 一行一句 |
| `scripts/bench_*.py` | 压测 + 回归脚本 | 不是单元测试全部 |

**最大思维差：**

- Java Web：一次请求 → 线程处理 → 返回 JSON → 线程结束。
- 这里：进程常驻 → **音频块持续涌入** → 状态机跨很多块攒成「一句」→ 才翻译播放。

更像 **Flink/Storm 流处理**，不像一次性 RPC。

---

## 3. 目录地图（只记这些就够）

```
realtime-translate/
├── run.py                 # 启动入口（选模式）
├── app/                   # 运行时代码（重点）
│   ├── config.py          # 所有开关与路径
│   ├── pipeline.py        # 组装模型 + 启动麦/环回循环
│   ├── dual_track.py      # ★ 一句如何收尾、译、播（核心）
│   ├── asr_stream.py      # 语音识别
│   ├── vad_stream.py      # 语音活动检测（是否在说话）
│   ├── translate.py       # 翻译（TM → 模型 → postedit）
│   ├── mt_tm.py / mt_postedit.py  # 术语表 / 规则修补
│   ├── tts_play.py / tts_queue.py # 语音合成与异步播放
│   ├── audio_io.py        # 麦、环回、读 wav
│   └── harvest.py         # 旁路采集（不影响延迟）
├── data/                  # 评测集、TM、采集 jsonl
├── models/                # 大模型文件（本地）
├── cache/                 # 缓存、锁、bench 音频（常被 gitignore）
├── scripts/               # 下载模型、A/B 压测、评测
├── tests/                 # pytest
└── docs/                  # 优化记录与本指南
```

**学习顺序建议（3 个晚上）：**

1. `run.py` → `pipeline.py`（怎么启动）  
2. `dual_track.py` 里 `feed_chunk` / `_finalize_utt`（一句怎么结束）  
3. `translate.py` + `tts_queue.py`（译和播）

先别钻进所有 `docs/*_AB.md`，那些是调参考古。

---

## 4. 主链路：一句话怎么走完？

旗舰场景：**耳机听英文 → 出中文**（`--mode loopback`）。

```
系统扬声器正在播英文
        │
        ▼
 LoopbackStream          ← 像「订阅默认声卡的环回主题」
  每 ~100ms 一块 float32 音频
        │
        ▼
 DualTrackEngine.feed_chunk(chunk)
   ├─ VAD：这块是语音还是静音？
   ├─ 还在说：塞进缓冲区；偶尔跑 ASR 出「字幕草稿」
   └─ 静音够久 / 仲裁说收句：
         │
         ▼
      _finalize_utt()     ← 「关单」
         ├─ ASR 定稿（可能有投机/二遍）
         ├─ Translator.translate()  → 中文
         └─ TtsQueue 异步播报
```

麦说中文出英文（`--mode vad`）同一套 Engine，只是 `force_src_lang="zh"`。

### 4.1 和「一次 HTTP」对照

| 阶段 | 实时翻译 | HTTP API |
|------|----------|----------|
| 开始 | VAD 检测到开始说话 | 收到请求 |
| 进行中 | 多块音频 + 部分识别 | Controller 调 Service |
| 结束条件 | 静音 0.45s 等 | 方法 return |
| 响应 | TTS 往声卡写 PCM | `ResponseEntity` |
| 超时/打断 | 人又开口 → 取消播报 | 客户端取消 / 超时 |

---

## 5. 四大件（模型）——用「外包服务」理解

每块都可以想成 **本地进程里的重型依赖库**（不是 REST）：

| 组件 | 干什么 | 本项目默认 | 配置里大致叫啥 |
|------|--------|------------|----------------|
| **VAD** | 有没有人在说话、何时说完 | Silero ONNX | `VAD_*` |
| **ASR** | 语音 → 文字 | Sherpa 流式 Paraformer | `ASR_*` |
| **MT** | 中↔英文字 | Opus-MT 的 CTranslate2 int8 | `MT_*` |
| **TTS** | 文字 → 语音 | Piper | `TTS_*` |

**为什么慢？** 全是矩阵运算，吃 CPU。所以：

- 线程数要拆（`ASR_THREADS_ONLINE/OFFLINE`），两路同时跑会抢核——像两个 JVM 都把 `ForkJoinPool` 开满。
- 翻译有 **TM（翻译记忆）**：精确命中则跳过模型，≈ Redis 缓存命中。
- **postedit**：模型出完后再跑几条正则/模板修正（通话黑话），≈ 出库前的 `Validator` + 文案修补；**只对固定句式有用**。

公开英文语音实测过：规则层命中率接近 0，真实场景里 **ASR 错了，MT 再强也救不了**。详见 `docs/HARVEST_PUBLIC_EN_2026-11-04.md`。

---

## 6. 几个「业务规则」必须懂

### 6.1 `final_only`（冻住基线）

**中途不翻译**，等一句收干净再整句 MT。  

类比：不要在用户还在打字时就提交订单；等「发送」再下单。  
文档：`docs/BASELINE_FINAL_ONLY.md`。

### 6.2 投机定稿（speculate）

人还没完全停，系统预判「可能要说完了」，**提前跑 ASR 定稿**；真收句时若猜对就省等待。  

类比：订单将关时预热库存扣减；取消则丢弃预热结果。  
相关：`ASR_SPECULATE_*`、`docs/SPEC_REARM.md`。

### 6.3 双通道 duplex

- 麦：`zh → en`  
- 环回：`en → zh`  
两路各一个 `DualTrackEngine`，像两个消费者并行。

**坑：** TTS 若播到「环回正在听」的同一扬声器，会把自己的翻译再识别一遍（回声）。要用不同输出设备，或静音垫（缓兵）。

### 6.4 延迟指标别搞混

| 字段 | 含义 | 别当成 |
|------|------|--------|
| `latency_perceived_ms` / mt_ready | 停说 → 译文就绪 | 耳机里听到声音 |
| `latency_device_ttfa_ms` | 停说 → 声卡真正出声 | 纯计算耗时 |
| `asr_wait_ms` | 收句点 → ASR 定稿完 | 整句端到端 |

优化时一定要问：**你优化的是哪一列？** 项目里大量 A/B 就是为了防止自嗨。

---

## 7. 启动与模式（上手命令）

```powershell
cd D:\xuanjian\realtime-translate
.\.venv\Scripts\Activate.ps1

python run.py --mode ptt          # 说完按 Enter（最好懂）
python run.py --mode vad          # 麦 → 英
python run.py --mode loopback     # 系统声音英文 → 中
python run.py --mode duplex       # 两路一起
python run.py --mode file --file x.wav --no-tts   # 离线跑 wav
```

采集真实效果（旁路日志，不改翻译逻辑）：

```powershell
python scripts\harvest_session.py --mode loopback --source-desc "说明音源"
```

契约在 `app/harvest.py` 注释里。

---

## 8. 代码怎么读（对照 Spring）

### 8.1 依赖注入？没有 Spring

`TranslatePipeline.__init__` 里 **手动 new**：

```text
StreamingAsr() + Translator() + TtsPlayer()
```

≈ 手写 `@Configuration` 里 `new` Bean，没有注解扫描。

### 8.2 配置

全是 `config.py` 顶层常量，改完重启。  
≈ 改 `application.yml` 后重启 Jar（这里没有 Spring Cloud 动态刷新）。

### 8.3 线程模型（简化）

- **采集循环**（主路径）：`for chunk in mic.chunks(): engine.feed_chunk(...)`  
- **TTS 工作线程**：队列里合成+播放，尽量别堵采集  
- **ASR/MT**：多数在 finalize 路径上同步算（贵！所以才有投机、并行二遍、线程数拆分）

Java 直觉：别在 Kafka `poll` 循环里同步调很重的 HTTP；这里同理——finalize 里已经很重了，所以播放要异步。

### 8.4 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

≈ `mvn test`。实时音频很难单测，所以大量逻辑用合成 wav + bench 脚本做 A/B。

---

## 9. 项目「工程文化」（值得学的部分）

即使你不做语音，这些习惯也值得搬到 Java 项目：

1. **冻住基线**：`BASELINE_FINAL_ONLY.md` —— 什么算现行真相，什么是废弃方案。  
2. **优化必须有尺子**：`docs/OPTIMIZATION_INDEX.md` + 各 `*_AB.md` —— 改之前先定义比什么。  
3. **旁路观测**：`harvest` 用队列写盘，热路径只 `put` —— 像异步审计日志，不拖接口 RT。  
4. **规则层有边界**：通话 TM/postedit 在自写集可以 100 分，公开语音上可能 0 命中——**评测集分布错了会得出假结论**。

---

## 10. 当前真实瓶颈（学完对照）

| 阶段 | 自写评测集感觉 | 公开英文语音 harvest |
|------|----------------|----------------------|
| 规则/TM | 很强，能「救」很多句 | **几乎 0 命中** |
| 端到端观感 | 通话短句尚可 | ok 很少；ASR 错 → MT 跟着错 |

所以下一刀优先序（产品向）：

1. **ASR / 收句**（真实口音、截断）  
2. 再谈 MT 模型  
3. 少堆只服务自写句子的 postedit  

---

## 11. 迷你术语表

| 词 | 人话 |
|----|------|
| 16k mono | 采样率 16000Hz、单声道（电话级常见） |
| PCM / float32 | 原始波形采样，还没压成 mp3 |
| ONNX / CT2 | 模型推理加速格式（把大模型变「能较快跑的引擎」） |
| int8 | 量化：精度略换速度/内存 |
| 环回 loopback | 把「扬声器正在播的内容」再录进来 |
| jsonl | 一行一个 JSON，好追加日志 |

---

## 12. 建议你亲手做的 3 个小实验

1. `python run.py --mode ptt`：说「你好」，看终端里 ASR → MT → TTS 打印顺序。  
2. 打开 `app/translate.py` 的 `translate()`：找出 TM 命中时为什么几乎 0ms。  
3. 读 `docs/HARVEST_PUBLIC_EN_2026-11-04.md`：对照「自写集很美」和「公开语音很惨」。

做完这三步，你就已经超过「只会跑 demo」的阶段了。

---

## 13. 还想深入时读什么

| 目的 | 文档 |
|------|------|
| 现行产品行为 | `docs/BASELINE_FINAL_ONLY.md` |
| 优化历史索引 | `docs/OPTIMIZATION_INDEX.md` |
| 架构与坑（偏深） | `docs/ARCHITECTURE_REVIEW.md` |
| 采集评测怎么用 | `docs/HARVEST_SESSION.md` |

有问题对着代码搜关键字比闷读更快：`feed_chunk`、`_finalize_utt`、`lookup_tm`、`speculate`。
