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


def _ensure_utf8_stdout():
    """将 sys.stdout 切换为 UTF-8 编码，根治 Windows GBK 编码问题。

    在进程入口调用一次，后续所有 print()/sys.stdout.write() 均以 UTF-8 输出，
    Claude Code 捕获 stdout 时能正确解析 UTF-8 内容。
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


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
        logger.debug("从 config.json 读取 server URL 失败", exc_info=True)
    try:
        from ..config import config

        port = config.server.port
        url = f"http://127.0.0.1:{port}"
        logger.debug("server URL from MemoConfig port: %s", url)
        return url
    except Exception:
        logger.debug("从 MemoConfig 读取 server URL 失败", exc_info=True)
    logger.info("server URL 使用默认值: http://127.0.0.1:8000")
    return "http://127.0.0.1:8000"


def run_hook_proxy(server_url: str, timeout: int = 30):
    """瞬发 Hook 代理：stdin → HTTP → stdout"""
    from .auth import load_credentials
    from .project_id import resolve_project_id, resolve_project_name

    _ensure_utf8_stdout()
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

    # 预编码请求体为 UTF-8 字节，避免 Windows 上 requests 库用 GBK 编码 JSON 体
    try:
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except Exception as e:
        logger.warning("请求体编码失败: %s", e)
        return

    # 分离 HTTP 请求与 stdout 写入：POST 成功后即使 stdout 失败也不重试
    http_succeeded = False
    additional_context = ""
    for attempt in range(2):
        if http_succeeded:
            break
        try:
            resp = requests.post(
                f"{server_url}{endpoint}",
                data=body_bytes,
                headers={**headers, "Content-Type": "application/json"},
                timeout=timeout,
            )
            logger.debug("Hook 响应 HTTP %d", resp.status_code)
            result = resp.json()
            additional_context = result.get("additional_context", "")
            http_succeeded = True  # POST 成功，标记后不再重试
            break
        except Exception as e:
            logger.warning("Hook 请求失败 (attempt %d/2): %s", attempt + 1, e)
            if attempt == 0:
                time.sleep(1)
                continue

    # stdout 写入（sys.stdout 已切换为 UTF-8，无编码问题）
    if additional_context:
        sys.stdout.write(additional_context)
    sys.stdout.flush()

    logger.info("Hook 代理完成")
