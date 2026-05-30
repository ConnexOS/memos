"""
嵌入模型的自动下载与管理。

策略：
1. 检查目标路径是否存在有效模型
2. 不存在则从 HuggingFace 下载
3. 支持离线模式（--model-path）跳过下载
4. 支持环境变量 MEMOS_MODEL_PATH 覆盖存储路径
5. 下载使用 huggingface_hub.snapshot_download（自带进度条 + 断点续传）
"""

import hashlib
import os
import shutil
import sys
import time
from pathlib import Path

from ..errors import DiskFullError

# 已知模型的 SHA256（可选校验），key 为文件名
_KNOWN_SHA256 = {
    # all-MiniLM-L6-v2 关键文件
    "pytorch_model.bin": None,  # 体积大且多版本，默认不校验
    # bge-large-zh-v1.5 关键文件
    # "pytorch_model.bin": None,
}

# 各模型的必需文件列表
_MODEL_REQUIRED_FILES = [
    "config.json",
    "tokenizer_config.json",
    "modules.json",
]


def get_model_path(model_name: str = None) -> Path:
    """获取模型目录路径。优先环境变量，其次按模型名构造子目录，默认 bge-large-zh-v1.5。"""
    from ..config import get_memos_home

    env_path = os.environ.get("MEMOS_MODEL_PATH")
    if env_path:
        return Path(env_path)

    home = get_memos_home()
    if model_name:
        # 按模型名构造子目录，避免 MiniLM 和 bge-large 混入同一目录
        safe_name = model_name.replace("/", "_").replace("\\", "_")
        return home / "model" / safe_name
    return home / "model" / "bge-large-zh-v1.5"


def model_exists(model_dir: Path) -> bool:
    """检查模型目录是否包含有效模型文件。"""
    # 模型文件可能是 .safetensors 或 .bin 格式，任一存在即可
    has_config = all((model_dir / f).exists() for f in _MODEL_REQUIRED_FILES)
    has_weights = (model_dir / "model.safetensors").exists() or (model_dir / "pytorch_model.bin").exists()
    return has_config and has_weights


def _compute_sha256(filepath: Path) -> str:
    """计算文件 SHA256 哈希。"""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _verify_model_files(target: Path) -> bool:
    """验证已下载模型文件的完整性（检查必需文件存在）。"""
    missing = [f for f in _MODEL_REQUIRED_FILES if not (target / f).exists()]
    if missing:
        print(f"  [WARN] 缺少文件: {', '.join(missing)}")
        return False
    return True


def _verify_model_sha256(target: Path) -> bool:
    """对 _KNOWN_SHA256 中列出的文件执行 SHA256 校验。
    仅校验已知哈希的文件，未登记的文件跳过（HuggingFace 不公开完整哈希列表）。
    """
    all_ok = True
    for filename, expected_hash in _KNOWN_SHA256.items():
        fpath = target / filename
        if not fpath.exists():
            continue
        if expected_hash is None:
            continue
        actual = _compute_sha256(fpath)
        if actual != expected_hash:
            print(f"  [ERROR] SHA256 不匹配: {filename}")
            print(f"    预期: {expected_hash}")
            print(f"    实际: {actual}")
            all_ok = False
    return all_ok


def _get_model_repo_id(model_name: str) -> str:
    """将短名转换为完整的 HuggingFace repo_id。

    all-MiniLM-L6-v2 → sentence-transformers/all-MiniLM-L6-v2
    bge-large-zh-v1.5 → BAAI/bge-large-zh-v1.5
    如果已含 / 则直接返回。
    """
    if "/" in model_name:
        return model_name
    # sentence-transformers 系列
    if "miniLM" in model_name.lower() or "minilm" in model_name.lower():
        return f"sentence-transformers/{model_name}"
    # BAAI/bge 系列
    if "bge" in model_name.lower():
        return f"BAAI/{model_name}"
    # 默认尝试 sentence-transformers
    return f"sentence-transformers/{model_name}"


