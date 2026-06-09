# src/memos/hook_proxy/proxy.py

"""Hook 代理：stdin → HTTP POST → stdout"""

import json
import logging
import os
import sys
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


def _setup_file_logging():
    """添加文件日志处理器，写入 etc/hook_proxy.log 用于诊断"""
    try:
        etc_dir = Path(__file__).resolve().parent.parent.parent.parent / "etc"
        etc_dir.mkdir(exist_ok=True)
        log_file = etc_dir / "hook_proxy.log"
        existing = [
            h
            for h in logger.root.handlers
            if isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "") == str(log_file)
        ]
        if existing:
            return
        handler = logging.FileHandler(log_file, encoding="utf-8", mode="a")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.root.addHandler(handler)
        logger.info("文件日志已开启: %s", log_file)
    except Exception as e:
        logger.warning("文件日志初始化失败: %s", e)


def _resolve_server_url(args_server: str | None) -> str:
    """解析 server URL，优先级：CLI参数 > 环境变量 > 配置 > 默认值"""
    if args_server:
        logger.debug("server URL from CLI: %s", args_server)
        return args_server
    env_server = os.environ.get("MEMOS_SERVER")
    if env_server:
        logger.debug("server URL from env MEMOS_SERVER: %s", env_server)
        return env_server
    try:
        _etc_dir = Path(__file__).resolve().parent.parent.parent.parent / "etc"
        _config_file = _etc_dir / "config.json"
        if _config_file.exists():
            with open(_config_file, encoding="utf-8") as _f:
                _cfg = json.load(_f)
            _port = _cfg.get("server", {}).get("port", 8000)
            _proxy_url = f"http://127.0.0.1:{_port}"
            logger.debug("server URL from etc/config.json port: %s", _proxy_url)
            return _proxy_url
    except Exception:
        pass
    try:
        from ..config import config

        port = config.server.port
        url = f"http://127.0.0.1:{port}"
        logger.debug("server URL from MemoConfig port: %s", url)
        return url
    except Exception:
        pass
    logger.info("server URL 使用默认值: http://127.0.0.1:8000")
    return "http://127.0.0.1:8000"


def run_hook_proxy(server_url: str, timeout: int = 30):
    """瞬发 Hook 代理：stdin → HTTP → stdout"""
    from .auth import load_credentials
    from .project_id import resolve_project_id, resolve_project_name

    _setup_file_logging()
    logger.info("Hook 代理启动: server_url=%s", server_url)

    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Hook 输入非 JSON，跳过")
        return

    project_id = resolve_project_id(os.getcwd())
    project_name = resolve_project_name(os.getcwd())
    headers = {"X-Memos-Project-Id": project_id, "X-Memos-Project-Name": project_name}

    creds = load_credentials()
    if creds and creds.get("token"):
        headers["X-Auth-Token"] = creds["token"]

    if "last_assistant_message" in payload:
        endpoint = "/api/hooks/stop"
    else:
        endpoint = "/api/hooks/prompt"
    logger.info(
        "Hook %s → %s%s",
        "stop" if "last_assistant_message" in payload else "prompt",
        server_url,
        endpoint,
    )

    for attempt in range(2):
        try:
            resp = requests.post(
                f"{server_url}{endpoint}",
                json=payload,
                headers=headers,
                timeout=timeout,
            )
            logger.debug("Hook 响应 HTTP %d", resp.status_code)
            result = resp.json()
            additional_context = result.get("additional_context", "")
            if additional_context:
                sys.stdout.write(additional_context)
            break
        except Exception as e:
            logger.warning("Hook 请求失败 (attempt %d/2): %s", attempt + 1, e)
            if attempt == 0:
                time.sleep(1)
                continue
        finally:
            sys.stdout.flush()

    logger.info("Hook 代理完成")
