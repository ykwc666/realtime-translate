$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$Pip = Join-Path $Root ".venv\Scripts\pip.exe"
$PipCache = Join-Path $Root "cache\pip"
$Mirror = "https://pypi.tuna.tsinghua.edu.cn/simple"
$Trusted = "pypi.tuna.tsinghua.edu.cn"

$env:PIP_CACHE_DIR = $PipCache
$env:MODELSCOPE_CACHE = Join-Path $Root "cache\modelscope"
$env:HF_HOME = Join-Path $Root "cache\huggingface"
$env:HUGGINGFACE_HUB_CACHE = Join-Path $Root "cache\huggingface\hub"
$env:HF_ENDPOINT = "https://hf-mirror.com"
$env:MODELSCOPE_DOMAIN = "www.modelscope.cn"

Write-Host "== pip upgrade =="
& $Pip install -U pip setuptools wheel -i $Mirror --trusted-host $Trusted

Write-Host "== torch CPU (清华源) =="
& $Pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
# 若官方 CPU 轮慢，回退清华：
if ($LASTEXITCODE -ne 0) {
  & $Pip install torch torchaudio -i $Mirror --trusted-host $Trusted
}

Write-Host "== project deps =="
& $Pip install funasr modelscope transformers sentencepiece sacremoses soundfile sounddevice numpy edge-tts huggingface_hub -i $Mirror --trusted-host $Trusted

Write-Host "== download models =="
& $VenvPython (Join-Path $PSScriptRoot "download_models.py")
Write-Host "DONE"
