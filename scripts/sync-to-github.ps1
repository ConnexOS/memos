# scripts/sync-to-github.ps1
# SVN → GitHub 筛选同步脚本
# 用法: .\scripts\sync-to-github.ps1 [-DryRun] [-TargetRepo D:/MyGitHub/MEMOS]

param(
    [switch]$DryRun,
    [string]$TargetRepo = "D:/MyGitHub/MEMOS"
)

$includePaths = @(
    "src/memos/",
    "tests/",
    "docs/",
    "scripts/",
    "pyproject.toml",
    "CHANGELOG.md",
    "CLAUDE.md",
    "LICENSE",
    "README.md",
    "README.zh.md",
    ".github/",
    ".gitignore",
)

$excludePatterns = @(
    "__pycache__", ".pytest_cache", "*.pyc",
    "venv/", ".venv/",
)

Write-Host "=== MEMOS → GitHub 同步 ===" -ForegroundColor Cyan
Write-Host "目标: $TargetRepo"
if ($DryRun) {
    Write-Host "模式: DRY RUN（仅预览）" -ForegroundColor Yellow
}

# 1. 创建目标目录
if (-not (Test-Path $TargetRepo)) {
    if ($DryRun) {
        Write-Host "[DRY-RUN] 创建目录: $TargetRepo"
    } else {
        New-Item -ItemType Directory -Path $TargetRepo -Force | Out-Null
        Write-Host "[OK] 已创建: $TargetRepo"
    }
}

# 2. 同步文件
$fileCount = 0
foreach ($path in $includePaths) {
    $src = Resolve-Path $path -ErrorAction SilentlyContinue
    if (-not $src) {
        Write-Host "[!!] 不存在: $path"
        continue
    }
    $dest = Join-Path $TargetRepo $path
    $destDir = Split-Path $dest -Parent
    if ($DryRun) {
        Write-Host "[DRY-RUN] Copy $src -> $dest"
    } else {
        if (-not (Test-Path $destDir)) {
            New-Item -ItemType Directory -Path $destDir -Force | Out-Null
        }
        Copy-Item -Path $src -Destination $dest -Recurse -Force
        $fileCount++
    }
}

if (-not $DryRun) {
    Write-Host "[OK] 已同步 $fileCount 个路径到 $TargetRepo" -ForegroundColor Green
}

# 3. 生成脱敏 config.example.json
$examplePath = Join-Path $TargetRepo "etc" "config.example.json"
if (-not (Test-Path $examplePath) -and (Test-Path "etc/config.json")) {
    if ($DryRun) {
        Write-Host "[DRY-RUN] 生成脱敏配置: $examplePath"
    } else {
        $example = Get-Content "etc/config.json" -Raw | ConvertFrom-Json
        # 脱敏
        $example.llm.api_key = "your-api-key-here"
        foreach ($ep in $example.llm.endpoints) {
            $ep.api_base = "http://localhost:11434/v1"
            $ep.api_key = ""
        }
        $example.auth.token_hash = ""
        $example.auth.secret_key = ""
        $destDir = Split-Path $examplePath -Parent
        if (-not (Test-Path $destDir)) {
            New-Item -ItemType Directory -Path $destDir -Force | Out-Null
        }
        $example | ConvertTo-Json -Depth 10 | Set-Content $examplePath -Encoding utf8
        Write-Host "[OK] 已生成脱敏配置: $examplePath" -ForegroundColor Green
    }
}

# 4. 排除敏感/临时文件
$gitignorePath = Join-Path $TargetRepo ".gitignore"
if ((Test-Path $gitignorePath) -and -not $DryRun) {
    Add-Content -Path $gitignorePath -Value "`netc/config.json" -NoNewline
    Write-Host "[OK] .gitignore 已追加 etc/config.json"
}

Write-Host "=== 同步完成 ===" -ForegroundColor Cyan