def download_model(
    model_name: str,
    target: Path | None = None,
    retries: int = 3,
    timeout: int = 600,
    verify_sha256: bool = False,
) -> bool:
    """
    确保嵌入模型可用。返回 True=成功/已就绪, False=失败。

    - target 指定模型本地路径，默认从 ModelConfig.path 读取
    - model_name 为 HuggingFace repo_id 或短名
    - 已就绪则跳过下载
    - 下载使用 huggingface_hub.snapshot_download（自带进度条 + 断点续传）
    """
    if target is None:
        target = get_model_path()

    repo_id = _get_model_repo_id(model_name)

    # 已存在有效模型 → 跳过下载
    if model_exists(target):
        print(f"  [OK] 模型已就绪: {target}")
        return True

    # 检查磁盘空间（可能抛出 DiskFullError）
    _check_disk_space(target, model_name)

    print(f"  [>>] 正在下载模型 {repo_id} 到: {target}")
    target.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, retries + 1):
        try:
            _do_download(repo_id, target, timeout)
            # 验证下载完整性 (文件存在 + 可选SHA256)
            if not _verify_model_files(target):
                if attempt < retries:
                    wait = 2**attempt
                    print(f"  [!!] 模型文件不完整，{wait}s 后重试 ({attempt}/{retries})")
                    time.sleep(wait)
                    continue
                print(f"  [ERROR] 模型文件不完整，重试 {retries} 次仍失败")
                return False
            # SHA256 校验（仅对已知哈希的文件执行）
            if verify_sha256:
                ok = _verify_model_sha256(target)
                if not ok:
                    print("  [WARN] SHA256 校验失败，但文件存在性检查通过（哈希库可能未收录此版本）")
            print(f"  [OK] 模型下载完成: {target}")
            return True

        except KeyboardInterrupt:
            print("\n  [!!] 下载已中断，已下载的部分已保留")
            print("  下次运行将自动从断点继续")
            return False
        except (ImportError, DiskFullError):
            # 不可重试的错误，直接向上传播（安全审计 HIGH-6）
            raise
        except Exception as e:
            msg = str(e).lower()
            if attempt < retries:
                wait = 2**attempt
                if "timeout" in msg or "timed out" in msg:
                    print(f"  [!!] 网络超时，{wait}s 后重试 ({attempt}/{retries})")
                elif "connection" in msg or "resolve" in msg:
                    print(f"  [!!] 网络不可达，{wait}s 后重试 ({attempt}/{retries})")
                else:
                    print(f"  [!!] 下载失败: {e}，{wait}s 后重试 ({attempt}/{retries})")
                time.sleep(wait)
            else:
                _print_download_error(e, repo_id)
                return False

    return False


def _do_download(repo_id: str, target: Path, timeout: int):
    """使用 huggingface_hub.snapshot_download 下载模型，自带进度条和断点续传。"""
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("  [ERROR] 缺少 huggingface_hub 库，请运行: pip install huggingface-hub")
        raise ImportError("缺少 huggingface_hub 库，请运行: pip install huggingface-hub")

    snapshot_download(
        repo_id=repo_id,
        local_dir=str(target),
    )


def _check_disk_space(target: Path, model_name: str):
    """检查磁盘空间是否充足。"""
    # 根据模型预估所需空间
    estimates = {
        "bge-large": 1.5 * 1024 * 1024 * 1024,  # ~1.5GB
        "miniLM": 500 * 1024 * 1024,  # ~500MB
        "minilm": 500 * 1024 * 1024,
    }
    required = 500 * 1024 * 1024  # 默认 500MB
    for key, size in estimates.items():
        if key in model_name.lower():
            required = size
            break

    # 确保父目录存在以检测磁盘
    parent = target
    while not parent.exists():
        parent = parent.parent
    try:
        usage = shutil.disk_usage(parent)
    except Exception:
        return  # 无法检测磁盘空间时不阻塞
    if usage.free < required:
        print(
            f"  [ERROR] 磁盘空间不足：需要至少 {required // (1024 * 1024)}MB，当前可用 {usage.free // (1024 * 1024)}MB"
        )
        print("  建议：①清理磁盘空间 ②将模型目录迁移到大容量磁盘")
        raise DiskFullError(
            f"磁盘空间不足：需要 {required // (1024 * 1024)}MB，当前可用 {usage.free // (1024 * 1024)}MB"
        )


def _print_download_error(e: Exception, repo_id: str):
    """输出中文下载错误指引。"""
    msg = str(e).lower()
    print(f"  [ERROR] 模型下载失败: {e}", file=sys.stderr)

    if "timeout" in msg or "timed out" in msg:
        print("  网络连接超时，请检查：", file=sys.stderr)
        print("  ① 是否可访问 HuggingFace（国内可能需要代理）", file=sys.stderr)
        print("  ② 使用 --model-path 指定本地已有模型路径", file=sys.stderr)
        print(f"  ③ 手动下载: huggingface-cli download {repo_id} --local-dir <目标路径>", file=sys.stderr)
    elif "connection" in msg or "resolve" in msg or "name or service" in msg:
        print("  无法连接 HuggingFace，请检查：", file=sys.stderr)
        print("  ① 网络是否正常", file=sys.stderr)
        print("  ② 如在国内，可能需要设置 HF_ENDPOINT=https://hf-mirror.com", file=sys.stderr)
        print("  ③ 使用 --model-path 指定本地已有模型路径", file=sys.stderr)
    elif "disk" in msg or "space" in msg or "no space" in msg:
        print("  磁盘空间不足，请清理磁盘后重试", file=sys.stderr)
    else:
        print("  参考: document/07部署/系统发布部署手册.md", file=sys.stderr)
        print(f"  或手动下载: huggingface-cli download {repo_id} --local-dir <目标路径>", file=sys.stderr)


def get_download_progress(target: Path) -> str:
    """返回模型下载状态文本。

    返回: "已就绪" / "未下载" / "下载中断 (约 45%)"
    """
    if model_exists(target):
        return "已就绪"

    # 检查是否有部分下载的文件
    if target.exists() and any(target.iterdir()):
        # 有些文件已下载，检查完整度
        required = set(_MODEL_REQUIRED_FILES)
        existing = {f.name for f in target.iterdir() if f.is_file()}
        if existing:
            pct = len(existing & required) / len(required) * 100
            return f"下载中断 (约 {pct:.0f}%)"
        return "未下载"
    return "未下载"
