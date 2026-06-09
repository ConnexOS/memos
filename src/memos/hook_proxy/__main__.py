# src/memos/hook_proxy/__main__.py

"""python -m memos.hook_proxy 入口

--server URL: 指定 HTTP server 地址（默认 http://localhost:8000）
--timeout N: 请求超时秒数（默认 60）
"""

import sys

from . import main


class _Args:
    """简易参数对象"""

    def __init__(self):
        self.hook = True
        self.server = None
        self.timeout = 60

        for i, arg in enumerate(sys.argv):
            if arg == "--server" and i + 1 < len(sys.argv):
                self.server = sys.argv[i + 1]
            elif arg == "--timeout" and i + 1 < len(sys.argv):
                try:
                    self.timeout = int(sys.argv[i + 1])
                except ValueError:
                    pass


if __name__ == "__main__":
    main(_Args())
