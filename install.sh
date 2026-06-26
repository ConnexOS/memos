#!/usr/bin/env bash
# MEMOS 一键安装脚本 (Linux / macOS)
#
# Windows 开发环境编写，未经 Linux/macOS 真实验证，欢迎反馈
#
# 用法:
#   chmod +x install.sh && ./install.sh
#   ./install.sh --mirror https://hf-mirror.com

set -euo pipefail

VENV_PATH="${VENV_PATH:-./venv}"
MODEL_NAME="${MODEL_NAME:-bge-large-zh-v1.5}"
MIRROR="${MIRROR:-}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mirror) MIRROR="$2"; shift 2 ;;
        --venv) VENV_PATH="$2"; shift 2 ;;
        --help) echo "用法: $0 [--mirror URL] [--venv PATH]"; exit 0 ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
done

echo "=== MEMOS 安装脚本 ==="

# 检查 Python
if ! command -v python3 &>/dev/null; then
    echo "[!!] 未找到 Python，请安装 Python 3.12+"
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if [ "$(echo "$PY_VER >= 3.12" | bc)" != "1" ]; then
    echo "[!!] 需要 Python 3.12+，当前: $PY_VER"
    exit 1
fi

# 创建虚拟环境
if [ ! -d "$VENV_PATH" ]; then
    echo "[..] 创建虚拟环境..."
    python3 -m venv "$VENV_PATH"
    echo "[OK] 虚拟环境已创建"
else
    echo "[OK] 虚拟环境已存在"
fi

PIP="$VENV_PATH/bin/pip"

# 升级 pip
"$PIP" install --upgrade pip

# 安装 MEMOS
if [ -n "$MIRROR" ]; then
    export HF_ENDPOINT="$MIRROR"
    echo "[..] 使用镜像: $MIRROR"
fi

"$PIP" install memos

# 初始化
echo "[..] 运行 memos init..."
"$VENV_PATH/bin/memos" init --force

echo ""
echo "=== 安装完成 ==="
echo ""
echo "启动:"
echo "  $VENV_PATH/bin/memos dashboard"
echo ""
echo "首次使用:"
echo "  浏览器访问 http://127.0.0.1:8000"
