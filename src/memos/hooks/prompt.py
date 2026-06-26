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

_pid_cache = None


def _get_project_id() -> str:
    global _pid_cache
    if _pid_cache is None:
        _pid_cache = hashlib.md5(str(PROJECT_DIR).encode()).hexdigest()[:8]
    return _pid_cache


def _no_suggestions_file_exists() -> bool:
    """检查免打扰文件是否存在。文件存在即阻断 Layer 2，JSON 解析失败仅记录警告。"""
    if not NO_SUGGESTIONS_FILE.exists():
        return False
    try:
        NO_SUGGESTIONS_FILE.read_text(encoding="utf-8")
    except Exception:
        logger.warning("免打扰文件解析失败，仍阻断建议: %s", NO_SUGGESTIONS_FILE)
    return True


# --- 用户建议匹配（L4 保留）---


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

            # 驳回检查（与 Dashboard list_manual_suggestions 保持一致）
            if meta.get("status") == "dismissed":
                logger.debug("manual_suggestion %s 已驳回，跳过", doc_id[:8])
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
                        "type": meta.get("type", "manual_suggestion"),
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


def _get_suggestion_config():
    """惰性获取 SuggestionConfig。"""
    from memos.config import config as _cfg

    return _cfg.suggestion


# 知识匹配用类型（v0.6.0 L2+L3，排除 watchlist 和 todo）
_KNOWLEDGE_TYPES_FOR_MATCHING = ["solution", "decision", "lesson", "process"]


def _build_layered_context(mem, query: str, pid: str) -> list[dict]:
    """语义检索相关记忆用于上下文注入。

    检索类型限定为 knowledge 层（solution/decision/lesson/process），
    简报和活跃任务由 _format_additional_context 独立注入（避免双重注入）。

    返回 context_candidates 列表，按相似度降序排列。
    """
    if not mem or not query:
        return []

    context_items = []

    # recall 语义检索（solution/decision/lesson/process）
    try:
        knowledge_results = mem.recall(
            query,
            top_k=10,
            project_id=pid,
            return_scores=True,
            where={"type": {"$in": _KNOWLEDGE_TYPES_FOR_MATCHING}},
        )
        results = list(knowledge_results)
        results.sort(key=lambda x: x.get("final_score", 0) or x.get("similarity", 0), reverse=True)
    except Exception as e:
        logger.error("分层检索失败: %s", e)
        return context_items

    if not results:
        logger.debug("分层检索: 无结果")
        return context_items

    cfg_sug = _get_suggestion_config()
    seen_types = {r.get("metadata", {}).get("type", "unknown") for r in context_items}

    for r in results:
        sim = r.get("similarity", 0)
        meta = r.get("metadata", {}) or {}
        result_type = meta.get("type", "unknown")

        if sim < cfg_sug.context_injection_threshold:
            continue

        if result_type not in seen_types or len(context_items) < 2:
            context_items.append(r)
            seen_types.add(result_type)

    logger.debug(
        "上下文注入: %d 条 (query=%s, top_sim=%.3f)",
        len(context_items),
        query[:50],
        results[0].get("similarity", 0) if results else 0,
    )
    return context_items


def _save_injected_records(pid: str, records: list) -> None:
    """保存本轮被注入上下文的记录（清旧写新，供 Dashboard 展示）。

    每次调用先覆盖旧文件，只保留最新会话的注入记录。
    Dashboard 通过读取此文件来展示"最近会话注入"列表。
    空 records 时删除旧文件，避免 Dashboard 展示过时数据。

    兼容两种记录格式：
      - 知识库召回: {"id", "document", "metadata.type"}
      - 用户建议:   {"source_memory_id", "content", "metadata.trigger_mode"}
    """
    from memos.config.models import get_memos_home

    path = get_memos_home() / "etc" / f".injected_records_{pid}.json"

    if not records:
        # 空记录时删除旧文件，避免 Dashboard 展示过时数据
        if path.exists():
            path.unlink()
            logger.debug("本轮无注入记录，已删除旧文件: %s", path)
        return

    path.parent.mkdir(parents=True, exist_ok=True)

    # 标准化记录字段（兼容两种格式：知识库召回 id+document，用户建议 source_memory_id+content）
    normalized = []
    now = time.time()
    for r in records:
        meta = r.get("metadata", {}) or {}
        record_id = r.get("id") or r.get("source_memory_id") or ""
        content = (r.get("document") or r.get("content") or "")[:500]
        source_type = meta.get("type") or meta.get("trigger_mode", "unknown")
        normalized.append(
            {
                "id": record_id,
                "content": content,
                "similarity": r.get("similarity", 0),
                "final_score": r.get("final_score", 0),
                "source_type": source_type,
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


# --- 用户建议可导入函数 ---


def run_manual_suggestion_matching(body: dict, context_memory, project_id: str = None) -> list[dict]:
    """用户建议匹配（基于关键词触发规则）

    入参：
      body: {"prompt": "用户消息", ...}
      context_memory: ContextMemory 实例
      project_id: 项目 ID（None 时自动检测）

    返回 list[dict]，每条含 suggestion_type="manual_trigger"。
    不依赖 sys.stdin / sys.stdout。
    """
    current_msg = (body.get("prompt") or "").strip()
    pid = project_id or _get_project_id()
    if not current_msg or not context_memory:
        return []

    return _match_manual_suggestions(current_msg, context_memory, pid)


if __name__ == "__main__":
    print(
        "[memos] 错误：不支持直接运行 python -m memos.hooks.prompt。\n"
        "v0.5.0 unified 模式下请使用 hook_proxy --hook 代理，\n"
        "或确保 unified server (memos server) 已启动后通过 HTTP Hook 端点调用。\n"
        "详见: document/50版本/analysis_unified_server_chromadb.md",
        file=sys.stderr,
    )
    sys.exit(1)
