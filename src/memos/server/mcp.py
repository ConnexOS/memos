import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ..config import config
from ..engine.extractor import MemoryExtractor
from ..engine.memory import ContextMemory
from ..errors import ChromaDBError

logger = logging.getLogger(__name__)

# MCP 文件日志：写入 data/logs/mcp_server.log
_log_dir = Path.cwd() / "data" / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_fh = logging.FileHandler(_log_dir / "mcp_server.log", encoding="utf-8")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logger.addHandler(_fh)
# 同时也让 memos 子包的其他 logger 写入文件
logging.getLogger("memos").addHandler(_fh)

mcp = FastMCP("长时记忆系统")

_id_len = config.server.id_length
_trunc = config.server.response_truncate_length
_top_k_max = config.server.mcp_top_k_max
_trigger_rounds = config.buffer.trigger_rounds

_default_project_id = hashlib.md5(str(Path.cwd()).encode()).hexdigest()[:_id_len]
_default_project_name = Path.cwd().name
_project_ctx = threading.local()


def _get_project_id() -> str:
    return getattr(_project_ctx, "project_id", _default_project_id)


def _detect_project_id() -> str:
    return _default_project_id


def _resolve_pid(override: str = None) -> str:
    return override or _get_project_id()


_memory_instance = None
_extractor_instance = None
_init_lock = threading.Lock()


def _ensure_initialized():
    global _memory_instance, _extractor_instance
    if _memory_instance is not None:
        return
    with _init_lock:
        if _memory_instance is not None:
            return
        try:
            # 支持环境变量覆盖 collection 名（主要用于测试隔离）
            test_collection = os.environ.get("MEMOS_TEST_COLLECTION")
            _memory_instance = ContextMemory(collection_name=test_collection)
            _extractor_instance = MemoryExtractor(
                memory_system=_memory_instance,
                project_id=_default_project_id,
                project_name=_default_project_name,
            )
        except Exception as e:
            logger.error("MCP 初始化失败: %s", e)
            raise ChromaDBError(f"MCP 服务初始化失败: {e}", detail=str(e)) from e


def _get_memory() -> ContextMemory:
    _ensure_initialized()
    return _memory_instance


def _reset_for_test(collection_name: str = None):
    """测试用：重置内部单例，下次调用使用指定 collection（受 MEMOS_TEST_COLLECTION 环境变量影响）"""
    global _memory_instance, _extractor_instance
    if collection_name:
        os.environ["MEMOS_TEST_COLLECTION"] = collection_name
    with _init_lock:
        _memory_instance = None
        _extractor_instance = None


def _get_extractor() -> MemoryExtractor:
    _ensure_initialized()
    return _extractor_instance


MAX_INPUT_LENGTH = 10000
MAX_METADATA_KEYS = 10
ALLOWED_METADATA_KEYS = {
    "type",
    "project_id",
    "source",
    "round_id",
    "status",
    "todo_status",
    "priority",
    "due_date",
    "sort_order",
    "started_at",
    "completed_at",
    "cancelled_at",
    "status_history",
}


@mcp.tool()
def remember(text: str, metadata: dict = None) -> str:
    """追加到缓冲区，累积后自动提炼为知识。直写请用 save_knowledge"""
    if len(text) > MAX_INPUT_LENGTH:
        return f"文本过长（{len(text)} 字符，上限 {MAX_INPUT_LENGTH}），请精简后重试。"
    if metadata:
        orig_keys = set(metadata.keys())
        metadata = {k: v for k, v in metadata.items() if k in ALLOWED_METADATA_KEYS}
        dropped = orig_keys - set(metadata.keys())
        if dropped:
            logger.debug("remember 过滤非法 metadata key: %s", dropped)  # P3-1: 记录被过滤的 key
    ext = _get_extractor()
    triggered = ext.buffer_remember(text)
    if triggered:
        return "已触发后台自动提炼，提炼完成后存入知识库。"
    buf_size = len(ext.conversation_buffer)
    return f"已追加到记忆缓冲区。缓冲区现有 {buf_size} 条，满 {_trigger_rounds} 条后自动提炼入库。"


