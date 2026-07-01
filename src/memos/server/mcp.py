import contextvars
import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ..config import config
from ..engine.extractor import MemoryExtractor
from ..engine.memory import ContextMemory
from ..errors import ChromaDBError
from ..web.auth import _resolve_creator_id

logger = logging.getLogger(__name__)

mcp = FastMCP("长时记忆系统")

# project_id 合法格式（与 set_project_id MCP 工具一致，供 sse_wrapper 复用）
_PID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")

_id_len = config.server.id_length
_trunc = config.server.response_truncate_length
_top_k_max = config.server.mcp_top_k_max

_default_project_id = hashlib.md5(str(Path.cwd()).encode()).hexdigest()[:_id_len]
_default_project_name = Path.cwd().name
_project_id_ctx: contextvars.ContextVar = contextvars.ContextVar("project_id", default="")
_auth_token_ctx: contextvars.ContextVar = contextvars.ContextVar("auth_token", default="")


class SessionAuthStore:
    """线程安全 session_id → token 映射，支持 TTL 过期。"""

    def __init__(self, ttl_seconds: int = 1800):
        self._store: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    def put(self, session_id: str, token: str) -> None:
        with self._lock:
            self._store[session_id] = (token, time.monotonic())

    def get(self, session_id: str) -> str | None:
        with self._lock:
            entry = self._store.get(session_id)
            if entry is None:
                return None
            token, ts = entry
            if time.monotonic() - ts > self._ttl:
                del self._store[session_id]
                return None
            # 刷新时间戳（活跃 session 续期）
            self._store[session_id] = (token, time.monotonic())
            return token

    def cleanup(self) -> int:
        """清理所有过期 session，返回清理数量。"""
        now = time.monotonic()
        removed = 0
        with self._lock:
            expired = [sid for sid, (_, ts) in self._store.items() if now - ts > self._ttl]
            for sid in expired:
                del self._store[sid]
                removed += 1
        return removed


# 全局单例
_session_auth_store = SessionAuthStore(ttl_seconds=1800)

# MCP 文件日志：统一写到 MEMOS 安装目录 data/logs/mcp_server_{project_id}.log
_MEMOS_ROOT = Path(__file__).resolve().parents[3]
_log_dir = _MEMOS_ROOT / "data" / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_fh = logging.FileHandler(_log_dir / f"mcp_server_{_default_project_id}.log", encoding="utf-8")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logger.addHandler(_fh)
# 同时也让 memos 子包的其他 logger 写入文件
logging.getLogger("memos").addHandler(_fh)


def _get_project_id() -> str:
    return _project_id_ctx.get() or _default_project_id


def _set_session_project_id(pid: str, project_name: str = None) -> None:
    """设置当前会话的 project_id 到 context var。

    由 ProjectAwareSSEWrapper（URL 路径提取）和 set_project_id（MCP 工具）调用。
    context var 在 Starlette 异步 task 级隔离，不同请求互不干扰。

    注意：不再直接修改 _extractor_instance.project_id（避免并发竞态）。
    改为在 _get_extractor() 中从 context var 同步，确保每次获取 extractor
    时 project_id 与当前请求一致。
    """
    _project_id_ctx.set(pid)
    if project_name:
        _register_project_name(pid, project_name)
        _project_name_ctx.set(project_name)


def _detect_project_id() -> str:
    return _default_project_id


def _resolve_pid(override: str = None) -> str:
    return override or _get_project_id()


# project_id → 项目名映射（由 ProjectAwareSSEWrapper 从 SSE URL ?name= 注册）
# 供 save_knowledge / remember 写入正确的 project_name metadata
_MAX_REGISTRY_SIZE = 1000
_project_name_registry: dict[str, str] = {}
_project_name_registry_lock = threading.Lock()


def _register_project_name(pid: str, name: str) -> None:
    with _project_name_registry_lock:
        if len(_project_name_registry) >= _MAX_REGISTRY_SIZE and pid not in _project_name_registry:
            logger.warning("_project_name_registry 已达上限 %d，跳过注册 %s", _MAX_REGISTRY_SIZE, pid)
            return
        _project_name_registry[pid] = name


_project_name_ctx: contextvars.ContextVar = contextvars.ContextVar("project_name", default="")


