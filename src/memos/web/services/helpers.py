"""Dashboard 辅助函数 (v0.4.3 架构重整)"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from ...config import LLMEndpoint, PromptTemplate, config

# 知识卡片类型白名单（Pipeline D 专用）
VALID_CARD_TYPES = {"bug_fix", "feature_design", "code_optimize", "tech_knowledge", "solution", "decision", "lesson", "process"}

# 数据库大小缓存（避免 rglob 遍历大量文件）
_db_size_cache: dict = {"size_mb": 0.0, "cached_at": 0.0}
_DB_SIZE_CACHE_TTL: int = 300  # 5 分钟

logger = logging.getLogger(__name__)


async def _probe_llm_endpoint(endpoint, timeout: float) -> tuple[bool, str, float]:
    """P2-3: 公共探活逻辑，使用 asyncio 异步 TCP 原生连接探活，保障秒退。

    返回 (ok, method, latency_ms)。"""
    import time as _time
    import urllib.parse

    ep = endpoint or config.llm.active_endpoint
    api_base = ep.api_base

    try:
        parsed = urllib.parse.urlparse(api_base)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port
        if not port:
            port = 443 if parsed.scheme == "https" else 80
    except Exception as e:
        return False, f"URL 解析错误: {e}", 0

    t0 = _time.time()
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        latency_ms = round((_time.time() - t0) * 1000)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            logger.debug("TCP 连接关闭等待失败", exc_info=True)
        return True, "TCP Connection", latency_ms
    except asyncio.TimeoutError:
        return False, f"连接超时 ({timeout}s)", 0
    except Exception as e:
        logger.warning("LLM 健康检查连接失败（%s）: %s", api_base, e)
        return False, str(e), 0


async def _check_llama_health(endpoint: LLMEndpoint | None = None) -> bool:
    """检查 LLM 端点是否在线。P2-3: 委托 _probe_llm_endpoint 消除重复。"""
    ok, _, _ = await _probe_llm_endpoint(endpoint, config.dashboard.health_check_timeout)
    return ok


async def _get_llama_status() -> bool:
    from ..app import _status_cache_lock, _system_status_cache  # 惰性导入，避免循环

    now = time.time()
    ttl = config.dashboard.status_cache_ttl
    with _status_cache_lock:
        if now - _system_status_cache["cached_at"] < ttl:
            return _system_status_cache["llama_server_ok"]

    ok = await _check_llama_health()

    with _status_cache_lock:
        _system_status_cache["llama_server_ok"] = ok
        _system_status_cache["cached_at"] = now
    return ok


def _get_notification_context() -> dict:
    """获取通知上下文（注入到所有页面模板）。"""
    try:
        from ...features.notifications import get_notification_logger

        notifier = get_notification_logger()
        unread = notifier.get_unread_counts()
        recent = notifier.get_recent(5)
        # 格式化时间
        for r in recent:
            r["_time_ago"] = _format_time_ago(r.get("timestamp", 0))
        return {"unread": unread, "recent": recent}
    except Exception:
        return {"unread": {"total": 0}, "recent": []}


def _format_time_ago(ts: float) -> str:
    """将时间戳转换为相对时间描述。"""
    import time as _time

    diff = _time.time() - ts
    if diff < 60:
        return "刚刚"
    if diff < 3600:
        return f"{int(diff / 60)} 分钟前"
    if diff < 86400:
        return f"{int(diff / 3600)} 小时前"
    return f"{int(diff / 86400)} 天前"


def _calc_db_size() -> float:
    """计算 ChromaDB 存储大小，只统计 sqlite3 主文件 + WAL + SHM，不做全目录递归。"""
    now = time.time()
    if now - _db_size_cache["cached_at"] < _DB_SIZE_CACHE_TTL:
        return _db_size_cache["size_mb"]
    try:
        db_path = Path(config.chroma.path)
        total = 0
        for fname in ("chroma.sqlite3", "chroma.sqlite3-wal", "chroma.sqlite3-shm"):
            fp = db_path / fname
            if fp.exists():
                total += fp.stat().st_size
        size_mb = round(total / (1024 * 1024), 2)
        _db_size_cache["size_mb"] = size_mb
        _db_size_cache["cached_at"] = time.time()
        return size_mb
    except Exception:
        return 0.0


def _parse_knowledge_cards(raw_text: str) -> list[dict]:
    """解析 LLM 输出的 JSON，提取知识卡片列表"""
    import re

    # 0) 去除 markdown 代码块标记（```json ... ```）
    text = re.sub(r"^[ \t]*```(?:json)?\s*", "", raw_text, flags=re.MULTILINE)
    text = re.sub(r"\n[ \t]*```\s*$", "", text, flags=re.MULTILINE)
    text = text.strip()

    # 1) 直接解析完整 JSON
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [_clean_card(c) for c in parsed if _valid_card(c)]
        if isinstance(parsed, dict) and _valid_card(parsed):
            return [_clean_card(parsed)]
    except json.JSONDecodeError:
        pass

    # 2) 从文本中提取 JSON 数组（贪婪匹配到最后一个 ]）
    arr_match = re.search(r"\[[\s\S]*\]", text)
    if arr_match:
        candidate = arr_match.group(0)
        # 尝试括号匹配找到真正的闭合点
        depth = 0
        for i, ch in enumerate(candidate):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    candidate = candidate[: i + 1]
                    break
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                return [_clean_card(c) for c in parsed if _valid_card(c)]
        except json.JSONDecodeError:
            pass

    # 3) 提取单个 JSON 对象
    obj_match = re.search(r"\{[\s\S]*\}", text)
    if obj_match:
        candidate = obj_match.group(0)
        depth = 0
        for i, ch in enumerate(candidate):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = candidate[: i + 1]
                    break
        try:
            obj = json.loads(candidate)
            if _valid_card(obj):
                return [_clean_card(obj)]
        except json.JSONDecodeError:
            pass

    return []


def _valid_card(card: dict) -> bool:
    """验证知识卡片是否包含必要字段"""
    return bool(card.get("problem") or card.get("solution") or card.get("insight"))


def _clean_card(card: dict) -> dict:
    """清洗单条知识卡片，补充默认值"""
    t = card.get("type", "tech_knowledge")
    if t not in VALID_CARD_TYPES:
        t = "tech_knowledge"
    result = {
        "problem": (card.get("problem") or "").strip(),
        "solution": (card.get("solution") or "").strip(),
        "insight": (card.get("insight") or "").strip(),
        "type": t,
    }
    # v0.4.1: 保留质量评分供用户审核参考
    qs = card.get("quality_score")
    if qs is not None:
        try:
            result["quality_score"] = round(float(qs), 2)
        except (ValueError, TypeError):
            pass
    qr = card.get("quality_reason")
    if qr:
        result["quality_reason"] = str(qr).strip()
    return result


def _template_to_dict(t: "PromptTemplate") -> dict:
    """将 PromptTemplate 转为 API 响应格式"""
    t._sync_from_legacy()
    versions_info = []
    for v in t.versions:
        versions_info.append(
            {
                "version": v.version,
                "changelog": v.changelog,
                "created_at": v.created_at,
                "is_active": v.version == t.active_version,
            }
        )
    # 从模板 ID 提取端点名（{端点}@{类型} 格式）
    known_types = {"extract", "daily-review", "briefing"}
    ep_name = t.id
    if "@" in t.id:
        parts = t.id.rsplit("@", 1)
        if parts[1] in known_types:
            ep_name = parts[0]

    return {
        "id": t.id,
        "name": t.name,
        "endpoint_name": ep_name,
        "description": t.description,
        "template_type": t.template_type,
        "user_template": t.user_template,
        "chat_style": t.chat_style,
        "parameters": t.parameters,
        "active_version": t.active_version,
        "system_prompt_text": t.system_prompt_text,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "draft": t.draft.model_dump(),
        "versions": versions_info,
        "version_count": len(t.versions),
    }


def _find_llm_endpoint(name: str) -> LLMEndpoint | None:
    for ep in config.llm.endpoints:
        if ep.name == name:
            return ep
    return None
