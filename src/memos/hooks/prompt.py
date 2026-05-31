"""UserPromptSubmit Hook —— 采集用户输入到 ChromaDB，检索相关记忆注入上下文。

v0.4.4：分层检索重构。Layer 1（上下文注入）+ Layer 2（主动建议写入）。

由 Claude Code settings.json 配置调用：
  python -m memos.hooks.prompt
"""

import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path

# 配置日志
LOG_FILE = Path.home() / ".memos" / "hook_prompt.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("memos.hooks.prompt")
logger.setLevel(logging.DEBUG)
_fh = logging.FileHandler(str(LOG_FILE), encoding="utf-8", mode="a")
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] prompt: %(message)s"))
logger.addHandler(_fh)
# 同时输出到 stderr（Claude Code 会捕获并展示在 hook 日志中）
_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setLevel(logging.WARNING)
_stderr_handler.setFormatter(logging.Formatter("[memos.prompt] %(levelname)s: %(message)s"))
logger.addHandler(_stderr_handler)

PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
STATE_FILE = PROJECT_DIR / ".claude" / "conv_state.json"
NO_SUGGESTIONS_FILE = PROJECT_DIR / ".claude" / "no_suggestions"

_mem = None
_pid_cache = None


def _get_project_id() -> str:
    global _pid_cache
    if _pid_cache is None:
        _pid_cache = hashlib.md5(str(PROJECT_DIR).encode()).hexdigest()[:8]
    return _pid_cache


def _get_memory():
    global _mem
    if _mem is None:
        try:
            from memos.engine.memory import ContextMemory

            _mem = ContextMemory()
            logger.debug("ContextMemory 初始化成功")
        except Exception as e:
            logger.error("初始化 ContextMemory 失败: %s", e)
            import traceback

            logger.error(traceback.format_exc())
    return _mem


def _no_suggestions_file_exists() -> bool:
    """检查免打扰文件是否存在。文件存在即阻断 Layer 2，JSON 解析失败仅记录警告。"""
    if not NO_SUGGESTIONS_FILE.exists():
        return False
    try:
        NO_SUGGESTIONS_FILE.read_text(encoding="utf-8")
    except Exception:
        logger.warning("免打扰文件解析失败，仍阻断建议: %s", NO_SUGGESTIONS_FILE)
    return True


def _check_suggestion_cooldown(mem, source_memory_id: str, pid: str, cooldown_minutes: int) -> bool:
    """检查指定源记忆是否在冷却期内。True=冷却期内，应跳过。"""
    if cooldown_minutes <= 0:
        return False
    cutoff = time.time() - cooldown_minutes * 60
    try:
        results = mem.store.get(
            where={
                "$and": [
                    {"type": "suggestion"},
                    {"project_id": pid},
                    {"suggestion_type": "active_push"},
                    {"source_memory_id": source_memory_id},
                    {"timestamp": {"$gte": cutoff}},
                ]
            },
            limit=1,
            include=[],
        )
        in_cooldown = len(results["ids"]) > 0
        if in_cooldown:
            logger.debug("冷却期命中: source_memory_id=%s", source_memory_id[:12])
        return in_cooldown
    except Exception as e:
        logger.error("冷却期检查失败: %s", e)
        return False  # 降级：不阻断


def _check_daily_limit(mem, pid: str, max_per_day: int) -> bool:
    """检查每日推送是否已达上限。True=已达到上限。"""
    if max_per_day <= 0:
        return True  # 0 表示不允许推送
    cutoff = time.time() - 86400
    try:
        count = mem.store.count(
            where={
                "$and": [
                    {"type": "suggestion"},
                    {"project_id": pid},
                    {"suggestion_type": "active_push"},
                    {"timestamp": {"$gte": cutoff}},
                ]
            }
        )
        at_limit = count >= max_per_day
        if at_limit:
            logger.info("每日上限已达: %d/%d", count, max_per_day)
        return at_limit
    except Exception as e:
        logger.error("每日上限检查失败: %s", e)
        return True  # 降级：阻断


# --- 管道二：系统状态型建议（v0.4.4 增强版 Phase 2）---


def _check_event_cooldown(mem, pid: str, event_type: str) -> bool:
    """检查同类事件是否在冷却期内。True=冷却期内，应跳过。

    查找 type=suggestion + suggestion_type=system_alert + event_type=event_type
    且 timestamp 在 cooldown_hours 内的记录。
    """
    cfg = _get_system_suggestion_config()
    if cfg.cooldown_hours <= 0:
        return False
    cutoff = time.time() - cfg.cooldown_hours * 3600
    try:
        results = mem.store.get(
            where={
                "$and": [
                    {"type": "suggestion"},
                    {"project_id": pid},
                    {"suggestion_type": "system_alert"},
                    {"event_type": event_type},
                    {"timestamp": {"$gte": cutoff}},
                ]
            },
            limit=1,
            include=[],
        )
        in_cooldown = len(results["ids"]) > 0
        if in_cooldown:
            logger.info("%s in cooldown, skipped", event_type)
        return in_cooldown
    except Exception as e:
        logger.warning("冷却检查失败 %s: %s", event_type, e)
        return False  # 降级：不阻断


