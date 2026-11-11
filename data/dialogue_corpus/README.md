# dialogue_corpus

双人对话验收集（当前产品 KPI）。说明见 `docs/DIALOGUE_CORPUS.md`。

**主来源：Microsoft MSLT v1.1**（公开双语对话 + 互译参考），不是从零录音。

1. 下载 https://www.microsoft.com/en-us/download/details.aspx?id=55951 → 存为 `MSLT_Corpus.zip`
2. `.venv\Scripts\python.exe scripts\import_mslt_dialogue.py --zip data\dialogue_corpus\MSLT_Corpus.zip`

状态：等 zip 到位后导入。
