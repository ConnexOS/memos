param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$TargetDir
)

$SourceRoot = "D:\DevSpace\MEMOS"
$ErrorActionPreference = "Stop"

# 规范化目标路径
$TargetDir = [System.IO.Path]::GetFullPath($TargetDir)

if (-not (Test-Path $TargetDir)) {
    Write-Host "创建目标目录: $TargetDir" -ForegroundColor Yellow
    New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
}

Write-Host "=" * 50
Write-Host "  memos 配置同步工具"
Write-Host "  源: $SourceRoot"
Write-Host "  目标: $TargetDir"
Write-Host "=" * 50

# 1. 复制 .mcp.json
$srcMcp = [System.IO.Path]::Combine($SourceRoot, ".mcp.json")
$dstMcp = [System.IO.Path]::Combine($TargetDir, ".mcp.json")
if (Test-Path $srcMcp) {
    Copy-Item -Path $srcMcp -Destination $dstMcp -Force
    Write-Host "[OK] .mcp.json" -ForegroundColor Green
} else {
    Write-Host "[WARN] .mcp.json 不存在，跳过" -ForegroundColor Yellow
}

# 2. 复制 .claude/settings.json
$srcSettings = [System.IO.Path]::Combine($SourceRoot, ".claude", "settings.json")
$dstClaude = [System.IO.Path]::Combine($TargetDir, ".claude")
$dstSettings = [System.IO.Path]::Combine($dstClaude, "settings.json")
if (Test-Path $srcSettings) {
    if (-not (Test-Path $dstClaude)) { New-Item -ItemType Directory -Path $dstClaude -Force | Out-Null }
    Copy-Item -Path $srcSettings -Destination $dstSettings -Force
    Write-Host "[OK] .claude\settings.json" -ForegroundColor Green
} else {
    Write-Host "[WARN] .claude\settings.json 不存在，跳过" -ForegroundColor Yellow
}

# 3. 复制 .claude/skills/
$srcSkills = [System.IO.Path]::Combine($SourceRoot, ".claude", "skills")
$dstSkills = [System.IO.Path]::Combine($dstClaude, "skills")
if (Test-Path $srcSkills) {
    if (-not (Test-Path $dstSkills)) { New-Item -ItemType Directory -Path $dstSkills -Force | Out-Null }
    Copy-Item -Path "$srcSkills\*" -Destination $dstSkills -Recurse -Force
    Write-Host "[OK] .claude\skills\" -ForegroundColor Green
} else {
    Write-Host "[WARN] .claude\skills\ 不存在，跳过" -ForegroundColor Yellow
}

# 4. 复制 .claude/hooks/
$srcHooks = [System.IO.Path]::Combine($SourceRoot, ".claude", "hooks")
$dstHooks = [System.IO.Path]::Combine($dstClaude, "hooks")
if (Test-Path $srcHooks) {
    if (-not (Test-Path $dstHooks)) { New-Item -ItemType Directory -Path $dstHooks -Force | Out-Null }
    Copy-Item -Path "$srcHooks\*" -Destination $dstHooks -Recurse -Force
    Write-Host "[OK] .claude\hooks\" -ForegroundColor Green
} else {
    Write-Host "[WARN] .claude\hooks\ 不存在，跳过" -ForegroundColor Yellow
}

Write-Host "=" * 50
Write-Host "完成!" -ForegroundColor Green
