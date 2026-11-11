# 双人对话验收集（当前产品 KPI）

产品定位（2026-11-11）：**对话式翻译**——两人对话，一中一英，实时互译。  
AMI / FLEURS / LoRA **不是**当前 KPI。

## 不必自己录：用公开语料

网上有，但要挑对类型：

| 语料 | 是什么 | 适不适合当 KPI |
|---|---|---|
| **Microsoft MSLT v1.1**（推荐） | 真人双语**对话**；wav + 转写 + **互译参考**；中↔英 | **适合**。专为 spoken language translation 评测做的 |
| ASCEND / BAAI CS-Dialogue | 双人对话，但是**中英夹杂（code-switch）** | **不适合**。测的是混说识别，不是「一句中文↔一句英文」互译 |
| SCIPPC | 总理记者会交替传译 | 太正式、远场会议腔，更像远期会议室，不当当前 KPI |
| AMI / FLEURS | 会议远场 / 朗读 | 远期参考 / 朗读参考，非 KPI |

**主集：MSLT。** 下载（需点一次微软确认页，约 1.8GB）：

https://www.microsoft.com/en-us/download/details.aspx?id=55951  

或 GitHub 说明：https://github.com/MicrosoftTranslator/MSLT-Corpus  

放到：`data/dialogue_corpus/MSLT_Corpus.zip`（或解压目录），然后：

```bat
cd /d D:\xuanjian\realtime-translate
.venv\Scripts\python.exe scripts\import_mslt_dialogue.py --zip data\dialogue_corpus\MSLT_Corpus.zip --n-zh-en 30 --n-en-zh 30
```

会写出 `data/dialogue_corpus/manifest.jsonl` + `raw/*.wav`（各方向抽样 20–30 条，合计约 40–60）。

参考译文来自 MSLT 官方 T2/T3（人工整理的对话译文），**不是**我们再用机器译一遍。

## 可选补录（领域适配）

若要贴近「笔记本内置麦、0.5–2m 普通房间」，可在 MSLT 过关后再补录少量本机麦样本——那是补丁，不是从零造集。

## 报告口径

中→英 / 英→中 **分开**报 WER + 可用率，禁止合并成一个总分。  
AMI 继续跑时必须写：**远期目标，非当前 KPI**。