def _check_pipe2_daily_limit(mem, pid: str) -> bool:
    """检查管道二每日上限。True=已达上限。

    24h 滑动窗口，与管道一独立计数。
    使用 system_suggestion.daily_limit 作为上限。
    """
    cfg = _get_system_suggestion_config()
    if cfg.daily_limit <= 0:
        return True
    cutoff = time.time() - 86400
    try:
        count = mem.store.count(
            where={
                "$and": [
                    {"type": "suggestion"},
                    {"project_id": pid},
                    {"suggestion_type": "system_alert"},
                    {"timestamp": {"$gte": cutoff}},
                ]
            }
        )
        at_limit = count >= cfg.daily_limit
        if at_limit:
            logger.info("daily_limit reached (%d/%d)", count, cfg.daily_limit)
        return at_limit
    except Exception as e:
        logger.error("管道二每日上限检查失败: %s", e)
        return True  # 降级：阻断


def _generate_system_suggestions(mem, pid: str) -> list[dict]:
    """管道二主调度：遍历 6 个事件，检查触发条件、冷却、上限。

    返回 list[dict]，每条含 suggestion_type="system_alert" + event_type。
    按优先级排序，超出 daily_limit 的低优先级丢弃。
    """
    cfg = _get_system_suggestion_config()

    # 全局开关
    if not cfg.enabled:
        logger.debug("管道二已禁用，跳过")
        return []

    # 免打扰文件
    if _no_suggestions_file_exists():
        logger.debug("免打扰文件存在，跳过管道二")
        return []

    # 收集可用的事件检查函数
    checks = [
        ("first_time_user", "high", _check_first_time_user),
        ("unrefined_rounds", "medium", _check_unrefined_rounds),
        ("low_quality_ratio", "medium", _check_low_quality_ratio),
        ("no_daily_review", "low", _check_no_daily_review),
        ("inactive_project", "low", _check_inactive_project),
        ("expired_memories", "low", _check_expired_memories),
    ]

    triggered = []
    priority_map = {"high": 0, "medium": 1, "low": 2}

    for event_type, default_priority, check_fn in checks:
        # 跳过被禁用的触发事件
        if not getattr(cfg.triggers, event_type, True):
            logger.debug("%s trigger disabled, skipped", event_type)
            continue

        # 独立 try/except，单条失败不阻断其他
        try:
            # 冷却检查
            if _check_event_cooldown(mem, pid, event_type):
                continue

            result = check_fn(mem, pid) if event_type != "no_daily_review" else check_fn(pid)
            if result is not None:
                result["suggestion_type"] = "system_alert"
                triggered.append(result)
                logger.info(
                    "pipe2 %s triggered: reason=%s",
                    event_type,
                    result.get("reason", ""),
                )
            else:
                logger.debug("pipe2 %s not triggered", event_type)
        except Exception as e:
            logger.warning("pipe2 %s check failed: %s", event_type, e)

    if not triggered:
        return []

    # 按优先级排序
    triggered.sort(key=lambda x: priority_map.get(x.get("priority", "low"), 2))

    # v0.4.4 P2-2: 复用入口 cfg，不再重复获取
    existing_count = 0
    try:
        cutoff = time.time() - 86400
        existing_count = mem.store.count(
            where={
                "$and": [
                    {"type": "suggestion"},
                    {"project_id": pid},
                    {"suggestion_type": "system_alert"},
                    {"timestamp": {"$gte": cutoff}},
                ]
            }
        )
    except Exception:
        pass

    remaining = cfg.daily_limit - existing_count
    if remaining <= 0:
        logger.info(
            "daily_limit (%d) reached, dropping all %d pipe2 events",
            cfg.daily_limit,
            len(triggered),
        )
        return []

    if len(triggered) > remaining:
        logger.info(
            "daily_limit: keeping %d/%d events (dropped %d low priority)",
            remaining,
            len(triggered),
            len(triggered) - remaining,
        )
        triggered = triggered[:remaining]

    logger.info("pipe2 generated: %d suggestions", len(triggered))
    return triggered


# --- 管道三：用户手工型建议（v0.4.4 增强版 Phase 3）---