@mcp.tool()
def recall(
    query: str,
    top_k: int = 3,
    type_filter: str = None,
    days_limit: int = None,
    project_id_override: str = None,
    hybrid: bool = False,
    bm25_weight: float = 0.7,
) -> str:
    """语义检索相关记忆，支持类型/时间过滤和混合检索(BM25+向量)"""
    if len(query) > MAX_INPUT_LENGTH:
        return "查询文本过长，请精简后重试。"
    pid = _resolve_pid(project_id_override)
    knowledge_types = [
        "fact",
        "decision",
        "preference",
        "todo",
        "bug_fix",
        "feature_design",
        "code_optimize",
        "tech_knowledge",
    ]
    if type_filter:
        where = {"type": type_filter} if type_filter in knowledge_types else {"type": {"$in": knowledge_types}}
    else:
        where = {"type": {"$in": knowledge_types}}
    try:
        results = _get_memory().recall(
            query,
            min(top_k, _top_k_max),
            where=where,
            days_limit=days_limit,
            project_id=pid,
            hybrid=hybrid,
            bm25_weight=bm25_weight,
        )
    except Exception as e:
        logger.warning("recall 查询失败（B3 降级）: %s", e)
        return "检索异常，请稍后重试。"
    if not results:
        return "未找到相关记忆。"
    return "\n---\n".join(results)


@mcp.tool()
def list_memories(type_filter: str = None, limit: int = 20, offset: int = 0, project_id_override: str = None) -> str:
    """列出当前项目的所有记忆（分页），不含待办，待办请用 list_todos"""
    pid = _resolve_pid(project_id_override)
    # 默认排除 Pipeline C 对话原文和 todo（todo 由 list_todos 独立管理）
    if type_filter is None:
        type_filter = [
            "fact",
            "decision",
            "preference",
            "bug_fix",
            "feature_design",
            "code_optimize",
            "tech_knowledge",
        ]
    items = _get_memory().list_memories(project_id=pid, type_filter=type_filter, limit=limit, offset=offset)
    if not items:
        return "暂无记忆。"
    lines = []
    for item in items:
        t = item["metadata"].get("type", "unknown")
        lines.append(f"[{t}] {item['document'][:_trunc]}  (id: {item['id'][:_id_len]}...)")
    return "\n".join(lines)


@mcp.tool()
def set_project_id(pid: str) -> str:
    """设置当前会话的项目 ID，用于记忆隔离。仅允许字母数字+连字符+下划线，最长 64 字符。"""
    if not pid or not pid.strip():
        return "参数错误: project_id 不能为空"
    pid = pid.strip()
    if len(pid) > 64:
        return f"参数错误: project_id 过长（{len(pid)} 字符，上限 64）"
    if not re.match(r"^[a-zA-Z0-9_\-]+$", pid):
        return "参数错误: project_id 仅允许字母、数字、下划线和连字符"
    _project_ctx.project_id = pid
    _get_extractor().project_id = pid
    return f"项目 ID 已设置为: {pid}"


@mcp.tool()
def log_complete_turn(user_message: str, assistant_message: str) -> str:
    """记录一轮完整对话（用户消息+助手回复），累积多轮后自动提炼"""
    if len(user_message) > MAX_INPUT_LENGTH or len(assistant_message) > MAX_INPUT_LENGTH:
        return f"消息文本过长（上限 {MAX_INPUT_LENGTH} 字符），请精简后重试。"
    _get_extractor().append_conversation("user", user_message)
    _get_extractor().append_conversation("assistant", assistant_message)
    buf_size = len(_get_extractor().conversation_buffer)
    return f"已记录本轮对话。缓冲区现有 {buf_size} 轮，满 {_trigger_rounds} 轮后自动提炼。"


_MANUAL_SUGGESTION_ALLOWED_KEYS = {
    "trigger_keywords",
    "trigger_mode",
    "priority",
    "cooldown_minutes",
    "expires_at",
}