def _get_project_name(pid: str) -> str:
    # 优先：per-request 上下文（由 SSE wrapper 或 set_project_id 设置）
    name = _project_name_ctx.get()
    if name:
        return name
    # 次优：全局注册表（由 SSE wrapper 从 SSE URL ?name= 注册）
    name = _project_name_registry.get(pid)
    if name:
        return name
    # 默认项目匹配
    if pid == _default_project_id:
        return _default_project_name
    return ""


_memory_instance = None
_extractor_instance = None
_init_lock = threading.Lock()


def _ensure_initialized():
    global _memory_instance, _extractor_instance
    if _memory_instance is not None and _extractor_instance is not None:
        return
    with _init_lock:
        if _memory_instance is not None and _extractor_instance is not None:
            return
        try:
            # 支持环境变量覆盖 collection 名（主要用于测试隔离）
            test_collection = os.environ.get("MEMOS_TEST_COLLECTION")
            if _memory_instance is None:
                _memory_instance = ContextMemory(collection_name=test_collection)
            if _extractor_instance is None:
                # 统一模式下 _memory_instance 由 set_memory() 注入，此处只创建 extractor
                _extractor_instance = MemoryExtractor(
                    memory_system=_memory_instance,
                    project_id=_default_project_id,
                    project_name=_default_project_name,
                )
        except Exception as e:
            logger.error("MCP 初始化失败: %s", e)
            raise ChromaDBError(f"MCP 服务初始化失败: {e}", detail=str(e)) from e


def _get_memory() -> ContextMemory:
    """获取 ContextMemory 实例"""
    global _memory_instance
    if _memory_instance is not None:
        return _memory_instance
    _ensure_initialized()
    # 健康检查：验证 ChromaDB store 仍可用
    if _memory_instance is not None:
        try:
            _memory_instance.store.count()
        except Exception:
            logger.warning("ChromaDB store 连接异常，尝试重建")
            _memory_instance = None
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
    """获取 MemoryExtractor 实例，同步当前请求的 project_id。

    每次调用时从 context var 同步 project_id，避免并发 SSE 会话间的竞态。
    后台提炼线程可能在同步后读取，但提炼由当前请求触发，时序上安全。
    """
    _ensure_initialized()
    if _extractor_instance is not None:
        pid = _get_project_id()
        if pid and pid != _extractor_instance.project_id:
            _extractor_instance.project_id = pid
            # 同步 project_name：优先使用注册名，否则回退到 pid
            pname = _project_name_registry.get(pid)
            if pname:
                _extractor_instance.project_name = pname
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
    "creator_id",
    "scope",
}


@mcp.tool()
def remember(text: str, metadata: dict = None) -> str:
    """标记待关注，写入 ChromaDB type=watchlist，供 L5 待关注面板展示。

    可通过 L5 Dashboard 将待关注内容转为知识/忽略/备注。
    """
    if len(text) > MAX_INPUT_LENGTH:
        return f"文本过长（{len(text)} 字符，上限 {MAX_INPUT_LENGTH}），请精简后重试。"

    watchlist_meta = {
        "type": "watchlist",
        "user_intent": "待关注",
        "created_at": time.time(),
        "processed": False,
        "project_id": _get_project_id(),
        "project_name": _get_project_name(_get_project_id()),
        "source": "remember",
    }
    if metadata:
        for k, v in metadata.items():
            if k not in ("type", "created_at", "processed", "user_intent"):
                watchlist_meta[k] = v

    mem = _get_memory()
    mid = mem.remember(text, metadata=watchlist_meta)
    if mid:
        # F9: SSE 事件总线通知
        try:
            from ..features.event_bus import touch_event as _touch

            _touch("watchlist")
        except Exception:
            logger.debug("SSE 事件总线通知失败（非致命）", exc_info=True)
        # v0.7.2: watchlist_update 通知
        try:
            from ..features.notifications import get_notification_logger

            notifier = get_notification_logger()
            notifier.notify(
                type="watchlist_update",
                title=f"新增待关注: {text[:40]}...",
                message="",
                metadata={"watchlist_id": mid, "action": "view"},
            )
        except Exception:
            logger.debug("watchlist_update 通知失败（非致命）", exc_info=True)
        return json.dumps({"id": mid, "status": "watchlist"}, ensure_ascii=False)
    return "保存失败"


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
    """语义检索相关记忆，支持类型/时间过滤和混合检索（BM25+向量）。

    支持 6 种类型过滤：task/briefing/solution/decision/lesson/process
    watchlist 不参与语义检索。"""
    if len(query) > MAX_INPUT_LENGTH:
        return "查询文本过长，请精简后重试。"
    pid = _resolve_pid(project_id_override)
    if type_filter and type_filter not in _VALID_RECALL_TYPES:
        return f"无效 type_filter '{type_filter}'，有效值：task/briefing/solution/decision/lesson/process"
    if type_filter:
        where = {"type": type_filter}
    else:
        where = {"type": {"$in": list(_VALID_RECALL_TYPES)}}
    try:
        _creator_id = _resolve_creator_id(from_ctx=True)
        _scope = _creator_id not in ("", "unknown")
        results = _get_memory().recall(
            query,
            min(top_k, _top_k_max),
            where=where,
            days_limit=days_limit,
            project_id=pid,
            hybrid=hybrid,
            bm25_weight=bm25_weight,
            creator_id=_creator_id if _scope else None,
            ignore_scope=not _scope,
        )
    except Exception as e:
        logger.warning("recall 查询失败（B3 降级）: %s", e)
        return "检索异常，请稍后重试。"
    if not results:
        return "未找到相关记忆。"
    return "\n---\n".join(results)


