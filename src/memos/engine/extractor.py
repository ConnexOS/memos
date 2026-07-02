import json
import logging
import re
import threading
import time
from typing import Dict, List

import requests

from ..config import config
from .memory import _get_similarity_threshold

# v0.4.4 P2-5: 惰性加载 usage_logger，避免循环导入
_usage_logger = None

logger = logging.getLogger(__name__)

_llm = config.llm
_mem = config.memory


def format_conversation(records: list[dict]) -> str:
    """将对话记录格式化为 User:/Assistant: 文本，按 timestamp 升序排列。

    参数:
        records: 对话记录列表，每项含 type/content/timestamp 字段
                 如 [{"type": "user_input", "content": "...", "timestamp": 100}]

    返回:
        格式化后的对话文本，如:
        User: 第一条消息
        Assistant: 回复
        User: 第二条消息
    """
    sorted_records = sorted(records, key=lambda r: r.get("timestamp", 0))
    parts = []
    for r in sorted_records:
        role = r.get("type", "")
        content = r.get("content", "").strip()
        if not content:
            continue
        if role == "user_input":
            parts.append(f"User: {content}")
        elif role == "assistant_output":
            parts.append(f"Assistant: {content}")
        else:
            parts.append(f"{role}: {content}")
    return "\n".join(parts)


LLM_URL = f"{_llm.api_base.rstrip('/')}/chat/completions"


def get_llm_url() -> str:
    """从当前配置获取活跃端点的 LLM URL"""
    return f"{config.llm.api_base.rstrip('/')}/chat/completions"


def get_llm_api_key() -> str:
    """从当前配置获取活跃端点的 API Key"""
    return config.llm.api_key


def _estimate_tokens(text: str) -> int:
    # 使用固定估算比例（6 字符约 1 token），无需 BufferConfig
    return int(len(text) / 6) if text else 0


def _extract_llm_content(resp_json: dict) -> str:
    """从 LLM 响应中提取文本内容，兼容 /completion 和 /v1/chat/completions 格式。

    若 content 为空但 reasoning_content 存在，回退使用 reasoning_content
    （DeepSeek V4 Flash 等推理模型在 reasoning_in_content=false 时所有输出进 reasoning_content）。
    """
    if resp_json is None:
        return ""
    if "choices" in resp_json:
        try:
            choices = resp_json["choices"]
            if choices and isinstance(choices[0], dict):
                msg = choices[0].get("message", {})
                content = (msg.get("content") or "").strip()
                if content:
                    return content
                # 兜底：content 为空时读 reasoning_content（DeepSeek 推理模型）
                rc = (msg.get("reasoning_content") or "").strip()
                if rc:
                    return rc
                return ""
        except (KeyError, IndexError, TypeError):
            return ""
    return resp_json.get("content", "")


def _strip_think_block(text: str) -> str:
    """剥离 LLM 响应中的推理块。

    支持多种模型格式:
    - <think>...</think>                   — DeepSeek 风格
    - <|channel|>...</channel|>            — Gemma scratchpad（完整标签）
    - <|channel>...<channel|>              — Gemma scratchpad（变体）

    对每种格式处理两种场景：
    a) 闭合标签 → 保留标签外内容
    b) 无闭合标签 → 截断标签位置之后所有内容
    """
    import re

    # 各路推理块的闭合标签对（左 → 右），re.escape 自动处理元字符
    _think_pairs = [
        ("<think>", "</think>"),
        ("<|channel|>", "</channel|>"),
        ("<|channel>", "<channel|>"),
    ]

    for left, right in _think_pairs:
        # 场景 a：剥离闭合的推理块
        pattern = re.escape(left) + r"[\s\S]*?" + re.escape(right)
        text = re.sub(pattern, "", text, flags=re.DOTALL)
        # 场景 b：未闭合的左标签 → 截断之后所有内容
        if left in text:
            text = text[: text.index(left)]

    return text.strip()


