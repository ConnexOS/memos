# MEMOS 一键安装脚本 (Windows PowerShell)
param(
    [switch]$Help,
    [string]$VenvPath = "$PWD\venv",
    [string]$ModelName = "bge-large-zh-v1.5",
    [string]$Mirror = ""  # 如 "https://hf-mirror.com"
)

if ($Help) {
    Write-Host @"
MEMOS 一键安装脚本 (Windows)

用法:
  .\install.ps1                     # 默认安装
  .\install.ps1 -Mirror "https://hf-mirror.com"  # 使用镜像站
  .\install.ps1 -VenvPath "D:\venv"  # 指定虚拟环境路径

参数:
  -VenvPath    虚拟环境路径 (默认: ./venv)
  -ModelName   嵌入模型名称 (默认: bge-large-zh-v1.5)
  -Mirror      HuggingFace 镜像站 (可选)
"@
    exit 0
}

Write-Host "=== MEMOS 安装脚本 ===" -ForegroundColor Cyan

# 检查 Python
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "[!!] 未找到 Python，请安装 Python 3.12+" -ForegroundColor Red
    exit 1
}

$ver = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ([version]$ver -lt [version]"3.12") {
    Write-Host "[!!] 需要 Python 3.12+，当前: $ver" -ForegroundColor Red
    exit 1
}

# 创建虚拟环境
if (-not (Test-Path $VenvPath)) {
    Write-Host "[..] 创建虚拟环境..." -ForegroundColor Yellow
    & python -m venv $VenvPath
    Write-Host "[OK] 虚拟环境已创建" -ForegroundColor Green
} else {
    Write-Host "[OK] 虚拟环境已存在" -ForegroundColor Green
}

$pip = "$VenvPath\Scripts\pip"

# 升级 pip
& $pip install --upgrade pip

# 安装 MEMOS
if ($Mirror) {
    $env:HF_ENDPOINT = $Mirror
    Write-Host "[..] 使用镜像: $Mirror" -ForegroundColor Yellow
}

& $pip install memomate

# 初始化
Write-Host "[..] 运行 memos init..." -ForegroundColor Yellow
& "$VenvPath\Scripts\memos" init --force

Write-Host @"

=== 安装完成 ===

启动:
  .\venv\Scripts\memos dashboard

首次使用:
  浏览器访问 http://127.0.0.1:8000
"@ -ForegroundColor Cyan
