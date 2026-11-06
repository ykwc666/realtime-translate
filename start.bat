@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [错误] 未找到 .venv\Scripts\python.exe
  echo 请先按 README「五、装起来」创建虚拟环境并下载模型。
  pause
  exit /b 1
)

echo ========================================
echo   realtime-translate  实时中英字幕
echo ========================================
echo.
echo 演示可说（说完一句停约 0.5 秒，再等英文）：
echo   1. 你好，延迟大概有多少？
echo   2. 今天天气怎么样？现在是星期二。
echo   3. 我明天上午10点要去客户那边开一个审批会，大概两个小时。
echo   4. 我们需要把这个方案再对一遍，然后发给产品。
echo   5. 帮我把这个文件发给他，再确认一下时间。
echo.
echo 戴耳机；说完一句停 0.5 秒，屏幕才会出英文译文。
echo 字幕页约 3 秒后自动打开：http://127.0.0.1:8765/  （可按 F11 全屏）
echo Ctrl+C 结束。
echo.

start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8765/"

.venv\Scripts\python.exe run.py --mode vad --subtitle-serve
echo.
pause
