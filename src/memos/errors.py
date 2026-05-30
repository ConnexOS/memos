"""统一错误码体系 — MemoError 基类 + 6 子类 + 错误码表"""


class MemoError(Exception):
    """MEMOS 统一异常基类。所有面向用户的错误均应使用子类抛出。"""

    code: str = "MEM_000"
    _default_message: str = "未知错误"
    _default_suggestion: str = "请联系开发者或查看日志"

    def __init__(self, message: str | None = None, suggestion: str | None = None, detail: str | None = None, **ctx):
        self.message = (message or self._default_message).format(**ctx) if ctx else (message or self._default_message)
        self.suggestion = suggestion or self._default_suggestion
        self.detail = detail
        self.context = ctx
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "suggestion": self.suggestion,
            "detail": self.detail,
        }


# --- 6 错误子类 ---


class ModelNotFoundError(MemoError):
    """嵌入模型未下载或未找到"""

    code = "MEM_001"
    _default_message = "嵌入模型尚未就绪"
    _default_suggestion = "运行 memos init 自动下载模型，或手动将模型放到 ./model/ 目录"


class LLMUnreachableError(MemoError):
    """LLM 端点不可达、超时或认证失败"""

    code = "MEM_002"
    _default_message = "LLM 端点连接失败"
    _default_suggestion = "1.检查 LLM 服务是否启动 2.运行 memos doctor 诊断 3.Dashboard 系统设置中点击'测试连接'验证"


class ChromaDBError(MemoError):
    """ChromaDB 异常（锁冲突、数据损坏、连接失败）"""

    code = "MEM_003"
    _default_message = "向量数据库异常"
    _default_suggestion = "1.检查是否有其他进程占用 ./memdb/ 2.运行 memos doctor 诊断 3.如数据损坏，从备份恢复"


class ConfigCorruptedError(MemoError):
    """配置文件损坏、解析失败或字段无效"""

    code = "MEM_004"
    _default_message = "配置文件异常"
    _default_suggestion = (
        "1.已尝试从 .bak 备份恢复 2.运行 memos config validate 查看详情 3.或运行 memos init --force 重新初始化"
    )


class DiskFullError(MemoError):
    """磁盘空间不足"""

    code = "MEM_005"
    _default_message = "磁盘空间不足"
    _default_suggestion = "1.清理磁盘空间 2.将 ./memdb/ 迁移到大容量磁盘 3.检查 ChromaDB 日志"


class PermissionDeniedError(MemoError):
    """文件或目录权限不足"""

    code = "MEM_006"
    _default_message = "文件权限不足"
    _default_suggestion = "1.检查文件/目录读写权限 2.Linux/macOS 运行 chmod 3.Windows 以管理员身份运行"


class InvalidStateTransitionError(MemoError):
    """待办状态流转非法"""

    code = "MEM_007"
    _default_message = "待办状态流转非法"
    _default_suggestion = "检查当前状态和目标状态是否允许该过渡"


# --- 错误码 → HTTP 状态码映射 ---

_ERROR_HTTP_MAP = {
    "MEM_001": 503,  # Model not found → Service Unavailable
    "MEM_002": 502,  # LLM unreachable → Bad Gateway
    "MEM_003": 500,  # ChromaDB error → Internal Server Error
    "MEM_004": 400,  # Config corrupted → Bad Request
    "MEM_005": 507,  # Disk full → Insufficient Storage
    "MEM_006": 403,  # Permission denied → Forbidden
    "MEM_007": 422,  # Invalid state transition → Unprocessable Entity
}


def http_status_for(exc: MemoError) -> int:
    """返回 MemoError 对应的 HTTP 状态码"""
    return _ERROR_HTTP_MAP.get(exc.code, 500)


# --- CLI 格式化 ---


def format_error(exc: MemoError) -> str:
    """格式化为 CLI 可读字符串"""
    import re

    lines = [f"[{exc.code}] {exc.message}"]
    if exc.suggestion:
        # v0.4.4 P3-2: 限定行首/换行后的编号，避免误拆版本号（如 3.12）
        parts = re.split(r"(?:^|\n)\d+\.\s*", exc.suggestion)
        for s in parts:
            s = s.strip()
            if s:
                lines.append(f"  → {s}")
    if exc.detail:
        lines.append(f"  详情: {exc.detail}")
    return "\n".join(lines)


# --- 从标准异常构造 ---


def from_exception(
    exc: Exception, error_cls: type[MemoError] = MemoError, suggestion: str | None = None, **ctx
) -> MemoError:
    """从标准异常构造 MemoError，保留原始异常信息为 detail"""
    return error_cls(
        message=str(exc),
        suggestion=suggestion,
        detail=f"{type(exc).__name__}: {exc}",
        **ctx,
    )