@mcp.tool()
def save_knowledge(text: str, type: str = "fact", metadata: dict = None) -> str:
    """直接保存知识到知识库（路径 B），由用户明确指令触发。

    路径 B 支持类型：fact/decision/preference/todo/manual_suggestion
    manual_suggestion 类型需要 metadata 中包含 trigger_keywords（list[str]）。
    manual_suggestion 可选 metadata：cooldown_minutes（推送间隔，默认 60），validity_minutes（有效期，0=永不过期）。
    """
    if len(text) > MAX_INPUT_LENGTH:
        return f"文本过长（{len(text)} 字符，上限 {MAX_INPUT_LENGTH}），请精简后重试。"
    if type not in {"fact", "decision", "preference", "todo", "manual_suggestion"}:
        return f"无效类型 '{type}'，支持：fact、decision、preference、todo、manual_suggestion"

    if type == "manual_suggestion":
        return _save_manual_suggestion(text, metadata or {})

    meta = {
        "type": type,
        "project_id": _get_project_id(),
        "project_name": _default_project_name,
        "source": "user_instructed",
        "quality_score": 1.0,
        "quality_reason": "用户直写",
    }
    if type == "todo":
        meta["sort_order"] = time.time()

    mem = _get_memory()
    pid = _get_project_id()

    # v0.4.1: 写入前去重，用户直写评分1.0优先覆盖
    try:
        similar = mem.recall_with_scores(text, project_id=pid, where={"type": type})
    except Exception as e:
        logger.warning("save_knowledge 去重查询失败(%s)，降级为直接写入", e)
        similar = []
    overwritten = None
    if similar and similar[0]["distance"] < config.memory.similarity_threshold:
        dup = similar[0]
        old_score = dup.get("metadata", {}).get("quality_score", 0.5)
        if 1.0 > old_score:
            try:
                # v0.4.4 P1-1: 改用 update_memory 覆盖内容，保留 reuse_count/feedback_count 等累计元数据
                merged_meta = {**meta, "quality_score": 1.0, "quality_reason": "用户直写（覆盖旧知识）"}
                mem.update_memory(dup["id"], new_content=text, new_metadata=merged_meta)
                overwritten = dup["id"]
                logger.info(
                    "save_knowledge 覆盖旧知识: id=%s old_score=%.2f dist=%.3f",
                    overwritten[:8],
                    old_score,
                    dup["distance"],
                )
            except Exception as e:
                logger.warning(
                    "save_knowledge update_memory 失败(id=%s error=%s)，回退到 delete+remember",
                    dup["id"][:8],
                    e,
                )
                try:
                    mem.delete_memory(dup["id"])
                    overwritten = dup["id"]
                except Exception as e2:
                    logger.warning("save_knowledge 删除旧知识也失败: %s", e2)
        else:
            return f"已存在相同知识（相似度 {1 - dup['distance']:.0%}），且质量评分不低于当前，已跳过保存。"

    if overwritten:
        mid = overwritten
        # v0.4.4 P1-1: 冲突检测在已有记忆上运行
        if config.memory.conflict_detection_enabled:
            ext = _get_extractor()
            ext._detect_conflicts_async(text, overwritten)
        return f"已覆盖旧知识 (id: {overwritten[:_id_len]}...)"
    else:
        mid = mem.remember(text, metadata=meta)
        if mid:
            if config.memory.conflict_detection_enabled:
                ext = _get_extractor()
                ext._detect_conflicts_async(text, mid)
            return f"已直接保存知识到知识库 (id: {mid[:_id_len]}...)"
        return "保存失败"