def _match_manual_suggestions(current_msg: str, mem, pid: str) -> list[dict]:
    """匹配用户手工设定的建议。

    从 ChromaDB 查询 type=manual_suggestion + project_id=pid，
    按 trigger_mode 匹配当前用户消息。
    命中后更新 hit_count 和 last_triggered。

    返回 list[dict]，每条含 suggestion_type="manual_trigger"。
    独立 try/except，失败返回空列表。
    """
    if not current_msg or not mem:
        return []

    # 免打扰文件阻断
    if _no_suggestions_file_exists():
        logger.debug("免打扰文件存在，跳过管道三")
        return []

    # 先用传入 pid 查询，若为空则尝试 default project_id
    try:
        results = mem.store.get(
            where={
                "$and": [
                    {"type": "manual_suggestion"},
                    {"project_id": pid},
                ]
            },
            include=["documents", "metadatas"],
        )
    except Exception as e:
        logger.warning("管道三查询失败: %s", e)
        return []

    ids = results.get("ids", [])
    documents = results.get("documents", [])
    metadatas = results.get("metadatas", [])
    if not ids:
        # Fallback: 尝试当前 CWD 项目 ID（兼容 Dashboard 创建的记录）
        import hashlib as _hlib

        default_pid = _hlib.md5(str(PROJECT_DIR).encode()).hexdigest()[:8]
        if default_pid != pid:
            try:
                results = mem.store.get(
                    where={
                        "$and": [
                            {"type": "manual_suggestion"},
                            {"project_id": default_pid},
                        ]
                    },
                    include=["documents", "metadatas"],
                )
                ids = results.get("ids", [])
                documents = results.get("documents", [])
                metadatas = results.get("metadatas", [])
                if ids:
                    logger.debug("管道三在 default project_id 下找到 %d 条", len(ids))
            except Exception:
                pass

    if not ids:
        return []

    now = time.time()

    matched = []
    msg_lower = current_msg.lower()

    for doc_id, doc, meta in zip(ids, documents, metadatas):
        try:
            # 过期检查
            expires_at = meta.get("expires_at", 0)
            if expires_at > 0 and now > expires_at:
                logger.debug("manual_suggestion %s 已过期，跳过", doc_id[:8])
                continue

            # 临时失效检查
            if meta.get("disabled", False):
                logger.debug("manual_suggestion %s 已临时失效，跳过", doc_id[:8])
                continue

            trigger_mode = meta.get("trigger_mode", "keyword")
            cooldown = meta.get("cooldown_minutes", 0)

            # 冷却检查（metadata 级）
            last_triggered = meta.get("last_triggered", 0)
            if cooldown > 0 and last_triggered > 0 and (now - last_triggered) < cooldown * 60:
                logger.debug("manual_suggestion %s 冷却中，跳过", doc_id[:8])
                continue

            # 解析 trigger_keywords（兼容 str 和历史数据）
            raw_keywords = meta.get("trigger_keywords", "[]")
            if isinstance(raw_keywords, str):
                try:
                    keywords = json.loads(raw_keywords)
                except (json.JSONDecodeError, TypeError):
                    keywords = [raw_keywords]
            elif isinstance(raw_keywords, list):
                keywords = raw_keywords
            else:
                keywords = []

            if not isinstance(keywords, list):
                keywords = []

            is_match = False
            if trigger_mode == "always":
                is_match = True
            elif trigger_mode == "keyword":
                # 子串包含，不区分大小写，OR 逻辑
                is_match = any(kw.lower() in msg_lower for kw in keywords if isinstance(kw, str) and kw)

            if not is_match:
                continue

            # 更新 hit_count 和 last_triggered（即时写回）
            new_hit_count = meta.get("hit_count", 0) + 1
            new_meta = dict(meta)
            new_meta["hit_count"] = new_hit_count
            new_meta["last_triggered"] = now
            try:
                mem.store.update(ids=[doc_id], metadatas=[new_meta])
            except Exception as e:
                logger.warning("手动建议命中计数更新失败: %s", e)

            # 构建返回结果
            sim = 0.85 if trigger_mode == "always" else 0.95
            matched.append(
                {
                    "suggestion_type": "manual_trigger",
                    "content": (doc or "")[:300],
                    "source_memory_id": doc_id,
                    "similarity": sim,
                    "metadata": {
                        "trigger_keywords": raw_keywords,
                        "trigger_mode": trigger_mode,
                        "hit_count": new_hit_count,
                        "priority": meta.get("priority", "medium"),
                        "created_by": meta.get("created_by", "user"),
                    },
                }
            )

            logger.info(
                "manual_suggestion matched: id=%s, mode=%s, hit=%d",
                doc_id[:8],
                trigger_mode,
                new_hit_count,
            )
        except Exception as e:
            logger.warning("manual_suggestion 处理失败 (%s): %s", doc_id[:8], e)
            continue

    return matched


def _format_context_item(r: dict) -> str:
    """格式化单条 Layer 1 上下文条目。"""
    meta = r.get("metadata", {})
    ts = meta.get("timestamp", 0)
    date_str = time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "unknown"
    doc_type = meta.get("type", "?")
    sim = r.get("similarity", 0)
    doc = r.get("document", "")
    # 截断到 150 字符
    doc_truncated = doc[:150] + "…" if len(doc) > 150 else doc
    return f"[历史参考] {date_str} | [{doc_type}] | 相似度 {sim:.0%}\n{doc_truncated}\n---"


def _build_context(mem, query: str, pid: str) -> str:
    """检索相关记忆并构建上下文注入文本（旧版，仅用于 MEMOS_USE_OLD_CONTEXT=1 回退）。"""
    if not mem or not query:
        return ""

    try:
        knowledge_types = [
            "fact",
            "decision",
            "preference",
            "bug_fix",
            "feature_design",
            "code_optimize",
            "tech_knowledge",
        ]
        results = mem.recall(
            query,
            top_k=5,
            project_id=pid,
            return_scores=True,
            where={"type": {"$in": knowledge_types}},
        )
        if not results:
            return ""

        lines = ["--- 相关记忆 ---"]
        for r in results:
            if isinstance(r, dict):
                doc = r.get("document", "")
                t = r.get("metadata", {}).get("type", "?")
                lines.append(f"  [{t}] {doc[:200]}")
            elif isinstance(r, str):
                lines.append(f"  {r[:200]}")
        logger.debug("[旧版] 已构建 %d 条相关记忆", len(results))
        return "\n".join(lines)
    except Exception as e:
        logger.error("检索记忆失败: %s", e)
        return ""


def _get_memory_config():
    """惰性获取 MemoryConfig，避免模块级导入导致循环依赖。"""
    from memos.config import config as _cfg

    return _cfg.memory


