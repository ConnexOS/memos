"""向后兼容 stub —— v0.4.3 Phase 10：dashboard 模块已移至 memos.web。"""

from memos.engine.memory import ContextMemory  # noqa: F401  # 测试 mock 引用
from memos.web.app import app, main  # noqa: F401
