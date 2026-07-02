"""Stop Hook task 异步处理队列。

接收 TASK_EVAL → 格式校验 → 推入待处理队列 → 立即返回 200 OK
后台守护线程异步消费 → MEMOS LLM 结构化（附录 B prompt） → 写入 ChromaDB
"""

import json
import logging
import queue
import threading
import time
from pathlib import Path

from ..config.models import get_memos_home
from ..features.event_bus import touch_event

logger = logging.getLogger(__name__)

# 附录 B — task 结构化 System Prompt
_TASK_STRUCTURIZE_SYSTEM_PROMPT = """你是一个任务进度结构化引擎。将以下 Claude Code 输出的自由文本任务进度转化为结构化 JSON。

接受输入格式：可能包含 [TASK_EVAL] 标记或不含标记的纯文本。
输出格式严格如下，不含额外说明：

{
  "project": "当前项目名称（如无法判断则设为 general）",
  "goal": "当前目标的简洁描述（10-30字）",
  "progress": {
    "done": ["已完成事项列表，每项一句话"],
    "todo": ["待完成事项列表"],
    "blocked": ["阻塞项列表，无则空数组"]
  },
  "next_steps": ["下一步行动，1-3项"],
  "confidence": 0.0-1.0之间的浮点数（根据自评文本的明确程度评估）
}

约束：
- 如果输入中没有项目信息，project 设为 "general"
- 如果输入信息不足以填充某个字段，用空数组替代
- confidence < 0.5 时附带原始文本在 metadata.raw_text 字段
- 输出必须是合法 JSON，不得包含 ```json 标记"""


_BACKLOG_FILE = "etc/.task_eval_backlog.json"
_lock = threading.Lock()