def _get_suggestion_config():
    """惰性获取 SuggestionConfig。"""
    from memos.config import config as _cfg

    return _cfg.suggestion


def _get_system_suggestion_config():
    """惰性获取 SystemSuggestionConfig。"""
    from memos.config import config as _cfg

    return _cfg.system_suggestion


_KNOWLEDGE_TYPES = [
    "fact",
    "decision",
    "preference",
    "todo",
    "bug_fix",
    "feature_design",
    "code_optimize",
    "tech_knowledge",
]

# 知识匹配用类型（排除 todo）：管道一主动匹配时使用，todo 不应参与上下文注入或主动建议
_KNOWLEDGE_TYPES_FOR_MATCHING = [
    "fact",
    "decision",
    "preference",
    "bug_fix",
    "feature_design",
    "code_optimize",
    "tech_knowledge",
]


def _check_first_time_user(mem, pid: str) -> dict | None:
    """检查知识库是否为空（新用户首次对话）。

    Returns: dict 含 reason 和 priority，或 None（不触发）。
    """
    try:
        count = mem.store.count(where={"$and": [{"project_id": pid}, {"type": {"$in": _KNOWLEDGE_TYPES}}]})
        if count == 0:
            return {"event_type": "first_time_user", "priority": "high", "reason": "知识库为空，建议引导用户初始化记忆"}
        logger.debug("first_time_user 跳过: 知识库有 %d 条记录", count)
    except Exception as e:
        logger.warning("first_time_user 检查失败: %s", e)
    return None


def _check_unrefined_rounds(mem, pid: str) -> dict | None:
    """检查是否存在大量对话未被提炼为知识。

    条件：user_input > 20 AND count(knowledge_types) / count(user_input) < 0.3
    """
    try:
        user_count = mem.store.count(where={"$and": [{"project_id": pid}, {"type": "user_input"}]})
        if user_count <= 20:
            logger.debug("unrefined_rounds 跳过: 仅 %d 轮对话", user_count)
            return None
        knowledge_count = mem.store.count(where={"$and": [{"project_id": pid}, {"type": {"$in": _KNOWLEDGE_TYPES}}]})
        ratio = knowledge_count / user_count
        if ratio < 0.3:
            return {
                "event_type": "unrefined_rounds",
                "priority": "medium",
                "reason": f"提炼率 {ratio:.0%} ({knowledge_count}/{user_count}) 低于 30%",
            }
        logger.debug("unrefined_rounds 跳过: 提炼率 {:.0%}".format(ratio))
    except Exception as e:
        logger.warning("unrefined_rounds 检查失败: %s", e)
    return None


def _check_low_quality_ratio(mem, pid: str) -> dict | None:
    """检查低质量记忆占比是否过高。

    全量拉取知识类型 metadatas，Python 侧过滤 quality_score < 0.5。
    分母 ≥ 10 且占比 > 30% → 触发。
    性能预算 < 50ms（suggestion 总量 < 500 条时）。
    """
    try:
        results = mem.store.get(
            where={"$and": [{"project_id": pid}, {"type": {"$in": _KNOWLEDGE_TYPES}}]},
            include=["metadatas"],
        )
        metadatas = results.get("metadatas", [])
        total = len(metadatas)
        if total < 10:
            logger.debug("low_quality_ratio 跳过: 仅 %d 条知识", total)
            return None
        low_quality = sum(1 for m in metadatas if m.get("quality_score", 1) < 0.5)
        ratio = low_quality / total
        if ratio > 0.3:
            return {
                "event_type": "low_quality_ratio",
                "priority": "medium",
                "reason": f"低质量记忆占比 {ratio:.0%} ({low_quality}/{total}) 超过 30%",
            }
        logger.debug("low_quality_ratio 跳过: 低质量占比 {:.0%}".format(ratio))
    except Exception as e:
        logger.warning("low_quality_ratio 检查失败: %s", e)
    return None


def _check_no_daily_review(pid: str) -> dict | None:
    """检查日报目录是否存在/是否过期。

    目录不存在或为空 → 触发。
    最新 .md 文件时间戳 > 3 天前 → 触发。
    """
    try:
        review_dir = PROJECT_DIR / "document" / "日报"
        if not review_dir.exists() or not review_dir.is_dir():
            return {"event_type": "no_daily_review", "priority": "low", "reason": "日报目录不存在"}
        md_files = sorted(review_dir.glob("*.md"))
        if not md_files:
            return {"event_type": "no_daily_review", "priority": "low", "reason": "日报目录为空"}
        latest_mtime = max(f.stat().st_mtime for f in md_files)
        days_ago = (time.time() - latest_mtime) / 86400
        if days_ago > 3:
            return {"event_type": "no_daily_review", "priority": "low", "reason": f"最新日报已过期 {days_ago:.0f} 天前"}
        logger.debug("no_daily_review 跳过: 最新日报 %.0f 天前", days_ago)
    except Exception as e:
        logger.warning("no_daily_review 检查失败: %s", e)
    return None