class MemoryExtractor:
    def __init__(
        self,
        llm_url: str = None,
        api_key: str = None,
        memory_system=None,
        project_id: str = None,
        project_name: str = None,
    ):
        self.llm_url = llm_url or get_llm_url()
        self.api_key = api_key or get_llm_api_key()
        self.memory = memory_system
        self.project_id = project_id
        self.project_name = project_name or project_id
        self._lock = threading.Lock()

    def _request_with_retry(self, payload: dict, max_retries: int = None, base_delay: float = None):
        if max_retries is None:
            max_retries = _llm.max_retries
        if base_delay is None:
            base_delay = _llm.retry_base_delay
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        for attempt in range(max_retries):
            try:
                resp = requests.post(self.llm_url, json=payload, headers=headers, timeout=_llm.request_timeout)
            except Exception as e:
                logger.warning("LLM 请求异常 (attempt %d/%d): %s", attempt + 1, max_retries, e)
                if attempt < max_retries - 1:
                    time.sleep(base_delay * (2**attempt))
                continue

            if resp.status_code == 200:
                return resp

            # 400 — 请求体错误，重试无意义
            if resp.status_code == 400:
                logger.error(
                    "LLM 请求已被拒绝 (400): %s (payload.model=%s, messages=%d)",
                    resp.text[:300],
                    payload.get("model", "N/A"),
                    len(payload.get("messages", [])),
                )
                return None

            # 429 — 限流，激进退避
            if resp.status_code == 429:
                sleep_time = base_delay * (4**attempt)
                logger.warning("LLM 限流 (429, attempt %d/%d)，等待 %.1fs", attempt + 1, max_retries, sleep_time)
                if attempt < max_retries - 1:
                    time.sleep(sleep_time)
                continue

            # 413 — payload 超限，截断 user 消息再重试
            if resp.status_code == 413:
                logger.warning("LLM payload 超限 (413, attempt %d/%d)", attempt + 1, max_retries)
                truncated = False
                for msg in reversed(payload.get("messages", [])):
                    if msg.get("role") == "user" and msg.get("content"):
                        original = msg["content"]
                        msg["content"] = original[: len(original) // 2] + "\n\n[内容被截断...]"
                        truncated = True
                        break
                if not truncated or attempt >= max_retries - 1:
                    return None
                continue

            # 5xx — 临时故障，标准退避重试
            logger.warning(
                "LLM returned %d (attempt %d/%d): %s", resp.status_code, attempt + 1, max_retries, resp.text[:300]
            )
            if attempt < max_retries - 1:
                time.sleep(base_delay * (2**attempt))

        return None

    def _get_prompt(self, endpoint_name: str = None, template_type: str = "extract"):
        """从 PromptManager 获取当前活跃端点对应的提示词模板（按类型查找）。

        Fallback 使用 PromptManager 的默认提取 prompt。
        """
        from ..config import PromptTemplate
        from ..config.prompts import _get_default_extract_prompt

        try:
            if endpoint_name is None:
                endpoint_name = config.llm.active_endpoint.name if config.llm.active_endpoint else "default"
            tpl = config.prompt.get_for_endpoint(endpoint_name, template_type)
            if tpl:
                tpl._sync_from_legacy()
                return tpl
        except Exception as e:
            logger.warning("获取提示词模板失败: %s，使用内置默认", e)

        # fallback: 通过 PromptManager 获取默认提取 prompt
        fallback = PromptTemplate(id="fallback", system_prompt_text=_get_default_extract_prompt())
        fallback._sync_from_legacy()
        return fallback

    def _build_extract_payload(self, conversation_text: str, prompt_version: str = None) -> dict:
        """使用 PromptManager 构建 LLM 请求体。统一通过 _get_prompt 获取模板（含异常保护）。"""
        endpoint_name = config.llm.active_endpoint.name if config.llm.active_endpoint else "default"
        tpl = self._get_prompt(endpoint_name)
        return tpl.build_payload(conversation_text, version_override=prompt_version or None)

    def extract(self, conversation_text: str, prompt_version: str = None) -> List[Dict]:
        payload = self._build_extract_payload(conversation_text, prompt_version)
        if "temperature" not in payload:
            payload["temperature"] = _llm.temperature
        if "max_tokens" not in payload:
            payload["max_tokens"] = _llm.max_tokens
        if "stop" not in payload:
            payload["stop"] = _llm.stop if _llm.stop else ["<|im_end|>"]
        model_name = config.llm.active_endpoint.model
        if model_name:
            payload["model"] = model_name

        resp = self._request_with_retry(payload)
        if resp is None:
            return []

        try:
            resp_json = resp.json()
        except Exception:
            logger.warning("LLM 响应非 JSON（HTTP %d）: %s", resp.status_code, resp.text[:500])
            return []

        raw_text = _extract_llm_content(resp_json)
        if not raw_text:
            logger.info("LLM 响应无可提取文本（HTTP %d）", resp.status_code)
            return []

        cleaned = _strip_think_block(raw_text)
        if not cleaned:
            logger.info("LLM 仅输出了 <think> 思考块, 无实际内容: %s", raw_text[:300])
            return []
        raw_text = cleaned

        # 1) 直接解析完整 JSON
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and parsed.get("content"):
                return [parsed]
        except json.JSONDecodeError:
            pass

        # 2) 从文本中提取 JSON 数组（兼容 markdown 包裹、多余文本）
        arr_match = re.search(r"\[[\s\S]*?\]", raw_text)
        if arr_match:
            try:
                return json.loads(arr_match.group(0))
            except json.JSONDecodeError:
                pass

        # 3) 提取单个 JSON 对象
        obj_match = re.search(r"\{[\s\S]*?\}", raw_text)
        if obj_match:
            try:
                obj = json.loads(obj_match.group(0))
                if isinstance(obj, dict) and obj.get("content"):
                    return [obj]
            except json.JSONDecodeError:
                pass

        logger.info("LLM 响应无法解析为记忆, 原始响应(前500字): %s", raw_text[:500])
        return []

    def store_memories(self, memories: List[Dict]) -> int:
        stored = 0
        if not self.memory:
            for m in memories:
                logger.info("  - %s (%s)", m["content"], m["type"])
            return len(memories)

        for mem in memories:
            content = mem.get("content")
            mem_type = mem.get("type", "solution")
            if not content:
                continue

            if hasattr(self.memory, "recall_with_scores"):
                similar = self.memory.recall_with_scores(
                    content, top_k=_mem.dedup_top_k, project_id=self.project_id, where={"type": mem_type}
                )
                if similar:
                    dist = similar[0]["distance"]
                    if dist < _get_similarity_threshold():
                        logger.info("Skipping duplicate (dist=%.3f): %s", dist, content)
                        continue

            meta = {"type": mem_type, "source": "auto_extracted"}

            # v0.4.1: 质量评分
            quality_score = mem.get("quality_score", None)
            if quality_score is not None:
                try:
                    quality_score = float(quality_score)
                except (ValueError, TypeError):
                    quality_score = 0.5
            else:
                quality_score = 0.5  # LLM 未返回时默认 0.5（中性值，纳入复审）
            quality_reason = mem.get("quality_reason", "")
            meta["quality_score"] = quality_score
            meta["quality_reason"] = quality_reason

            if self.project_id:
                meta["project_id"] = self.project_id
                meta["project_name"] = self.project_name
            new_id = self.memory.remember(content, metadata=meta)
            logger.info("Stored: %s (%s) score=%.2f", content, mem_type, quality_score)
            # v0.4.1: 异步冲突检测
            if new_id:
                self._detect_conflicts_async(content, new_id)
            stored += 1
        return stored

    def extract_and_store(self, conversation_text: str) -> int:
        start_time = time.time()
        memories = self.extract(conversation_text)
        duration_ms = int((time.time() - start_time) * 1000)
        endpoint_name = config.llm.active_endpoint.name if config.llm.active_endpoint else "default"
        if memories:
            stored = self.store_memories(memories)
            # v0.4.1: 记录成功事件
            self._log_usage(
                "extract_auto_success",
                endpoint_name,
                len(memories),
                _estimate_tokens(conversation_text),
                0,
                duration_ms,
            )
            # v0.4.2: 提炼完成通知（含内容摘要）
            self._notify_extract_complete(len(memories), memories)
            return stored
        logger.debug("No memories extracted.")
        # v0.4.1: 记录失败事件
        self._log_usage("extract_auto_failed", endpoint_name, 0, _estimate_tokens(conversation_text), 0, duration_ms)
        return 0

    @staticmethod
    def _log_usage(event: str, endpoint: str, mem_count: int, input_tokens: int, output_tokens: int, duration_ms: int):
        """记录用量统计事件（不阻塞主流程）。v0.4.4 P2-5: 模块级惰性加载。"""
        global _usage_logger
        try:
            if _usage_logger is None:
                from ..features.usage import usage_logger as _ul

                _usage_logger = _ul

            _usage_logger.log(
                {
                    "timestamp": time.time(),
                    "endpoint": endpoint,
                    "event": event,
                    "memories_extracted": mem_count,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "duration_ms": duration_ms,
                }
            )
        except Exception:
            logger.debug("统计记录失败，不影响提炼", exc_info=True)

    @staticmethod
    def _notify_extract_complete(count: int, memories: list = None):
        """提炼完成通知（不阻塞主流程）。"""
        try:
            from ..features.notifications import get_notification_logger

            notifier = get_notification_logger()
            # 从 memories 提取前 3 条内容摘要作为预览
            previews = []
            extracted_ids = []
            if memories:
                for m in memories[:3]:
                    content = m.get("content", "")
                    previews.append(content[:40] + ("..." if len(content) > 40 else ""))
                    mem_id = m.get("id", "")
                    if mem_id:
                        extracted_ids.append(mem_id)
            preview_text = "；".join(previews) if previews else ""
            message = f"LLM 自动提炼完成，新增 {count} 条知识记忆。"
            if preview_text:
                message += f" 摘要：{preview_text}"

            notifier.notify(
                type="extract_complete",
                title=f"提炼完成 — {count} 条新记忆",
                message=message,
                metadata={"extracted_count": count, "extracted_ids": extracted_ids, "previews": previews},
            )
        except Exception:
            logger.debug("提炼完成通知推送失败，不影响提炼", exc_info=True)

    # v0.4.1: 冲突检测异步机制
    _conflict_semaphore = threading.Semaphore(3)  # 类变量：全局限制并发 LLM 冲突检测数，保护 API 资源

    def _detect_conflicts_async(self, new_content: str, new_memory_id: str):
        """后台异步检测冲突（独立线程 + Semaphore 控流），不回写结果到调用方"""
        if not config.memory.conflict_detection_enabled:
            return

        def _run():
            if not self._conflict_semaphore.acquire(blocking=False):
                logger.debug("冲突检测并发已满，跳过: %s", new_memory_id[:8])
                return
            try:
                # Step 1: 检索语义相似记忆
                similar = self.memory.recall_with_scores(
                    new_content,
                    top_k=3,
                    project_id=self.project_id,
                    where={"type": {"$in": ["solution", "decision", "lesson", "process"]}},
                )
                candidates = [
                    s
                    for s in similar
                    if s.get("id", "") != new_memory_id
                    and s.get("distance", 1.0) < config.memory.conflict_distance_threshold
                    and s.get("metadata", {}).get("conflict_status", "") not in ("dismissed",)
                ]
                if not candidates:
                    return

                # v0.4.5 Phase 1.2: conflict_use_llm=false 分支 — 纯向量判断
                if not config.memory.conflict_use_llm:
                    best = candidates[0]
                    conflict_with = best.get("id", "")
                    sim = 1 - best.get("distance", 0)
                    reason = f"向量相似度 {sim:.2f} 触发冲突标记（降级模式）"
                    now = time.time()
                    self.memory.update_memory(
                        new_memory_id,
                        new_metadata={
                            "conflict_status": "pending",
                            "conflict_role": "trigger",
                            "conflict_with": conflict_with,
                            "conflict_reason": reason,
                            "conflict_detected_at": now,
                        },
                    )
                    if conflict_with:
                        try:
                            self.memory.update_memory(
                                conflict_with,
                                new_metadata={
                                    "conflict_status": "pending",
                                    "conflict_role": "matched",
                                    "conflict_with": new_memory_id,
                                    "conflict_reason": reason,
                                    "conflict_detected_at": now,
                                },
                            )
                        except Exception:
                            logger.debug("冲突对方记忆更新失败: %s", conflict_with[:8])
                    logger.info("冲突检测到(向量降级): %s <-> %s sim=%.2f", new_memory_id[:8], conflict_with[:8], sim)
                    # 通知推送
                    try:
                        from ..features.notifications import get_notification_logger

                        get_notification_logger().notify(
                            type="conflict_detected",
                            title=f"记忆冲突 — {reason[:30]}{'...' if len(reason) > 30 else ''}",
                            message=(
                                f"记忆 {new_memory_id[:8]} 与 {conflict_with[:8] if conflict_with else '?'}"
                                f" 存在内容冲突（降级模式），需人工审查。"
                            ),
                            link="/?tab=knowledge",
                            metadata={
                                "memory_id": new_memory_id,
                                "conflict_with": conflict_with,
                                "reason": reason,
                                "detected_at": now,
                            },
                        )
                    except Exception:
                        logger.debug("冲突通知推送失败", exc_info=True)
                    return

                # Step 2: 调用 LLM 判断矛盾
                tpl = config.prompt.get_for_endpoint(
                    config.llm.active_endpoint.name if config.llm.active_endpoint else "default",
                    template_type="conflict",
                )
                if tpl is None:
                    tpl = config.prompt.get_for_endpoint("default", template_type="conflict")
                if tpl is None:
                    logger.warning("冲突检测模板不存在，跳过")
                    return

                conflict_input = json.dumps(
                    {
                        "new_content": new_content,
                        "existing_memories": [
                            {"id": c.get("id", ""), "content": c.get("document", "")} for c in candidates
                        ],
                    },
                    ensure_ascii=False,
                )
                payload = tpl.build_payload(conflict_input)
                payload.setdefault("temperature", config.llm.temperature)
                payload.setdefault("max_tokens", 512)
                model_name = config.llm.active_endpoint.model
                if model_name:
                    payload["model"] = model_name

                resp = self._request_with_retry(payload)
                if resp is None:
                    logger.warning("冲突检测 LLM 调用失败（已重试）")
                    return

                result = resp.json()
                raw_text = _extract_llm_content(result)
                cleaned = _strip_think_block(raw_text)
                # 使用多级 JSON 回退（同 extract()）
                try:
                    parsed = json.loads(cleaned)
                except (json.JSONDecodeError, ValueError):
                    arr_match = re.search(r"\[[\s\S]*?\]", cleaned)
                    if arr_match:
                        try:
                            parsed = json.loads(arr_match.group(0))
                        except (json.JSONDecodeError, ValueError):
                            logger.warning("冲突检测 JSON 解析失败（数组回退也无效）")
                            return
                    else:
                        logger.warning("冲突检测 JSON 解析失败")
                        return

                if parsed.get("has_conflict"):
                    conflict_with = parsed.get("conflict_with", "")
                    reason = parsed.get("reason", "")
                    now = time.time()
                    # 更新新记忆的 metadata
                    self.memory.update_memory(
                        new_memory_id,
                        new_metadata={
                            "conflict_status": "pending",
                            "conflict_role": "trigger",
                            "conflict_with": conflict_with,
                            "conflict_reason": reason,
                            "conflict_detected_at": now,
                        },
                    )
                    # 更新对方记忆的 metadata
                    if conflict_with:
                        try:
                            self.memory.update_memory(
                                conflict_with,
                                new_metadata={
                                    "conflict_status": "pending",
                                    "conflict_role": "matched",
                                    "conflict_with": new_memory_id,
                                    "conflict_reason": reason,
                                    "conflict_detected_at": now,
                                },
                            )
                        except Exception:
                            logger.debug("冲突对方记忆更新失败: %s", conflict_with[:8])
                    logger.info(
                        "冲突检测到: %s <-> %s reason=%s",
                        new_memory_id[:8],
                        conflict_with[:8] if conflict_with else "?",
                        reason,
                    )
                    # v0.4.2: 冲突检测通知
                    try:
                        from ..features.notifications import get_notification_logger

                        get_notification_logger().notify(
                            type="conflict_detected",
                            title=f"记忆冲突 — {reason[:30]}{'...' if len(reason) > 30 else ''}",
                            message=(
                                f"记忆 {new_memory_id[:8]} 与 {conflict_with[:8] if conflict_with else '?'}"
                                f" 存在内容冲突，需人工审查。"
                            ),
                            link="/?tab=knowledge",
                            metadata={
                                "memory_id": new_memory_id,
                                "conflict_with": conflict_with,
                                "reason": reason,
                                "detected_at": now,
                            },
                        )
                    except Exception:
                        logger.debug("冲突通知推送失败", exc_info=True)
            except Exception as e:
                logger.warning("冲突检测失败（降级）: %s", e)
            finally:
                self._conflict_semaphore.release()

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    def _persist_raw_conversation(self, user_msg: str, assistant_msg: str, scope: str = None, creator_id: str = None):
        """后台异步持久化原始对话。embedding 编码（~500ms）不阻塞 MCP 响应。"""
        if not self.memory:
            return
        meta = {
            "type": "conversation",
            "user_msg": user_msg,
            "assistant_summary": assistant_msg,
            "timestamp": time.time(),
        }
        if self.project_id:
            meta["project_id"] = self.project_id
            meta["project_name"] = self.project_name
        if scope:
            meta["scope"] = scope
        if creator_id:
            meta["creator_id"] = creator_id
        text = f"用户: {user_msg}\n助手: {assistant_msg}"
        t = threading.Thread(target=self._do_persist_conversation, args=(text, meta), daemon=True)
        t.start()

    def _do_persist_conversation(self, text: str, meta: dict):
        """实际执行 embedding + ChromaDB 写入的线程目标。"""
        try:
            self.memory.remember(text, metadata=meta)
        except Exception as e:
            logger.error("原始对话持久化失败: %s", e)

    # append_conversation() 和 buffer_remember() 已随 v0.6.0 移除