@mcp.tool()
def list_memories(
    type_filter: str = None,
    limit: int = 20,
    offset: int = 0,
    project_id_override: str = None,
    exclude_types: list[str] = None,
) -> str:
    """列出当前项目的所有记忆（分页），默认排除 todo 类型（待办请用 list_todos）。v0.7.0 新 6 类体系：task/briefing/solution/decision/lesson/process，向后兼容旧 7 类查询。

    参数：
      type_filter: 按类型过滤（可选，不指定则列出所有非排除类型）
      exclude_types: 要排除的类型列表（默认 ["todo"]，可通过 [] 恢复查询全量）
    """
    pid = _resolve_pid(project_id_override)
    if exclude_types is None:
        exclude_types = ["todo"]
    if type_filter == "todo" and exclude_types == ["todo"]:
        logger.warning("list_memories type_filter=todo 与默认 exclude_types=['todo'] 冲突，已自动清除排除列表")
        exclude_types = []
    if type_filter is None:
        type_filter = list(_VALID_RECALL_TYPES)
    _creator_id = _resolve_creator_id(from_ctx=True)
    _scope = _creator_id not in ("", "unknown")
    items = _get_memory().list_memories(
        project_id=pid,
        type_filter=type_filter,
        limit=limit,
        offset=offset,
        exclude_types=exclude_types,
        creator_id=_creator_id if _scope else None,
        ignore_scope=not _scope,
    )
    if not items:
        return "暂无记忆。"
    lines = []
    for item in items:
        t = item["metadata"].get("type", "unknown")
        lines.append(f"[{t}] {item['document'][:_trunc]}  (id: {item['id']})")
    return "\n".join(lines)


@mcp.tool()
def set_project_id(pid: str, project_name: str = None) -> str:
    """设置当前会话的项目 ID，用于记忆隔离。仅允许字母数字+连字符+下划线，最长 64 字符。

    Args:
        pid: 项目 ID
        project_name: 可选的项目可读名称，用于 Dashboard 显示
    """
    if not pid or not pid.strip():
        return "参数错误: project_id 不能为空"
    pid = pid.strip()
    if len(pid) > 64:
        return f"参数错误: project_id 过长（{len(pid)} 字符，上限 64）"
    if not _PID_PATTERN.match(pid):
        return "参数错误: project_id 仅允许字母、数字、下划线、连字符，1-64 字符"
    _set_session_project_id(pid, project_name)
    return f"项目 ID 已设置为: {pid}"


# v0.6.0 知识类型常量 —— 仅 L3 四类（Claude Code 可写范围）
_VALID_KNOWLEDGE_TYPES = {
    "solution",
    "decision",
    "lesson",
    "process",
}

# recall 可查询类型范围（新 6 类）
_VALID_RECALL_TYPES = {
    "task",
    "briefing",
    "solution",
    "decision",
    "lesson",
    "process",
}

_MANUAL_SUGGESTION_ALLOWED_KEYS = {
    "trigger_keywords",
    "trigger_mode",
    "priority",
    "cooldown_minutes",
    "expires_at",
}


# ==== v0.7.2: MCP 写入去重策略优化 ====


