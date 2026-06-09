"""memos setup — 一键初始化命令"""

import hashlib
import json
import logging
import os as _os
from pathlib import Path
from urllib.parse import quote

from ..hook_proxy.auth import save_credentials
from ..hook_proxy.project_id import clear_project_id_cache

logger = logging.getLogger(__name__)


def _normalize_server(server: str) -> str:
    """归一化 server 地址：无 :// 前缀时补 http://"""
    server = server.rstrip("/")
    if "://" not in server:
        server = f"http://{server}"
    return server


def cmd_setup(args):
    """一键初始化：创建 .memos-project + 保存凭据 + 生成 .mcp.json + 安装 Hook

    所有配置写入 --project 指定的目录（默认当前目录），全部覆盖。
    """
    # Step 1: 解析参数
    project_dir_str = args.project if hasattr(args, "project") and args.project else None
    target_dir = Path(project_dir_str).resolve() if project_dir_str else Path.cwd()
    target_dir.mkdir(parents=True, exist_ok=True)
    project_name = args.name if hasattr(args, "name") and args.name else target_dir.name
    project_id = hashlib.md5(project_name.encode()).hexdigest()[:8]
    server_url = _normalize_server(args.server)
    token = args.token

    # 切换到目标目录，后续所有写入基于 CWD
    _orig_cwd = _os.getcwd()
    try:
        _os.chdir(str(target_dir))

        # Step 2: 写入 .memos-project（覆盖）
        proj_file = Path(".memos-project")
        proj_file.write_text(
            json.dumps({"id": project_id, "name": project_name}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        clear_project_id_cache(str(target_dir))
        print(f"[OK] .memos-project 已创建: id={project_id} name={project_name}")

        # Step 3: 保存凭据（覆盖）
        save_credentials(server_url, token)
        print(f"[OK] 凭据已保存: {server_url}")

        # Step 4: 生成 .mcp.json（覆盖）
        mcp_config = {
            "mcpServers": {
                "memos": {
                    "type": "sse",
                    "url": f"{server_url}/mcp/{project_id}/sse?name={quote(project_name)}&token={token}",
                }
            }
        }
        mcp_json_path = Path(".mcp.json")
        mcp_json_path.write_text(json.dumps(mcp_config, indent=2) + "\n", encoding="utf-8")
        print("[OK] .mcp.json 已生成")

        # Step 5: 安装 Hook（覆盖 memos 相关项，保留 settings.json 其他配置）
        from .dispatch import install_hooks

        install_hooks(global_mode=False)
        print("[OK] Hook 已安装到项目 settings.json")
    finally:
        _os.chdir(_orig_cwd)

    print()
    print("提示: 重新加载 Claude Code 后生效")
