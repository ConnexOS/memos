# MEMOS 一键升级脚本（Windows PowerShell）
# 用法：.\upgrade-memos.ps1

$ErrorActionPreference = "Stop"
$src = "D:\DevSpace\MEMOS"
$dev_py = "$src\venv\Scripts\python.exe"
$global_py = "$env:USERPROFILE\.pyenv\pyenv-win\versions\3.12.4\python.exe"

Write-Host "=== 升级开发环境 ==="
& $dev_py -m pip install $src --force-reinstall --no-cache-dir
Write-Host "  开发环境: OK"

Write-Host "=== 升级全局环境 ==="
& $global_py -m pip install $src --force-reinstall --no-cache-dir

# 确保模板和 hooks 等非 Python 文件同步到全局 site-packages
$global_site = (& $global_py -c "import sysconfig; print(sysconfig.get_path('platlib'))")
Get-ChildItem "$src\src\memos" -Directory | ForEach-Object {
    $dest = Join-Path $global_site "memos\$($_.Name)"
    if (-not (Test-Path $dest)) {
        Copy-Item $_.FullName $dest -Recurse -Force
        Write-Host "  已同步: $($_.Name)"
    }
}
Write-Host "  全局环境: OK"

Write-Host ""
Write-Host "=== 验证 ==="
& $dev_py -c "import memos; print('dev:', memos.__version__)"
& $global_py -c "import memos; print('global:', memos.__version__)"
Write-Host ""
Write-Host "升级完成！"