def _call_llm(prompt_text: str, timeout_sec: int = 30) -> str | None:
    """直接调用 LLM（使用 urllib 而非项目 LLM 抽象，避免后台线程引入过多导入依赖和锁争用）。
    向 app.py 的 LLM 调用对齐（去除 max_tokens、统一 model 回退、用 config.request_timeout）。
    返回 response JSON 字符串，超时/失败返回 None。"""
    ep = config.llm.active_endpoint
    if not ep or not ep.api_base:
        logger.warning("LLM 端点未配置，跳过去重判断")
        return None

    # 与 app.py 对齐：model 回退用 "default"，不传 max_tokens，timeout 用 config 值
    payload = json.dumps(
        {
            "model": ep.model or "default",
            "messages": [{"role": "user", "content": prompt_text}],
            "temperature": 0.1,
        }
    ).encode()

    headers = {"Content-Type": "application/json"}
    if ep.api_key:
        headers["Authorization"] = f"Bearer {ep.api_key}"

    req = urllib.request.Request(
        ep.api_base.rstrip("/") + "/chat/completions",
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body = resp.read().decode()
            data = json.loads(body)
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                logger.warning("LLM 返回空内容，响应前 200 字符: %s", body[:200])
            return content
    except (urllib.error.URLError, ConnectionError, json.JSONDecodeError, KeyError) as e:
        logger.warning("LLM 调用失败: %s", e)
        return None


def _dedup_llm_judge(text: str, new_mem_type: str, old_text: str, old_id: str, original_meta: dict) -> None:
    """后台线程：LLM 判断新旧知识是否同一内容，按类型策略处理。

    直接调用，不返回值，结果通过通知中心反馈。
    """
    # 与 app.py 对齐：使用配置的 request_timeout（当前 600s）
    timeout = config.llm.request_timeout

    # 构造类型专用 prompt
    prompts = {
        "solution": (
            f"你是一个知识去重引擎。知识类型：solution（问题+解决方案）\n"
            f"旧记忆: {old_text[:500]}\n"
            f"新记忆: {text[:500]}\n"
            f"判断：是否同一错误场景的同一解决方案？若不是，是否互补（不同方案）？\n"
            f'输出 JSON: {{"is_same": bool, "is_superseding": bool, "reasoning": "..."}}'
        ),
        "decision": (
            f"你是一个知识去重引擎。知识类型：decision（技术选型/架构决策）\n"
            f"旧记忆: {old_text[:500]}\n"
            f"新记忆: {text[:500]}\n"
            f"判断：是否同一决策主题的更新？决策具有迭代性——新决策应覆盖旧决策。\n"
            f'输出 JSON: {{"is_same": bool, "is_superseding": true, "reasoning": "..."}}'
        ),
        "lesson": (
            f"你是一个知识去重引擎。知识类型：lesson（经验教训）\n"
            f"旧记忆: {old_text[:500]}\n"
            f"新记忆: {text[:500]}\n"
            f"判断：是否同一认知角度？教训具有互补性——不同角度应共存。\n"
            f'输出 JSON: {{"is_same": bool, "is_superseding": false, "reasoning": "..."}}'
        ),
    }

    prompt = prompts.get(new_mem_type)
    if not prompt:
        return

    try:
        result = _call_llm(prompt, timeout_sec=timeout)
        if not result:
            raise ValueError("LLM 返回空")
        verdict = json.loads(result)
    except json.JSONDecodeError:
        logger.warning("LLM 去重 JSON 解析失败 (%s), 原始返回: %s", new_mem_type, (result or "")[:300])
        raise
    except Exception as e:
        logger.warning("LLM 去重判断失败 (%s): %s", new_mem_type, e)
        # 错误处理：decision 降级为直接覆盖，其余跳过
        if new_mem_type == "decision":
            mem = _get_memory()
            # 先写入新知识，再用新 ID 覆盖旧知识（防止数据丢失）
            new_meta = {**original_meta, "quality_score": 1.0, "quality_reason": "LLM 降级覆盖（去重判断失败）"}
            new_id = mem.remember(text, metadata=new_meta)
            if new_id:
                mem.supersede_memory(old_id, new_id=new_id)
                logger.info(
                    "save_knowledge 去重判断失败(dedup_failed) -> decision 降级覆盖 old=%s new=%s",
                    old_id[:8],
                    new_id[:8],
                )
        else:
            logger.info("save_knowledge 去重判断失败(dedup_failed) -> %s 跳过写入（LLM 返回空）", new_mem_type)
        # 发送 dedup_failed 通知
        try:
            from ..features.notifications import get_notification_logger

            notifier = get_notification_logger()
            notifier.notify(
                type="dedup_failed",
                title=f"去重判断失败: {text[:40]}...",
                message=f"类型={new_mem_type}，已降级处理",
                metadata={"action": "retry", "memory_type": new_mem_type},
            )
        except Exception:
            pass
        return

    # 按 verdict 处理
    mem = _get_memory()
    if verdict.get("is_superseding"):
        meta = {
            **original_meta,
            "quality_score": original_meta.get("quality_score", 1.0),
            "quality_reason": "LLM 判覆盖",
        }
        new_id = mem.remember(text, metadata=meta)  # 先写入新知识，获取真实 ID
        if new_id:
            mem.supersede_memory(old_id, new_id=new_id)  # 再用真实 ID 覆盖旧知识
            logger.info(
                "save_knowledge 去重完成 -> 覆盖 old=%s new=%s reasoning=%s",
                old_id[:8],
                new_id[:8],
                verdict.get("reasoning", "")[:80],
            )
    elif not verdict.get("is_same"):
        # 不同内容，追加写入
        meta = {
            **original_meta,
            "quality_score": original_meta.get("quality_score", 1.0),
            "quality_reason": "LLM 判不同",
        }
        mid = mem.remember(text, metadata=meta)
        if mid:
            logger.info(
                "save_knowledge 去重完成 -> 追加写入 id=%s reasoning=%s", mid[:8], verdict.get("reasoning", "")[:80]
            )
    else:
        # 同一 + 不覆盖 → skip
        logger.info(
            "save_knowledge 去重完成 -> 跳过（与旧记忆 %s 重复） reasoning=%s",
            old_id[:8],
            verdict.get("reasoning", "")[:80],
        )


@mcp.tool()
def save_knowledge(text: str, type: str = None, metadata: dict = None) -> str:
    """直接保存知识到知识库（路径 B），由用户明确指令触发。

    支持 4 种类型：solution/decision/lesson/process。
    待办请用 create_todo 工具。
    """
    if len(text) > MAX_INPUT_LENGTH:
        return f"文本过长（{len(text)} 字符，上限 {MAX_INPUT_LENGTH}），请精简后重试。"

    # type 可选：不传则由 Claude 自行决定，默认 fallback 到 "solution"
    effective_type = type or "solution"

    # manual_suggestion 不走知识类型校验（触发 _save_manual_suggestion）
    if effective_type == "manual_suggestion":
        return _save_manual_suggestion(text, metadata or {})

    if effective_type not in _VALID_KNOWLEDGE_TYPES:
        return (
            f"无效类型 '{effective_type}'，v0.6.0 支持 4 种类型："
            f"{', '.join(sorted(_VALID_KNOWLEDGE_TYPES))}。"
            f"task/briefing 属于系统类型，由 MEMOS LLM 写入。"
        )

    meta = {
        "type": effective_type,
        "project_id": _get_project_id(),
        "project_name": _get_project_name(_get_project_id()),
        "source": "auto_save",
        "scope": "team",
        "creator_id": _resolve_creator_id(from_ctx=True),
        "quality_score": 0.8,
        "quality_reason": "Claude 主动保存",
    }
    # 合并调用方传入的 metadata（可覆盖 quality_score / source / quality_reason 等）
    if metadata:
        meta.update(metadata)

    mem = _get_memory()
    pid = _get_project_id()

    # v0.4.1: 写入前去重，默认评分 0.8，调用方可通过 metadata 覆盖
    dedup_failed = False
    try:
        similar = mem.recall_with_scores(text, project_id=pid, where={"type": effective_type})
    except ChromaDBError as e:
        logger.error("save_knowledge 去重查询因数据库异常失败，降级为直接写入: %s", e)
        dedup_failed = True
        similar = []
    except Exception as e:
        logger.warning("save_knowledge 去重查询失败(%s)，降级为直接写入", e)
        similar = []
    if similar and similar[0]["distance"] < config.memory.similarity_threshold:
        dup = similar[0]

        if effective_type == "process":
            # process：不调 LLM，直接覆盖
            merged_meta = {**meta, "quality_score": 1.0, "quality_reason": "process 覆盖（按类型策略）"}
            mem.update_memory(dup["id"], new_content=text, new_metadata=merged_meta)
            # F7 活动日志埋点（非阻塞）
            try:
                from ..features.activity_log import log_knowledge_write as _log_kw

                _log_kw(
                    type_=effective_type,
                    summary=text[:100],
                    source="save_knowledge",
                    extra={"action": "overwrite"},
                    project_id=_get_project_id(),
                )
            except Exception:
                logger.debug("save_knowledge 活动日志埋点(覆盖)失败", exc_info=True)
            # F9: SSE 事件总线通知
            try:
                from ..features.event_bus import touch_event as _touch

                _touch("memory_stream")
            except Exception:
                logger.debug("SSE 事件总线通知失败（非致命）", exc_info=True)
            return "已覆盖旧知识（process 直接覆盖）"
        else:
            # solution/decision/lesson：异步 LLM 判断
            old_text = dup.get("document", "")
            threading.Thread(
                target=_dedup_llm_judge,
                args=(text, effective_type, old_text, dup["id"], meta),
                daemon=True,
            ).start()
            return "已收到，去重判断中"

    else:
        mid = mem.remember(text, metadata=meta)
        if mid:
            if config.memory.conflict_detection_enabled:
                ext = _get_extractor()
                ext._detect_conflicts_async(text, mid)
            # F7 活动日志埋点（非阻塞）
            try:
                from ..features.activity_log import log_knowledge_write as _log_kw

                _log_kw(
                    type_=effective_type,
                    summary=text[:100],
                    source="save_knowledge",
                    extra={"action": "create"},
                    project_id=_get_project_id(),
                )
            except Exception:
                logger.debug("save_knowledge 活动日志埋点(创建)失败", exc_info=True)
            # F9: SSE 事件总线通知
            try:
                from ..features.event_bus import touch_event as _touch

                _touch("memory_stream")
            except Exception:
                logger.debug("SSE 事件总线通知失败（非致命）", exc_info=True)
            # v0.7.2: quality_alert 通知
            _notify_if_low_quality(text, mid, effective_type, meta)
            msg = f"已直接保存知识到知识库 (id: {mid[:_id_len]}...)"
            if dedup_failed:
                msg += "（数据库异常，去重检查未完成，建议运行 memos doctor 诊断）"
            return msg
        return "保存失败"


def _save_manual_suggestion(text: str, metadata: dict) -> str:
    """保存用户建议。"""
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
        "project_name": _get_project_name(_get_project_id()),
        "source": "user_instructed",
        "scope": "personal",
        "creator_id": _resolve_creator_id(from_ctx=True),
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
        return f"已保存用户建议 (id: {mid[:_id_len]}...)，下次命中关键词时将触发推送"
    return "保存失败"


# v0.7.2: quality_alert 通知
def _notify_if_low_quality(text: str, memory_id: str, mem_type: str, meta: dict):
    """quality_score < 阈值时发送 quality_alert 通知（含 60min 限频）。"""
    qs = meta.get("quality_score", 1.0)
    threshold = getattr(config.memory, "quality_alert_threshold", 0.5)
    if qs >= threshold:
        return

    try:
        from ..features.notifications import get_notification_logger

        notifier = get_notification_logger()

        # 60min 限频：检查最近 60 分钟内是否已有同一 memory_id 的 quality_alert
        rate_limit = 60  # 分钟
        now = time.time()
        existing = notifier._read_all()
        for rec in reversed(existing):
            if rec.get("type") == "quality_alert":
                meta_data = rec.get("metadata", {}) or {}
                if meta_data.get("memory_id") == memory_id:
                    age_minutes = (now - rec.get("timestamp", 0)) / 60
                    if age_minutes < rate_limit:
                        logger.debug(
                            "quality_alert 限频跳过: memory_id=%s, age=%.1fmin",
                            memory_id[:8],
                            age_minutes,
                        )
                        return
                    break

        notifier.notify(
            type="quality_alert",
            title=f"低质量知识: {text[:40]}...",
            message=f"quality_score={qs}，建议审查",
            metadata={
                "memory_id": memory_id,
                "quality_score": qs,
                "action": "review",
            },
        )
    except Exception as e:
        logger.debug("quality_alert 通知失败（非致命）: %s", e)


@mcp.tool()
def update_memory(memory_id: str, text: str = None, metadata: dict = None) -> str:
    """更新记忆内容和/或元数据。仅可更新当前项目下的记忆。metadata 采用合并更新策略。

    支持通过 metadata 中的 status 字段变更记忆状态流转：active ↔ forgotten/archived。
    注意：状态变更建议使用专用 MCP 工具或 API 端点（forget/restore/archive），
    直接修改 status 字段不会自动记录 inactive_reason 或 forgotten_at 时间戳。
    """
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
    # P3-17: 防御性清理尾部 ...（用户从 list_memories 拷贝截断 ID）
    memory_id = memory_id.rstrip(".")
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
        return f"已更新记忆 (id: {memory_id})"
    except Exception as e:
        return f"更新失败: {e}"


@mcp.tool()
def delete_memory(memory_id: str) -> str:
    """硬删除指定记忆。仅可删除当前项目下的记忆。"""
    # P3-3: 空 memory_id 前端校验
    if not memory_id or not memory_id.strip():
        return "参数错误: memory_id 不能为空"
    # P3-17: 防御性清理尾部 ...（用户从 list_memories 拷贝截断 ID）
    memory_id = memory_id.rstrip(".")
    mem = _get_memory()
    existing = mem.get_memory(memory_id)
    if existing is None:
        return f"记忆不存在: {memory_id}"
    if existing.get("metadata", {}).get("project_id") != _get_project_id():
        return "跨项目操作被拒绝"
    try:
        mem.delete_memory(memory_id)
        return f"已删除记忆 (id: {memory_id})"
    except Exception as e:
        return f"删除失败: {e}"


# ==== v0.4.5 R2 / v0.4.8: 待办 MCP 工具 ====

_TODO_STATUS_VALUES = {"pending", "in_progress", "completed", "cancelled"}

_VALID_TODO_TRANSITIONS = {
    "pending": {"in_progress", "completed", "cancelled"},
    "in_progress": {"completed", "cancelled"},
    "completed": {"pending"},
    "cancelled": {"pending"},
}

_PRIORITY_VALUES = {"high", "medium", "low"}


@mcp.tool()
def create_todo(content: str, priority: str = "medium", due_date: str = "") -> str:
    """创建待办，写入完整 metadata（含 todo_status/pending/priority/status），返回 JSON 格式。

    参数：
      content (必填): 待办内容
      priority (可选): 优先级 high/medium/low，默认 medium
      due_date (可选): 到期日 ISO 8601 格式 "YYYY-MM-DD"
    """
    content = (content or "").strip()
    if not content:
        return "参数错误: content 不能为空"
    if priority not in _PRIORITY_VALUES:
        return f"无效 priority: {priority}，可选: {', '.join(sorted(_PRIORITY_VALUES))}"

    mem = _get_memory()
    now = time.time()
    metadata = {
        "type": "todo",
        "todo_status": "pending",
        "priority": priority,
        "status": "active",
        "project_id": _get_project_id(),
        "project_name": _get_project_name(_get_project_id()),
        "source": "mcp",
        "status_history": json.dumps([]),
        "sort_order": now,
        "timestamp": now,
    }
    if due_date:
        metadata["due_date"] = due_date

    metadata.setdefault("scope", "personal")
    metadata["creator_id"] = _resolve_creator_id(from_ctx=True)

    mid = mem.remember(content, metadata=metadata)
    if mid is None:
        return "待办创建失败"
    logger.info("MCP 待办已创建 id=%s", mid[:8])
    return json.dumps({"id": mid, "message": "待办已创建"}, ensure_ascii=False)


@mcp.tool()
def list_todos(todo_status: str = "pending", limit: int = 10, project_id_override: str = "") -> str:
    """查询待办列表（独立实现），按 todo_status 过滤，返回 JSON 格式。"""
    if todo_status and todo_status not in _TODO_STATUS_VALUES:
        return f"无效 todo_status: {todo_status}，可选: {', '.join(sorted(_TODO_STATUS_VALUES))}"

    pid = _resolve_pid(project_id_override)
    mem = _get_memory()

    _creator_id = _resolve_creator_id(from_ctx=True)
    _scope = _creator_id not in ("", "unknown")
    results = mem.list_todos(
        project_id=pid,
        todo_status=todo_status,
        limit=min(limit, 200),
        creator_id=_creator_id if _scope else None,
        ignore_scope=not _scope,
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
                "due_date": meta.get("due_date", ""),
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
    if existing.get("metadata", {}).get("project_id") != _get_project_id():
        return f"待办不存在: {memory_id}"

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


def set_memory(memory):
    """由 server/app.py lifespan 注入 ContextMemory 单例（unified 模式）"""
    global _memory_instance
    _memory_instance = memory