def _save_manual_suggestion(text: str, metadata: dict) -> str:
    """保存手工建议。"""
    trigger_keywords = metadata.get("trigger_keywords", [])
    if not isinstance(trigger_keywords, list) or not trigger_keywords:
        return "manual_suggestion 类型需要 metadata.trigger_keywords 为非空 list[str]"
    if not all(isinstance(kw, str) and kw.strip() for kw in trigger_keywords):
        return "trigger_keywords 中每个关键词必须为非空字符串"
    if len(trigger_keywords) > 10:
        return "trigger_keywords 最多 10 个关键词"
    if any(len(kw) > 50 for kw in trigger_keywords):
        return "每个关键词长度不超过 50 字符"

    trigger_mode = metadata.get("trigger_mode", "keyword")
    if trigger_mode not in ("keyword", "always"):
        return "trigger_mode 必须为 'keyword' 或 'always'"
    # AI 创建的不能设 trigger_mode=always（由 MCP 调用方决定，这里做防守）
    if trigger_mode == "always":
        return "trigger_mode=always 仅限 Dashboard 创建"

    priority = metadata.get("priority", "medium")
    if priority not in ("high", "medium", "low"):
        priority = "medium"

    cooldown_minutes = int(metadata.get("cooldown_minutes", 60))
    validity_minutes = int(metadata.get("validity_minutes", 0))
    expires_at = int(metadata.get("expires_at", 0))
    if validity_minutes > 0:
        expires_at = time.time() + validity_minutes * 60

    meta = {
        "type": "manual_suggestion",
        "project_id": _get_project_id(),
        "project_name": _default_project_name,
        "source": "user_instructed",
        "trigger_keywords": json.dumps(trigger_keywords),
        "trigger_mode": trigger_mode,
        "priority": priority,
        "cooldown_minutes": cooldown_minutes,
        "validity_minutes": validity_minutes,
        "expires_at": expires_at,
        "disabled": False,
        "hit_count": 0,
        "last_triggered": 0,
        "created_by": "ai",
        "timestamp": time.time(),
    }

    mem = _get_memory()
    mid = mem.remember(text, metadata=meta)
    if mid:
        return f"已保存手工建议 (id: {mid[:_id_len]}...)，下次命中关键词时将触发推送"
    return "保存失败"


@mcp.tool()
def update_memory(memory_id: str, text: str = None, metadata: dict = None) -> str:
    """更新记忆内容和/或元数据。仅可更新当前项目下的记忆。metadata 采用合并更新策略。"""
    # P3-3: 空 memory_id 前端校验
    if not memory_id or not memory_id.strip():
        return "参数错误: memory_id 不能为空"
    # P2-6: 文本长度校验，与其他 MCP 工具保持一致
    if text and len(text) > MAX_INPUT_LENGTH:
        return f"文本过长（{len(text)} 字符，上限 {MAX_INPUT_LENGTH}），请精简后重试。"
    if metadata:
        orig_keys = set(metadata.keys())
        metadata = {k: v for k, v in metadata.items() if k in ALLOWED_METADATA_KEYS}
        dropped = orig_keys - set(metadata.keys())
        if dropped:
            logger.debug("update_memory 过滤非法 metadata key: %s", dropped)  # P3-1: 记录被过滤的 key
    mem = _get_memory()
    existing = mem.get_memory(memory_id)
    if existing is None:
        return f"记忆不存在: {memory_id}"
    if existing.get("metadata", {}).get("project_id") != _get_project_id():
        return "跨项目操作被拒绝"
    if not text and not metadata:
        return "请至少提供 text 或 metadata 中的一个参数。"
    try:
        mem.update_memory(memory_id, new_content=text, new_metadata=metadata)
        return f"已更新记忆 (id: {memory_id[:_id_len]}...)"
    except Exception as e:
        return f"更新失败: {e}"


@mcp.tool()
def delete_memory(memory_id: str) -> str:
    """硬删除指定记忆。仅可删除当前项目下的记忆。"""
    # P3-3: 空 memory_id 前端校验
    if not memory_id or not memory_id.strip():
        return "参数错误: memory_id 不能为空"
    mem = _get_memory()
    existing = mem.get_memory(memory_id)
    if existing is None:
        return f"记忆不存在: {memory_id}"
    if existing.get("metadata", {}).get("project_id") != _get_project_id():
        return "跨项目操作被拒绝"
    try:
        mem.delete_memory(memory_id)
        return f"已删除记忆 (id: {memory_id[:_id_len]}...)"
    except Exception as e:
        return f"删除失败: {e}"


@mcp.tool()
def force_extract() -> str:
    """强制立即提炼缓冲区中的所有内容，返回提炼出的记忆条数。P0-3: 注册为 MCP 工具供 AI 助手调用。"""
    ext = _get_extractor()
    count = ext.force_extract()
    return f"强制提炼完成，共提取 {count} 条记忆。"


