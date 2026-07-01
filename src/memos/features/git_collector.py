"""Git 数据采集工具 —— 供简报生成时获取昨日代码变更记录。

v0.7.1 F10: 新增。采集已提交日志 (git log) 和未提交变更 (git diff --stat)。
"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _run_git(args: list[str], repo_path: str | None) -> str:
    """执行 git 命令并返回输出。失败时返回空字符串。"""
    cwd = str(Path(repo_path).resolve()) if repo_path else None
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            timeout=10,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "not a git repository" in stderr:
                logger.warning("Git 收集: 当前目录不是 git 仓库")
            else:
                logger.warning("Git 收集: 命令失败 %s → %s", args, stderr)
            return ""
        return result.stdout.strip()
    except FileNotFoundError:
        logger.warning("Git 收集: git 命令不可用")
        return ""
    except subprocess.TimeoutExpired:
        logger.warning("Git 收集: git 命令超时")
        return ""


def get_git_log(date: str, repo_path: str | None = None) -> str:
    """获取指定日期的 Git 提交日志（已提交）。

    Args:
        date: 日期 "YYYY-MM-DD"（采集该日 00:00:00 ~ 23:59:59）

    命令: git log --after="YYYY-MM-DD 00:00:00" --before="YYYY-MM-DD 23:59:59" --oneline --stat
    """
    after = f"{date} 00:00:00"
    before = f"{date} 23:59:59"
    return _run_git(["log", f"--after={after}", f"--before={before}", "--oneline", "--stat"], repo_path)


def get_git_diff(repo_path: str | None = None) -> str:
    """获取当前工作区的未提交变更。

    命令: git diff --stat
    """
    return _run_git(["diff", "--stat"], repo_path)