class TaskEvalQueue:
    """TASK_EVAL 待处理队列（内存队列 + 文件持久化）。

    每次 enqueue 同步持久化到 etc/.task_eval_backlog.json，
    _process_item 成功后移除，启动时自动回放未处理条目。
    """

    def __init__(self, memory_instance=None, llm_caller=None):
        self._queue = queue.Queue()
        self._memory = memory_instance
        self._llm_caller = llm_caller
        self._running = False
        self._thread = None
        self._backlog_path = None

    def _get_backlog_path(self) -> Path:
        if self._backlog_path is None:
            from ..config.models import get_memos_home

            self._backlog_path = get_memos_home() / _BACKLOG_FILE
        return self._backlog_path

    def _read_backlog(self) -> list[dict]:
        path = self._get_backlog_path()
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            logger.warning("task backlog 读取失败，忽略")
            return []

    def _write_backlog(self, items: list[dict]):
        path = self._get_backlog_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False)

    def _replay_backlog(self):
        pending = self._read_backlog()
        if not pending:
            return
        logger.info("回放 task backlog: %d 条待处理", len(pending))
        for item in pending:
            self._queue.put_nowait(item)
        # 清空 backlog（回放的条目会在 _process_item 中重新持久化）
        self._write_backlog([])

    def start(self):
        if self._running:
            return
        self._running = True
        # 启动时回放 backlog
        self._replay_backlog()
        self._thread = threading.Thread(target=self._consume_loop, daemon=True, name="task-eval-consumer")
        self._thread.start()
        logger.info("TaskEvalQueue 后台消费线程已启动")

    def stop(self):
        self._running = False
        logger.info("TaskEvalQueue 已停止")

    def enqueue(self, task_eval: dict, session_id: str, project_id: str, project_name: str = "") -> bool:
        item = {
            "task_eval": task_eval,
            "session_id": session_id,
            "project_id": project_id,
            "project_name": project_name,
            "received_at": time.time(),
        }
        self._queue.put_nowait(item)

        # 持久化到 backlog
        backlog = self._read_backlog()
        backlog.append(item)
        self._write_backlog(backlog)

        logger.info("TASK_EVAL 已入队: session=%s", session_id)
        return True

    def _remove_from_backlog(self, item: dict):
        """从 backlog 中移除指定条目（按 received_at 匹配）。"""
        received_at = item.get("received_at")
        if received_at is None:
            return
        backlog = self._read_backlog()
        before = len(backlog)
        backlog = [i for i in backlog if i.get("received_at") != received_at]
        if len(backlog) < before:
            self._write_backlog(backlog)

    def _consume_loop(self):
        while self._running:
            try:
                item = self._queue.get(timeout=1)
                self._process_item(item)
                self._remove_from_backlog(item)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error("task 消费异常: %s", e)

    def _structurize_task(self, task_eval: dict, project_name: str = "") -> dict | None:
        """调用 MEMOS LLM 将自评文本结构化为 task 对象。

        遵循规格书附录 B 的 prompt 模板。
        若 LLM 不可用，直接使用原始 task_eval 数据作为降级方案。
        project_name: 当 LLM/降级都无法确定 project 时用于兜底。
        """
        if self._llm_caller is None:
            return self._build_task_from_raw(task_eval, project_name)

        try:
            user_prompt = f"请将以下任务自评文本结构化：\n\n{json.dumps(task_eval, ensure_ascii=False)}"
            result = self._llm_caller(_TASK_STRUCTURIZE_SYSTEM_PROMPT, user_prompt)
            if result:
                if isinstance(result, str):
                    result = result.strip()
                    if result.startswith("```"):
                        result = result.split("\n", 1)[-1]
                        result = result.rsplit("\n```", 1)[0]
                parsed = json.loads(result) if isinstance(result, str) else result
                # LLM 结构化后 project 仍是 general 且有真实项目名则覆盖
                if parsed and parsed.get("project") in ("general", "") and project_name:
                    parsed["project"] = project_name
                return parsed
        except Exception as e:
            logger.warning("MEMOS LLM 结构化失败，降级为直接使用原始数据: %s", e)

        return self._build_task_from_raw(task_eval, project_name)

    def _build_task_from_raw(self, task_eval: dict, project_name: str = "") -> dict:
        """直接使用原始 task_eval 数据构建 task（降级路径）。

        project_name 可选：当 TASK_EVAL 未提供 project 时，
        用此值兜底，避免始终显示 "general"。
        """
        project = task_eval.get("project", "general")
        if project == "general" and project_name:
            project = project_name
        return {
            "project": project,
            "goal": task_eval.get("goal", ""),
            "progress": {
                "done": task_eval.get("done", []),
                "todo": task_eval.get("todo", []),
                "blocked": task_eval.get("blocked", []),
            },
            "next_steps": task_eval.get("next_steps", task_eval.get("todo", [])),
            "confidence": 0.7,
        }

    @staticmethod
    def _get_task_mode_file(project_id: str):
        """获取 per-project 任务模式标记文件路径。"""
        return get_memos_home() / "etc" / f".task_mode_{project_id}"

    def _get_task_mode(self, project_id: str) -> str:
        """读取任务模式，缺省返回 'manual'。"""
        mode_file = self._get_task_mode_file(project_id)
        if mode_file.exists():
            try:
                data = json.loads(mode_file.read_text(encoding="utf-8"))
                return data.get("mode", "manual")
            except (json.JSONDecodeError, OSError):
                logger.debug("task_mode 文件读取失败，使用 manual 兜底")
        return "manual"

    def _process_item(self, item: dict):
        """处理单条 TASK_EVAL 项：结构化 → 同链检查 → 写入/更新 ChromaDB。"""
        task_eval = item["task_eval"]
        session_id = item["session_id"]
        project_id = item["project_id"]
        project_name = item.get("project_name", "")

        structured = self._structurize_task(task_eval, project_name)
        if structured is None:
            logger.warning("task 结构化失败，跳过: session=%s", session_id)
            return

        if self._memory is None:
            logger.warning("memory 实例不可用，无法写入 task")
            return

        now = time.time()
        metadata = {
            "type": "task",
            "source": "auto_extracted",
            "source_info": json.dumps(
                {
                    "session_id": session_id,
                    "conversation_count": 0,
                    "note": "",
                }
            ),
            "project_id": project_id,
            "project": structured.get("project", "general"),
            "goal": structured.get("goal", ""),
            "confidence": structured.get("confidence", 0.7),
            "status": "active",
            "paused": False,
            "created_at": now,
            "updated_at": now,
        }

        # 同链检查：相同 project_id 的现有 active task（仅用 UUID 匹配，避免 project 可读名不一致时重复）
        try:
            existing = self._memory.store.get(
                where={
                    "$and": [
                        {"type": "task"},
                        {"project_id": project_id},
                        {"status": "active"},
                    ]
                },
                limit=1,
                include=["metadatas", "documents"],
            )
            if existing["ids"]:
                active_id = existing["ids"][0]

                # 检查任务模式
                mode = self._get_task_mode(project_id)

                if mode == "auto":
                    # 自动模式：完成旧活跃任务，新建任务为 active
                    self._memory.update_memory(
                        active_id,
                        new_metadata={"status": "completed", "paused": False, "updated_at": now},
                    )
                    auto_meta = {
                        "type": "task",
                        "status": "active",
                        "project_id": project_id,
                        "project": structured.get("project", "general"),
                        "goal": structured.get("goal", ""),
                        "confidence": structured.get("confidence", 0.7),
                        "source": "auto_extracted",
                        "source_info": json.dumps(
                            {
                                "session_id": session_id,
                                "conversation_count": 0,
                                "note": "",
                            }
                        ),
                        "created_at": now,
                        "updated_at": now,
                    }
                    self._memory.remember(
                        json.dumps(structured, ensure_ascii=False),
                        metadata=auto_meta,
                    )
                    touch_event("task")
                    logger.info("task 自动轮换: project=%s", structured.get("project", "general"))
                    return

                # 手动模式（默认）：创建 pending 记录
                # ① 创建 pending 记录（完整 TASK_EVAL 快照）
                pending_meta = {
                    "type": "task",
                    "status": "pending",
                    "project_id": project_id,
                    "project": structured.get("project", "general"),
                    "goal": structured.get("goal", ""),
                    "confidence": structured.get("confidence", 0.7),
                    "source": "auto_extracted",
                    "source_info": json.dumps(
                        {
                            "session_id": session_id,
                            "conversation_count": 0,
                            "note": "",
                        }
                    ),
                    "created_at": now,
                    "updated_at": now,
                }
                self._memory.remember(
                    json.dumps(structured, ensure_ascii=False),
                    metadata=pending_meta,
                )
                # ② 更新活跃 task 的 updated_at（document 不动）
                self._memory.update_memory(active_id, new_metadata={"updated_at": now})
                touch_event("task")
                logger.info("task pending 已创建: project=%s", structured.get("project", "general"))
                return
        except Exception as e:
            logger.warning("task 同链检查失败，尝试新建: %s", e)

        # 新建 task
        content_text = json.dumps(structured, ensure_ascii=False)
        self._memory.remember(content_text, metadata=metadata)
        touch_event("task")
        logger.info("task 已创建: project=%s", structured.get("project", "general"))

        # 冷启动标记（路径与 hook_handler._get_cold_start_path 保持一致）
        cold_start_file = get_memos_home() / "etc" / f".cold_start_done_{project_id}"
        if not cold_start_file.exists():
            cold_start_file.parent.mkdir(parents=True, exist_ok=True)
            cold_start_file.write_text(
                json.dumps({"done": True, "created_at": now}, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.info("冷启动标记已创建: project=%s", project_id)
