"""v0.6.0 关键 E2E 场景测试（Phase 1-6 全链路集成测试）。"""

import json
import time
from pathlib import Path


def _get_memory():
    from memos.engine.memory import ContextMemory

    return ContextMemory()


class TestPhase1Connectivity:
    """Phase 1: 基础连通性测试"""

    def test_health_endpoint(self, unified_client):
        """H1: 健康检查端点"""
        resp = unified_client.get("/api/health")
        assert resp.status_code == 200

    def test_task_eval_endpoint(self, unified_client):
        """H2: Task Eval 接收端点"""
        resp = unified_client.post("/api/task/eval", json={
            "task_eval": {"done": ["测试"], "todo": [], "blocked": []},
            "session_id": "int-h2-001",
            "project_id": "health-test",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"


class TestV060E2E:
    """E2E 验证 6 个缺失场景。"""

    def test_remember_writes_watchlist(self):
        """场景 2: remember() 写入 watchlist + 出现在待关注面板"""
        mem = _get_memory()
        mid = mem.remember("test watchlist content", metadata={"type": "watchlist", "project_id": "test"})
        assert mid is not None
        results = mem.list_memories(type_filter="watchlist")
        ids = [r["id"] for r in results]
        assert mid in ids

    def test_save_knowledge_type_validation(self):
        """场景 1: save_knowledge(type='task') 返回参数错误"""
        from memos.server.mcp import save_knowledge

        result = save_knowledge("test", type="task")
        assert "无效类型" in result

    def test_save_knowledge_type_empty_defaults_solution(self):
        """场景 1b: save_knowledge(type=None) 默认 solution"""
        from memos.server.mcp import save_knowledge

        result = save_knowledge("test knowledge", type=None)
        assert "已直接保存" in result or "已存在相同知识" in result

    def test_fallback_briefing_generation(self):
        """场景 5: 兜底简报生成正确（无 LLM）"""
        from memos.features.briefing import build_fallback_briefing

        result = build_fallback_briefing()
        assert result["source"] == "lazy_hook"
        assert result["quality"] == "simple"

    def test_activity_log_rotation(self):
        """场景 5b: 活动日志按天轮转"""
        from memos.features.activity_log import _get_log_filename

        fname = _get_log_filename("2026-06-14")
        assert "activity_log_2026-06-14.jsonl" in fname

    def test_v2_config_behavior_guide_gone(self, unified_client):
        """场景 3: 行为引导配置面板 — F13 已移除，返回 404"""
        resp = unified_client.get("/api/v2/config/behavior-guide")
        assert resp.status_code == 404

    def test_v2_task_current_endpoint(self, unified_client):
        """场景 4: task 端点可访问"""
        resp = unified_client.get("/api/v2/task/current")
        assert resp.status_code == 200
        data = resp.json()
        assert "task" in data or "message" in data

    def test_save_knowledge_manual_suggestion(self):
        """场景 6: save_knowledge(type='manual_suggestion') 修复后可达"""
        from memos.server.mcp import save_knowledge

        result = save_knowledge(
            "test manual", type="manual_suggestion", metadata={"trigger_keywords": ["test"]}
        )
        # 应走到 _save_manual_suggestion 而非返回"无效类型"
        assert "无效类型" not in result


class TestPhase2PromptHook:
    """Phase 2: Prompt Hook 核心链路"""

    def test_prompt_full_chain(self, unified_client):
        """A1: 完整 Prompt Hook 返回 correct 结构"""
        resp = unified_client.post("/api/hooks/prompt", json={
            "conversation_id": "int-a1-001",
            "user_input": "请帮我分析项目的技术架构",
            "assistant_output": "项目使用FastAPI框架，前端采用Vue3",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["additional_context"], str)

    def test_context_contains_behavior_guide(self, unified_client):
        """A3: additional_context 包含行为引导（从文件或默认值注入）"""
        resp = unified_client.post("/api/hooks/prompt", json={
            "conversation_id": "int-a3-001",
            "user_input": "遇到一个报错",
        })
        assert resp.status_code == 200
        ctx = resp.json()["additional_context"]
        assert "save_knowledge" in ctx, f"行为引导文本未注入到 context: {ctx[:200]}"

    def test_prompt_writes_c_pipeline(self, unified_client):
        """A5: Prompt Hook 写入 C 管线（ChromaDB 持久化）"""
        resp = unified_client.post("/api/hooks/prompt", json={
            "conversation_id": "int-a5-001",
            "user_input": "C管线持久化测试消息",
            "assistant_output": "C管线持久化测试回复",
        })
        assert resp.status_code == 200

        store = unified_client.app.state.context_memory.store
        ui = store.get(
            where={"$and": [{"type": "user_input"}, {"conversation_id": "int-a5-001"}]},
            include=["documents"],
        )
        assert any("C管线持久化测试消息" in (d or "") for d in ui["documents"])
        ao = store.get(
            where={"$and": [{"type": "assistant_output"}, {"conversation_id": "int-a5-001"}]},
            include=["documents"],
        )
        assert any("C管线持久化测试回复" in (d or "") for d in ao["documents"])

    def test_prompt_triggers_activity_log(self, unified_client):
        """G1: Prompt Hook 触发 activity_log 埋点"""
        from memos.config.models import get_memos_home

        unified_client.post("/api/hooks/prompt", json={
            "conversation_id": "int-g1-001",
            "user_input": "触发活动日志测试",
        })

        log_dir = get_memos_home() / "etc"
        log_files = sorted(log_dir.glob("activity_log_*.jsonl"))
        assert len(log_files) >= 1, f"活动日志文件未创建: {list(log_dir.iterdir())}"
        content = log_files[-1].read_text(encoding="utf-8")
        assert "context_injection" in content


class TestPhase3StopHook:
    """Phase 3: Stop Hook + Task 链路"""

    def test_stop_extracts_and_writes_task(self, unified_client):
        """B1: Stop Hook 提取 TASK_EVAL 并写入 task"""
        # 加速降级：设置 llm_caller 为 None 避免 LLM 连接超时
        tq = unified_client.app.state.task_queue
        if tq._llm_caller is not None:
            tq._llm_caller = None

        resp = unified_client.post("/api/hooks/stop", json={
            "last_assistant_message": (
                "工作完成。\n[TASK_EVAL]\n"
                '{"project":"test-b1","goal":"验证task写入","done":["步骤A"],"todo":["步骤B"],"blocked":[]}\n'
                "[/TASK_EVAL]"
            ),
            "conversation_id": "int-b1-001",
            "stop_hook_active": False,
        })
        assert resp.status_code == 200

        # 等待 TaskEvalQueue 异步消费（最多 3 秒）
        store = unified_client.app.state.context_memory.store
        task_found = False
        for _ in range(30):
            time.sleep(0.1)
            results = store.get(
                where={"type": "task"},
                include=["documents", "metadatas"],
            )
            for doc in results.get("documents", []):
                if "test-b1" in (doc or ""):
                    task_found = True
                    break
            if task_found:
                break
        assert task_found, "TASK_EVAL 未在超时内写入 ChromaDB"

    def test_task_chain_overwrite(self, unified_client):
        """C1: TaskEvalQueue 同链覆盖（新 task 覆盖旧 task）"""
        tq = unified_client.app.state.task_queue
        if tq._llm_caller is not None:
            tq._llm_caller = None

        # 第一次 TASK_EVAL
        resp1 = unified_client.post("/api/hooks/stop", json={
            "last_assistant_message": (
                "[TASK_EVAL]\n"
                '{"project":"chain-test","goal":"第一期","done":["A"],"todo":["B"],"blocked":[]}\n'
                "[/TASK_EVAL]"
            ),
            "conversation_id": "int-c1-001",
            "stop_hook_active": False,
        })
        assert resp1.status_code == 200
        time.sleep(1.5)  # 给消费者足够时间处理第一条

        # 第二次 TASK_EVAL（相同 project 名称）
        resp2 = unified_client.post("/api/hooks/stop", json={
            "last_assistant_message": (
                "[TASK_EVAL]\n"
                '{"project":"chain-test","goal":"第二期","done":["A","B"],"todo":["C"],"blocked":[]}\n'
                "[/TASK_EVAL]"
            ),
            "conversation_id": "int-c1-002",
            "stop_hook_active": False,
        })
        assert resp2.status_code == 200
        time.sleep(1.5)  # 给消费者足够时间处理第二条

        # 验证：只有 1 条 type=task + project=chain-test
        store = unified_client.app.state.context_memory.store
        results = store.get(
            where={"$and": [{"type": "task"}, {"project": "chain-test"}]},
            include=["metadatas"],
        )
        assert len(results["ids"]) == 1, f"预期 1 条 task，实际 {len(results['ids'])} 条"
        meta = results["metadatas"][0]
        assert meta["goal"] == "第二期"
        prev = json.loads(meta.get("previous_versions", "[]"))
        assert len(prev) >= 1, "应该有 previous_versions"
        assert prev[0]["goal"] == "第一期"

    def test_cold_start_marker(self, tmp_path):
        """C2: TASK_EVAL 冷启动标记（直接调用 _process_item，绕过异步队列）

        使用随机的 project 名确保每次运行都是全新 task（否则链式更新会跳过标记生成）。
        """
        import os
        import uuid
        uid = uuid.uuid4().hex[:8]
        old_val = os.environ.pop("CLAUDE_PROJECT_DIR", None)
        os.environ["CLAUDE_PROJECT_DIR"] = str(tmp_path)
        try:
            marker = tmp_path / "etc" / ".cold_start_done"
            if marker.exists():
                marker.unlink()

            from memos.server.task_handler import TaskEvalQueue
            from memos.engine.memory import ContextMemory
            coll = os.environ.get("MEMOS_TEST_COLLECTION", "test_suite")
            tq = TaskEvalQueue(memory_instance=ContextMemory(collection_name=coll))
            tq._llm_caller = None  # 确保 LLM 降级路径，快速完成
            tq._process_item({
                "task_eval": {"project": f"cold-start-{uid}", "goal": "冷启动", "done": [], "todo": ["X"], "blocked": []},
                "session_id": f"int-cs-{uid}",
                "project_id": f"test-cold-{uid}",
            })
            assert marker.exists(), f"冷启动标记文件未创建: {marker}"
            content = marker.read_text(encoding="utf-8")
            assert "cold-start" in content or "done" in content
        finally:
            if old_val is not None:
                os.environ["CLAUDE_PROJECT_DIR"] = old_val
            else:
                os.environ.pop("CLAUDE_PROJECT_DIR", None)

    def test_stop_logs_hook_latency(self, unified_client, tmp_path, monkeypatch):
        """G2: Stop Hook 触发 hook_latency 埋点"""
        monkeypatch.setenv("MEMOS_HOME", str(tmp_path))

        unified_client.post("/api/hooks/stop", json={
            "last_assistant_message": "延迟记录测试",
            "conversation_id": "int-g2-001",
            "stop_hook_active": False,
        })

        log_files = list(tmp_path.glob("etc/activity_log_*.jsonl"))
        assert len(log_files) >= 1
        content = log_files[0].read_text(encoding="utf-8")
        assert "hook_latency" in content
        assert "latency_ms" in content


class TestPhase5Briefing:
    """Phase 5: Briefing 全链路"""

    def _headers(self):
        return {"X-Memos-Project-Id": "e2e-test-pid"}

    def test_first_prompt_triggers_fallback_briefing(self, unified_client):
        """D1: 首次 Prompt Hook 触发兜底简报生成"""
        resp = unified_client.post("/api/hooks/prompt", json={
            "conversation_id": "int-d1-001",
            "user_input": "今天的工作",
        }, headers=self._headers())
        assert resp.status_code == 200

        mem = unified_client.app.state.context_memory
        briefings = mem.list_memories(type_filter="briefing", limit=10)
        assert len(briefings) >= 1, "兜底简报未生成"

    def test_briefing_no_duplicate_same_day(self, unified_client):
        """D2: 同一日不重复生成简报"""
        resp1 = unified_client.post("/api/hooks/prompt", json={
            "conversation_id": "int-d2-001",
            "user_input": "第一轮对话",
        }, headers=self._headers())
        assert resp1.status_code == 200
        time.sleep(0.3)

        resp2 = unified_client.post("/api/hooks/prompt", json={
            "conversation_id": "int-d2-002",
            "user_input": "第二轮对话",
        }, headers=self._headers())
        assert resp2.status_code == 200

        mem = unified_client.app.state.context_memory
        results = mem.list_memories(type_filter="briefing", limit=10)
        from datetime import datetime
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        today_briefings = [r for r in results if r.get("metadata", {}).get("briefing_date") == today]
        assert len(today_briefings) <= 1, f"同一日不应有多条简报，当前 {len(today_briefings)} 条"


class TestPhase6Regression:
    """Phase 6: 回归验证"""

    def test_activity_log_api_accessible(self, unified_client):
        """G3: 活动日志 API 可查询"""
        resp = unified_client.get("/api/v2/activity-log?page=1&page_size=10")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data

    def test_old_config_auto_fill(self, tmp_path, monkeypatch):
        """M4: F10 旧 config.json 自动补全新字段"""
        import json
        monkeypatch.setenv("MEMOS_HOME", str(tmp_path))

        # 模拟缺少 v0.6.0 新增字段（behavior_guide/activity_log）的旧配置
        # 但包含 schema 要求的 llm 字段
        old_config = {
            "server": {"host": "127.0.0.1", "port": 8000},
            "llm": {
                "endpoints": [{"name": "test", "api_base": "http://test/v1", "api_key": "", "model": "test", "prompt_templates": {}}],
                "active": "test",
                "temperature": 0.1,
                "max_tokens": 2048,
                "request_timeout": 30,
                "max_retries": 3,
                "retry_base_delay": 1.0,
                "stop": [],
            },
        }
        config_path = tmp_path / "etc" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(old_config), encoding="utf-8")

        from memos.config.loader import MemoConfig
        cfg = MemoConfig.load()

        # behavior_guide 已独立为 etc/behavior_guide.json（F13），不在 MemoConfig 中
        assert cfg.activity_log.retention_days == 30
