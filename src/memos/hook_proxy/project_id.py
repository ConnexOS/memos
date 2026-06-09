# src/memos/hook_proxy/project_id.py

import json
from pathlib import Path

_project_id_cache: dict[str, str] = {}
_project_name_cache: dict[str, str] = {}
_last_source: str | None = None


def resolve_project_id(cwd: str) -> str:
    """读取 .memos-project JSON 文件，返回 project_id。文件不存在时抛 FileNotFoundError。"""
    cwd = str(Path(cwd).resolve())
    if cwd not in _project_id_cache:
        pid, name, source = _do_resolve(cwd)
        _project_id_cache[cwd] = pid
        _project_name_cache[cwd] = name
        global _last_source
        _last_source = source
    return _project_id_cache[cwd]


def resolve_project_name(cwd: str) -> str:
    """读取 .memos-project JSON 文件，返回 project_name。文件不存在时抛 FileNotFoundError。"""
    cwd = str(Path(cwd).resolve())
    if cwd not in _project_name_cache:
        pid, name, source = _do_resolve(cwd)
        _project_id_cache[cwd] = pid
        _project_name_cache[cwd] = name
    return _project_name_cache[cwd]


def _do_resolve(cwd: str) -> tuple[str, str, str]:
    """读取 .memos-project JSON 文件，返回 (project_id, project_name, source)。"""
    proj_file = Path(cwd) / ".memos-project"
    if not proj_file.exists():
        raise FileNotFoundError(
            f"未找到 .memos-project 文件（{proj_file}），请运行 memos setup --server <URL> --token <TOKEN> --project <项目目录> 初始化"
        )
    try:
        data = json.loads(proj_file.read_text(encoding="utf-8"))
        return data["id"], data["name"], "file"
    except (json.JSONDecodeError, KeyError) as e:
        raise ValueError(f'.memos-project 格式错误（{proj_file}），期望 {{"id": "...", "name": "..."}}: {e}') from e


def get_project_id_source(cwd: str | None = None) -> str:
    """返回 project_id 来源描述。cwd 为 None 时返回最近一次解析的来源。"""
    if cwd:
        cwd = str(Path(cwd).resolve())
        # 触发解析以填充缓存
        resolve_project_id(cwd)
        return _last_source or "unknown"
    return _last_source or "unknown"


def clear_project_id_cache(cwd: str | None = None):
    """清空缓存（测试用）。cwd 为 None 时清空全部。"""
    global _project_id_cache, _project_name_cache, _last_source
    if cwd:
        cwd = str(Path(cwd).resolve())
        _project_id_cache.pop(cwd, None)
        _project_name_cache.pop(cwd, None)
    else:
        _project_id_cache.clear()
        _project_name_cache.clear()
        _last_source = None