def _check_inactive_project(mem, pid: str) -> dict | None:
    """检查项目是否长时间无活动。

    max(timestamp) where type ∈ knowledge_types，差值 > 7 天 → 触发。
    知识库为空时跳过。
    """
    try:
        results = mem.store.get(
            where={"$and": [{"project_id": pid}, {"type": {"$in": _KNOWLEDGE_TYPES}}]},
            include=["metadatas"],
        )
        metadatas = results.get("metadatas", [])
        if not metadatas:
            logger.debug("inactive_project 跳过: 知识库为空")
            return None
        max_ts = max(m.get("timestamp", 0) for m in metadatas)
        days_ago = (time.time() - max_ts) / 86400
        if days_ago > 7:
            return {"event_type": "inactive_project", "priority": "low", "reason": f"项目已 {days_ago:.0f} 天无新知识"}
        logger.debug("inactive_project 跳过: 最近活动 %.0f 天前", days_ago)
    except Exception as e:
        logger.warning("inactive_project 检查失败: %s", e)
    return None


def _check_expired_memories(mem, pid: str) -> dict | None:
    """检查是否存在过期记忆。

    ChromaDB 查询后 Python 侧过滤 expiry_date > 0 AND expiry_date < now()。
    count > 5 → 触发。
    """
    try:
        results = mem.store.get(
            where={"$and": [{"project_id": pid}, {"type": {"$in": _KNOWLEDGE_TYPES}}]},
            include=["metadatas"],
        )
        metadatas = results.get("metadatas", [])
        now = time.time()
        expired = sum(1 for m in metadatas if m.get("expiry_date", 0) > 0 and m.get("expiry_date", 0) < now)
        if expired > 5:
            return {"event_type": "expired_memories", "priority": "low", "reason": f"有 {expired} 条过期记忆未处理"}
        logger.debug("expired_memories 跳过: 过期 %d 条", expired)
    except Exception as e:
        logger.warning("expired_memories 检查失败: %s", e)
    return None


def _build_layered_context(mem, query: str, pid: str) -> tuple[str, list, list]:
    """分层筛选核心逻辑 — v0.4.4 增强版。

    优化：
      3a — final_score 排序（替代 similarity）
      3b — 扩展检索池（含 type=suggestion, feedback=useful）
      3d — 注入多样性采样（type 去重，前 2 条同 type fallthrough）

    返回 (空字符串(兼容占位), suggestions_list, context_candidates)。
    注意：context_str 在 main() 中由统一排序截断后格式化。
    """
    if not mem or not query:
        return "", [], []

    try:
        # v0.4.4 P1-2: 拆分复合查询，避免 ChromaDB $or+$and 嵌套解析限制
        knowledge_results = mem.recall(
            query,
            top_k=10,
            project_id=pid,
            return_scores=True,
            where={"type": {"$in": _KNOWLEDGE_TYPES_FOR_MATCHING}},
        )
        suggestion_results = mem.recall(
            query,
            top_k=5,
            project_id=pid,
            return_scores=True,
            where={"$and": [{"type": "suggestion"}, {"feedback": "useful"}]},
        )
        # Python 层合并去重，按 final_score 降序取 top 10
        seen_ids = set()
        results = []
        for r in knowledge_results + suggestion_results:
            rid = r.get("id")
            if rid and rid not in seen_ids:
                seen_ids.add(rid)
                results.append(r)
        results.sort(key=lambda x: x.get("final_score", 0), reverse=True)
        results = results[:10]
    except Exception as e:
        logger.error("分层检索失败: %s", e)
        return "", [], []
    if not results:
        logger.debug("分层检索: 无结果")
        return "", [], []

    cfg_sug = _get_suggestion_config()

    # 3a: 按 final_score（含 feedback_boost）降序排列
    results.sort(key=lambda x: x.get("final_score", 0), reverse=True)

    context_items = []
    suggestion_items = []

    # 3d: 多样性采样 — 同 type 前 2 条 fallthrough，后续必须不同
    seen_types = set()
    for r in results:
        sim = r.get("similarity", 0)
        meta = r.get("metadata", {}) or {}
        result_type = meta.get("type", "unknown")
        is_useful_suggestion = result_type == "suggestion" and meta.get("feedback") == "useful"

        # Layer 2：仅知识库类型才生成建议（不重复建议已处理的 suggestion）
        if not is_useful_suggestion and sim >= cfg_sug.active_suggestion_threshold:
            if cfg_sug.enable_active_suggestions and not _no_suggestions_file_exists():
                suggestion_items.append(r)

        # Layer 1：阈值过滤 + 多样性采样
        if sim < cfg_sug.context_injection_threshold:
            continue

        # 多样性：前 2 条可同 type，后续必须不同 type
        if result_type not in seen_types or len(context_items) < 2:
            context_items.append(r)
            seen_types.add(result_type)

    logger.debug(
        "分层结果: Layer1=%d 条, Layer2=%d 条 (query=%s, top_sim=%.3f)",
        len(context_items),
        len(suggestion_items),
        query[:50],
        results[0].get("similarity", 0) if results else 0,
    )
    return "", suggestion_items, context_items


