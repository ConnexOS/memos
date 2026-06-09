# src/memos/hook_proxy/__init__.py

"""Hook 代理子包（v0.5.0 SSE 模式）

运行模式:
  - --hook: Hook 瞬发模式（stdin → HTTP）
"""


def main(args):
    """CLI 入口"""
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    from .proxy import _resolve_server_url, run_hook_proxy

    server_url = _resolve_server_url(getattr(args, "server", None))
    timeout = getattr(args, "timeout", 60)
    run_hook_proxy(server_url, timeout)