# ==== v0.4.5 R2: 待办 MCP 工具 ====

_TODO_STATUS_VALUES = {"pending", "in_progress", "completed", "cancelled"}

_VALID_TODO_TRANSITIONS = {
    "pending": {"in_progress", "completed", "cancelled"},
    "in_progress": {"completed", "cancelled"},
    "completed": {"pending"},
    "cancelled": {"pending"},
}


@mcp.tool()
def list_todos(todo_status: str = "pending", limit: int = 10, project_id_override: str = None) -> str:
    """查询待办列表（独立实现），按 todo_status 过滤，返回 JSON 格式。"""
    if todo_status and todo_status not in _TODO_STATUS_VALUES:
        return f"无效 todo_status: {todo_status}，可选: {', '.join(sorted(_TODO_STATUS_VALUES))}"

    pid = _resolve_pid(project_id_override)
    mem = _get_memory()

    results = mem.list_todos(
        project_id=pid,
        todo_status=todo_status,
        limit=min(limit, 200),
    )

    todos = []
    for item in results:
        meta = item.get("metadata", {})
        ts = meta.get("todo_status", "pending")
        if not ts or ts not in _TODO_STATUS_VALUES:
            ts = "pending"
        todos.append(
            {
                "id": item["id"],
                "content": item.get("document", ""),
                "todo_status": ts,
                "priority": meta.get("priority", "medium"),
                "context": meta.get("context", ""),
                "source_date": meta.get("source_date", ""),
                "created_at": meta.get("timestamp", 0),
                "started_at": meta.get("started_at", None),
                "completed_at": meta.get("completed_at", None),
            }
        )

    if not todos:
        return "暂无待办事项。"

    return json.dumps({"todos": todos, "total": len(todos)}, ensure_ascii=False)


@mcp.tool()
def update_todo(memory_id: str, todo_status: str) -> str:
    """更新待办状态（pending/in_progress/completed/cancelled），自动记录 status_history + 时间戳。"""
    if not memory_id or not memory_id.strip():
        return "参数错误: memory_id 不能为空"
    if todo_status not in _TODO_STATUS_VALUES:
        return f"无效 todo_status: {todo_status}，可选: {', '.join(sorted(_TODO_STATUS_VALUES))}"

    mem = _get_memory()
    existing = mem.get_memory(memory_id)
    if existing is None:
        return f"待办不存在: {memory_id}"
    if existing.get("metadata", {}).get("type") != "todo":
        return f"记忆 {memory_id[:8]} 不是待办类型"

    meta = existing.get("metadata", {})
    current = meta.get("todo_status", "pending")
    if not current or current not in _TODO_STATUS_VALUES:
        current = "pending"

    # 校验转换合法性
    allowed = _VALID_TODO_TRANSITIONS.get(current, set())
    if todo_status not in allowed:
        return (
            f"待办状态无法从 {current} 转换到 {todo_status}（允许: {', '.join(sorted(allowed)) if allowed else '无'}）"
        )

    now = time.time()
    raw_history = meta.get("status_history", "[]")
    try:
        status_history = json.loads(raw_history) if isinstance(raw_history, str) else list(raw_history)
    except (json.JSONDecodeError, TypeError):
        status_history = []
    status_history.append(
        {
            "from_status": current,
            "to_status": todo_status,
            "changed_at": now,
        }
    )

    new_meta = {
        "todo_status": todo_status,
        "status_history": json.dumps(status_history),
    }
    if todo_status == "in_progress":
        new_meta["started_at"] = now
    elif todo_status == "completed":
        new_meta["completed_at"] = now
    elif todo_status == "cancelled":
        new_meta["cancelled_at"] = now

    try:
        mem.update_memory(memory_id, new_metadata=new_meta)
    except Exception as e:
        return f"更新失败: {e}"

    logger.info("MCP 待办状态变更: %s: %s → %s", memory_id[:8], current, todo_status)
    return f"待办状态已从 {current} 变更为 {todo_status}"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mcp.run()