def _write_suggestions(mem, suggestions: list, pid: str, query: str) -> int:
    """写入 suggestion 到 ChromaDB，支持三管道合并写入。

    规则：
    - 阈值过滤仅对 active_push（管道一）生效
    - 管道二/三直接写入
    - 写入前 FIFO 清理：pending >= suggestion_max_pending 时按优先级淘汰
    - 所有类型单条异常不阻断整体流程
    """
    if not suggestions:
        return 0

    cfg = _get_suggestion_config()
    expiry_seconds = cfg.suggestion_expiry_days * 86400 if cfg.suggestion_expiry_days > 0 else 365 * 86400
    now = time.time()

    # FIFO 清理：写入前检查 pending 数
    _fifo_cleanup(mem, pid, cfg)

    written = 0

    for s in suggestions:
        try:
            sug_type = s.get("suggestion_type", "active_push")
            content = ""

            if sug_type == "active_push":
                # 管道一：需阈值过滤 + 冷却期 + 每日上限
                sim = s.get("similarity", 0)
                if sim < cfg.active_suggestion_threshold:
                    continue
                source_id = s.get("id", "")
                if _check_suggestion_cooldown(mem, source_id, pid, cfg.suggestion_cooldown_minutes):
                    continue
                if _check_daily_limit(mem, pid, cfg.suggestion_max_per_day):
                    logger.info("管道一每日上限已达，停止写入")
                    break

                content = (s.get("document") or "")[:300]
                meta = s.get("metadata", {}) or {}
                write_meta = {
                    "type": "suggestion",
                    "project_id": pid,
                    "source_memory_id": source_id,
                    "similarity": sim,
                    "query": query[:200],
                    "suggestion_type": "active_push",
                    "status": "pending",
                    "timestamp": now,
                    "source_date": meta.get("created_at", meta.get("timestamp", "")),
                    "source_type": meta.get("type", ""),
                    "expires_at": now + expiry_seconds,
                }

            elif sug_type == "system_alert":
                # 管道二：直接写入（不受阈值约束）
                event_type = s.get("event_type", "unknown")
                reason = s.get("reason", "")
                content = f"【系统提醒】{event_type} — {reason}"[:300]
                write_meta = {
                    "type": "suggestion",
                    "project_id": pid,
                    "suggestion_type": "system_alert",
                    "event_type": event_type,
                    "priority": s.get("priority", "low"),
                    "status": "pending",
                    "timestamp": now,
                    "expires_at": now + expiry_seconds,
                }

            elif sug_type == "manual_trigger":
                # 管道三：直接写入
                content = (s.get("content") or "")[:300]
                sug_meta = s.get("metadata", {}) or {}
                write_meta = {
                    "type": "suggestion",
                    "project_id": pid,
                    "suggestion_type": "manual_trigger",
                    "source_memory_id": s.get("source_memory_id", ""),
                    "similarity": s.get("similarity", 0),
                    "status": "pending",
                    "timestamp": now,
                    "expires_at": now + expiry_seconds,
                    "trigger_keywords": sug_meta.get("trigger_keywords", "[]"),
                    "trigger_mode": sug_meta.get("trigger_mode", "keyword"),
                    "hit_count": sug_meta.get("hit_count", 0),
                    "priority": sug_meta.get("priority", "medium"),
                    "created_by": sug_meta.get("created_by", "user"),
                }

            else:
                logger.warning("未知 suggestion_type: %s", sug_type)
                continue

            mem.remember(content, metadata=write_meta)
            written += 1
            logger.debug("已写入 suggestion: type=%s", sug_type)

        except Exception as e:
            logger.error("写入 suggestion 失败 (type=%s): %s", s.get("suggestion_type", "?"), e)

    logger.info("suggestion 写入完成: %d 条 (共 %d 候选)", written, len(suggestions))
    return written


def _fifo_cleanup(mem, pid: str, cfg) -> None:
    """FIFO 优先级清理：pending 数 >= suggestion_max_pending 时淘汰旧建议。

    T7: 已反馈（status=reacted）永不参与 FIFO 清理——查询条件固定为 status=pending。
    清理顺序：active_push → system_alert → manual_trigger（最后清理用户手工设定）。
    审计修复（P2-4）：改为对所有 suggestion_type 统一计数和清理，不再仅限 active_push。
    """
    max_pending = cfg.suggestion_max_pending
    try:
        pending_where = {
            "$and": [
                {"type": "suggestion"},
                {"project_id": pid},
                {"status": "pending"},  # T7: 仅 pending 参与，reacted 永不被清理
            ]
        }
        pending_count = mem.store.count(where=pending_where)
        if pending_count < max_pending:
            return

        # 需要清理的数量 = pending - max_pending + 1（预留新建议空间）
        to_clean = pending_count - max_pending + 1
        if to_clean <= 0:
            return

        # 获取所有 pending 建议，按优先级排序后清理
        results = mem.store.get(
            where=pending_where,
            include=["metadatas"],
        )
        all_ids = results["ids"]
        all_metas = results["metadatas"]

        # 按 suggestion_type 分组（manual_trigger 最后清理）
        priority_order = {"active_push": 0, "system_alert": 1, "manual_trigger": 2}
        indexed = list(zip(all_ids, all_metas))
        indexed.sort(key=lambda x: priority_order.get(x[1].get("suggestion_type", ""), 0))

        # 清理 to_clean 条
        to_delete_ids = []
        type_counts = {"active_push": 0, "system_alert": 0, "manual_trigger": 0}
        for doc_id, meta in indexed[:to_clean]:
            to_delete_ids.append(doc_id)
            st = meta.get("suggestion_type", "active_push")
            type_counts[st] = type_counts.get(st, 0) + 1

        # 标记为 dismissed
        dismissed_metas = []
        for doc_id in to_delete_ids:
            # 获取原始 metadata
            orig = mem.store.get(ids=[doc_id], include=["metadatas"])
            if orig["metadatas"]:
                m = dict(orig["metadatas"][0])
                m["status"] = "dismissed"
                dismissed_metas.append(m)

        if dismissed_metas:
            mem.store.update(ids=to_delete_ids, metadatas=dismissed_metas)
            logger.info(
                "FIFO 清理了 %d 条旧 pending 建议 (active_push=%d, system_alert=%d, manual_trigger=%d)",
                len(to_delete_ids),
                type_counts.get("active_push", 0),
                type_counts.get("system_alert", 0),
                type_counts.get("manual_trigger", 0),
            )
    except Exception as e:
        logger.warning("FIFO 清理失败: %s", e)


def _save_injected_records(pid: str, records: list) -> None:
    """保存被注入 additionalContext 的 Layer 1 记录（清旧写新）。

    每次调用先覆盖旧文件，只保留最新会话的注入记录。
    Dashboard 通过读取此文件来展示"最近会话注入"列表。
    空 records 时删除旧文件，避免 Dashboard 展示过时数据。
    """
    from memos.config.models import get_memos_home

    path = get_memos_home() / "etc" / f".injected_records_{pid}.json"

    if not records:
        if path.exists():
            path.unlink()
            logger.info("已清除旧的注入记录文件 %s", path)
        else:
            logger.debug("无 Layer 1 注入记录，跳过保存")
        return

    path.parent.mkdir(parents=True, exist_ok=True)

    # 标准化记录字段
    normalized = []
    now = time.time()
    for r in records:
        meta = r.get("metadata", {}) or {}
        normalized.append(
            {
                "id": r.get("id", ""),
                "content": (r.get("document") or "")[:500],
                "similarity": r.get("similarity", 0),
                "final_score": r.get("final_score", 0),
                "source_type": meta.get("type", "unknown"),
                "source_date": "",
                "timestamp": now,
                "suggestion_type": r.get("suggestion_type", "active_push"),
            }
        )

    data = {
        "project_id": pid,
        "updated_at": now,
        "count": len(normalized),
        "records": normalized,
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    logger.info("已保存 %d 条 Layer 1 注入记录到 %s", len(normalized), path)


def main():
    """主入口。

    支持 MEMOS_USE_OLD_CONTEXT=1 环境变量回退到旧版 _build_context。
    main() 级兜底：任何未预期异常输出 {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": ""}}。
    """
    # T5: 五段分段计时（perf_counter，单调高精度）
    _t0 = time.perf_counter()
    _t1 = _t2 = _t3 = _t4 = _t5 = _t0

    # 1. 解析用户消息（强制 UTF-8 解码避免 Windows GBK 编码问题）
    try:
        raw_bytes = sys.stdin.buffer.read()
        input_data = json.loads(raw_bytes.decode("utf-8"))
        current_msg = (input_data.get("prompt") or "").strip()
    except Exception as e:
        logger.warning("读取 stdin 失败: %s", e)
        current_msg = ""

    logger.debug("=== Hook 开始 ===")
    if not current_msg:
        logger.debug("空消息，跳过")
        return

    logger.info("收到用户消息 (%d 字符): %s", len(current_msg), current_msg[:500])

    # 2. 生成 round_id 并保存 user_input 到 ChromaDB
    round_id = f"R_{time.time_ns()}"
    pid = _get_project_id()
    mem = _get_memory()
    user_record_id = ""

    if mem:
        try:
            user_record_id = mem.remember(
                current_msg,
                metadata={
                    "type": "user_input",
                    "project_id": pid,
                    "project_name": PROJECT_DIR.name,
                    "round_id": round_id,
                    "timestamp": time.time(),
                },
            )
            logger.info("已保存用户输入 round=%s, id=%s", round_id, user_record_id)
        except Exception as e:
            logger.error("保存用户输入失败: %s", e)
            import traceback

            logger.error(traceback.format_exc())
    else:
        logger.error("ContextMemory 不可用，无法保存用户输入")

    # 3. 写状态文件（供 Stop Hook 使用）
    state = {
        "round_id": round_id,
        "user_record_id": user_record_id,
        "pending_assistant": True,
    }
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    logger.debug("已写入状态文件 round=%s", round_id)

    _t1 = time.perf_counter()  # stdin 阶段结束

    # 4. 三管道检索与建议生成
    additional_context = ""
    use_old = os.environ.get("MEMOS_USE_OLD_CONTEXT") == "1"
    all_suggestions = []
    pipe_counts = {"active_push": 0, "system_alert": 0, "manual_trigger": 0}
    injected_items = []  # Layer 1 注入记录（旧版路径为空）

    try:
        if use_old:
            logger.info("使用旧版 _build_context (MEMOS_USE_OLD_CONTEXT=1)")
            additional_context = _build_context(mem, current_msg, pid) or ""
        else:
            # 管道一：分层检索（context_str 由统一排序截断后重建）
            _, pipe1, injected_items = _build_layered_context(mem, current_msg, pid)
            all_suggestions.extend(pipe1)
            pipe_counts["active_push"] = len(pipe1)
            _t2 = time.perf_counter()  # 管道一结束

            # 管道二：系统状态型建议（独立 try/except）
            try:
                pipe2 = _generate_system_suggestions(mem, pid)
                all_suggestions.extend(pipe2)
                pipe_counts["system_alert"] = len(pipe2)
            except Exception as e:
                logger.error("管道二生成失败: %s", e)
            _t3 = time.perf_counter()  # 管道二结束

            # 管道三：用户手工型建议（独立 try/except）
            pipe3 = []
            try:
                pipe3 = _match_manual_suggestions(current_msg, mem, pid)
                all_suggestions.extend(pipe3)
                pipe_counts["manual_trigger"] = len(pipe3)
            except Exception as e:
                logger.error("管道三匹配失败: %s", e)

            # 统一排序截断：合并 L1 + 管道三候选，按优先级排序后截断 + 重建上下文
            # （不论管道三是否异常，都执行此合并逻辑）
            _merge_cfg = _get_suggestion_config()
            _max_n = _merge_cfg.max_injection_per_round
            _candidates = []  # (source, priority, item)

            # L1 知识匹配候选（优先级 3）
            for item in injected_items:
                _candidates.append(("knowledge", 3, item))
            # 管道三候选：Always(1) > 关键词(2)
            for item in pipe3:
                _mode = (item.get("metadata", {}) or {}).get("trigger_mode", "keyword")
                _pri = 1 if _mode == "always" else 2
                _candidates.append(("manual", _pri, item))

            # 按优先级排序 + 截断
            _candidates.sort(key=lambda x: x[1])
            _candidates = _candidates[:_max_n]

            # 重建 additional_context + final_injected
            _ctx_parts = []
            _final_injected = []
            for _src, _pri, _item in _candidates:
                if _src == "knowledge":
                    _ctx_parts.append(_format_context_item(_item))
                    _meta = _item.get("metadata", {}) or {}
                    _final_injected.append(
                        {
                            "id": _item.get("id", ""),
                            "document": _item.get("document", ""),
                            "similarity": _item.get("similarity", 0),
                            "final_score": _item.get("final_score", _item.get("similarity", 0)),
                            "metadata": _meta,
                            "suggestion_type": "active_push",
                        }
                    )
                else:  # manual
                    _content = (_item.get("content") or "")[:300]
                    _sug_meta = _item.get("metadata", {}) or {}
                    _mode = _sug_meta.get("trigger_mode", "keyword")
                    if _mode == "always":
                        _kw_info = "[始终触发]"
                    else:
                        _raw_kw = _sug_meta.get("trigger_keywords", "[]")
                        if isinstance(_raw_kw, str):
                            try:
                                _kws = json.loads(_raw_kw)
                            except Exception:
                                _kws = []
                        else:
                            _kws = _raw_kw
                        _kw_info = f"[触发关键词: {', '.join(str(k) for k in _kws)}]" if isinstance(_kws, list) else ""
                    _ctx_parts.append(f"  {_kw_info} {_content}")
                    _final_injected.append(
                        {
                            "id": _item.get("source_memory_id", ""),
                            "document": _content,
                            "similarity": _item.get("similarity", 0),
                            "final_score": _item.get("similarity", 0),
                            "metadata": {"type": _mode},
                            "suggestion_type": "manual_trigger",
                        }
                    )

            # 构建 additional_context
            if _final_injected:
                additional_context = "--- 自动注入（按优先级排序） ---\n" + "\n".join(_ctx_parts) + "\n---"
            else:
                additional_context = ""
            injected_items = _final_injected

            # 合并写入
            if all_suggestions:
                written = _write_suggestions(mem, all_suggestions, pid, current_msg)
            else:
                written = 0

            logger.debug("注入截断: 候选=%d, 上限=%d, 实际注入=%d", len(_candidates), _max_n, len(_final_injected))

            # 汇总日志
            pipe2_detail = ""
            if pipe_counts["system_alert"] > 0:
                events = [
                    s.get("event_type", "?") for s in all_suggestions if s.get("suggestion_type") == "system_alert"
                ]
                pipe2_detail = f"({','.join(events)})"
            _t4 = time.perf_counter()  # 管道三+写入结束

            logger.info(
                "suggestions: pipe1_active_push=%d, pipe2_system_alert=%d%s, pipe3_manual_trigger=%d, total_written=%d",
                pipe_counts["active_push"],
                pipe_counts["system_alert"],
                pipe2_detail,
                pipe_counts["manual_trigger"],
                written,
            )
    except Exception:
        logger.error("分层检索/写入异常，降级为空上下文", exc_info=True)
        additional_context = ""
        _t4 = time.perf_counter()  # 异常分支也要记录计时
    finally:
        # 无论正常/异常/旧版路径，都确保清理旧注入记录
        # 避免 Dashboard 展示过时数据
        _save_injected_records(pid, injected_items)

    # 5. 输出上下文给 Claude Code（兜底：确保 stdout 始终是有效 JSON）
    try:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": additional_context,
            }
        }
        print(json.dumps(output, ensure_ascii=False))
    except Exception:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": ""}}))
    sys.stdout.flush()
    _t5 = time.perf_counter()  # 输出结束

    # T5: hook_timing 单行日志
    logger.info(
        "hook_timing: stdin=%.1fms pipe1=%.1fms pipe2=%.1fms pipe3=%.1fms output=%.1fms total=%.1fms",
        (_t1 - _t0) * 1000,
        (_t2 - _t1) * 1000,
        (_t3 - _t2) * 1000,
        (_t4 - _t3) * 1000,
        (_t5 - _t4) * 1000,
        (_t5 - _t0) * 1000,
    )
    logger.debug("=== Hook 结束 (%.0fms) ===", (_t5 - _t0) * 1000)


if __name__ == "__main__":
    main()
