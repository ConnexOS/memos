"""v0.7.1 Task 管理架构改造 集成测试（T1-T10）。

TDD 流程：先写测试 → 验证失败 → 实现代码 → 验证通过。

P1 测试绕过异步队列，直接调用 _process_item() 避免 LLM 阻塞。
"""

import json
import os
import time
import uuid


class TestP1StorageLayer:
    """P1: 存储层改造 — _process_item pending 时序保存（T1-T4）

    直接调用 _process_item() 绕过异步队列和 LLM 调用。
    """

    def _make_queue(self, unified_client):
        """创建 TaskEvalQueue 实例（禁用 LLM，使用测试 collection）。"""
        from memos.server.task_handler import TaskEvalQueue
        from memos.engine.memory import ContextMemory

        coll = os.environ.get("MEMOS_TEST_COLLECTION", "test_suite")
        tq = TaskEvalQueue(memory_instance=ContextMemory(collection_name=coll))
        tq._llm_caller = None  # 禁用 LLM，使用降级路径
        return tq

    def test_t1_no_active_creates_active(self, unified_client):
        """T1: TASK_EVAL 到达，无 active 记录 → 创建 status=active 记录"""
        uid = uuid.uuid4().hex[:8]
        tq = self._make_queue(unified_client)
        tq._process_item({
            "task_eval": {"project": f"t1-{uid}", "goal": "T1测试", "done": [], "todo": ["X"], "blocked": []},
            "session_id": f"t1-{uid}",
            "project_id": f"t1-pid-{uid}",
        })

        store = unified_client.app.state.context_memory.store
        results = store.get(
            where={"$and": [{"type": "task"}, {"project": f"t1-{uid}"}]},
            include=["metadatas"],
        )
        assert len(results["ids"]) >= 1, "应有至少 1 条 task"
        meta = results["metadatas"][0]
        assert meta.get("status") == "active", f"新建记录应为 active，实际 {meta.get('status')}"

    def test_t2_active_exists_creates_pending(self, unified_client):
        """T2: TASK_EVAL 到达，有 active 记录 → 创建 pending 记录，active document 不变"""
        uid = uuid.uuid4().hex[:8]
        tq = self._make_queue(unified_client)
        project = f"t2-{uid}"

        # 第一条：创建 active
        tq._process_item({
            "task_eval": {"project": project, "goal": "第一期", "done": [], "todo": ["A"], "blocked": []},
            "session_id": f"{project}-1",
            "project_id": f"{project}-pid",
        })

        store = unified_client.app.state.context_memory.store
        results = store.get(
            where={"$and": [{"type": "task"}, {"project": project}]},
            include=["metadatas", "documents"],
        )
        assert len(results["ids"]) == 1, f"第一步应有 1 条 active，实际 {len(results['ids'])}"
        active_doc = results["documents"][0]

        # 第二条：相同 project → 应创建 pending，而非覆盖
        tq._process_item({
            "task_eval": {"project": project, "goal": "第二期", "done": ["A", "B"], "todo": ["C"], "blocked": []},
            "session_id": f"{project}-2",
            "project_id": f"{project}-pid",
        })

        results2 = store.get(
            where={"$and": [{"type": "task"}, {"project": project}]},
            include=["metadatas", "documents"],
        )
        assert len(results2["ids"]) == 2, f"预期 2 条（1 active + 1 pending），实际 {len(results2['ids'])}"

        active_meta = [m for m in results2["metadatas"] if m.get("status") == "active"]
        pending_meta = [m for m in results2["metadatas"] if m.get("status") == "pending"]
        assert len(active_meta) == 1, "应有 1 条 active"
        assert len(pending_meta) == 1, "应有 1 条 pending"

        # active 的 document 不应变化（第一条的内容）
        active_idx = results2["metadatas"].index(active_meta[0])
        assert results2["documents"][active_idx] == active_doc, "active 的 document 不应被修改"

    def test_t3_consecutive_evals(self, unified_client):
        """T3: 连续 5 次 TASK_EVAL → 1 条 active + 4 条 pending"""
        uid = uuid.uuid4().hex[:8]
        tq = self._make_queue(unified_client)
        project = f"t3-{uid}"

        for i in range(5):
            tq._process_item({
                "task_eval": {"project": project, "goal": f"第{i+1}期", "done": [], "todo": ["X"], "blocked": []},
                "session_id": f"{project}-{i}",
                "project_id": f"{project}-pid",
            })

        store = unified_client.app.state.context_memory.store
        results = store.get(
            where={"$and": [{"type": "task"}, {"project": project}]},
            include=["metadatas"],
        )
        assert len(results["ids"]) == 5, f"预期 5 条，实际 {len(results['ids'])}"
        statuses = [m.get("status") for m in results["metadatas"]]
        assert statuses.count("active") == 1, f"应有 1 条 active，实际 {statuses.count('active')}"
        assert statuses.count("pending") == 4, f"应有 4 条 pending，实际 {statuses.count('pending')}"

    def test_t4_no_previous_versions(self, unified_client):
        """T4: previous_versions 不再追加，新记录无此字段"""
        uid = uuid.uuid4().hex[:8]
        tq = self._make_queue(unified_client)
        project = f"t4-{uid}"

        for i in range(2):
            tq._process_item({
                "task_eval": {"project": project, "goal": f"第{i+1}期", "done": [], "todo": ["X"], "blocked": []},
                "session_id": f"{project}-{i}",
                "project_id": f"{project}-pid",
            })

        store = unified_client.app.state.context_memory.store
        results = store.get(
            where={"$and": [{"type": "task"}, {"project": project}]},
            include=["metadatas"],
        )
        assert len(results["ids"]) >= 2, "应有至少 2 条记录"
        for meta in results["metadatas"]:
            assert "previous_versions" not in meta, f"记录不应含 previous_versions 字段"


class TestP2InjectionLayer:
    """P2: 注入层微调 — _inject_active_task where 过滤（T5-T6）"""

    def _create_task(self, mem, uid, status, goal, suffix=""):
        """Helper: 创建 test task，返回 id（remember() 返回裸 ID 字符串）。"""
        return mem.remember(
            json.dumps({"goal": goal}),
            metadata={
                "type": "task",
                "status": status,
                "project_id": f"{uid}-pid",
                "project": uid,
                "goal": goal,
            },
        )

    def test_t5_inject_only_active(self, unified_client):
        """T5: 项目有 active + pending，注入只返回 active"""
        from memos.server.hook_handler import _inject_active_task

        uid = uuid.uuid4().hex[:8]
        mem = unified_client.app.state.context_memory

        # 创建一条 active
        self._create_task(mem, uid, "active", "Active Task")
        # 创建一条 pending
        self._create_task(mem, uid, "pending", "Pending Task")

        result = _inject_active_task(mem, f"{uid}-pid")
        assert "[当前任务]" in result, "应注入 active task"
        assert "Active Task" in result, "应包含 active task 的目标"

    def test_t6_pending_only_no_inject(self, unified_client):
        """T6: 项目有 pending 但无 active，注入返回空"""
        from memos.server.hook_handler import _inject_active_task

        uid = uuid.uuid4().hex[:8]
        mem = unified_client.app.state.context_memory

        # 只创建 pending（不创建 active）
        self._create_task(mem, uid, "pending", "Only Pending")

        result = _inject_active_task(mem, f"{uid}-pid")
        assert result == "", "pending-only 项目应返回空注入"

        result = _inject_active_task(mem, f"t5-pid-{uid}")
        assert "[当前任务]" in result, "应注入 active task"
        assert "Active Task" in result, "应包含 active task 的目标"

    def test_t6_pending_only_no_inject(self, unified_client):
        """T6: 项目有 pending 但无 active，注入返回空"""
        from memos.server.hook_handler import _inject_active_task

        uid = uuid.uuid4().hex[:8]
        mem = unified_client.app.state.context_memory

        # 只创建 pending（不创建 active）
        mem.remember('{"project":"t6-' + uid + '"}', metadata={
            "type": "task",
            "status": "pending",
            "project_id": f"t6-pid-{uid}",
            "project": f"t6-{uid}",
            "goal": "Only Pending",
        })

        result = _inject_active_task(mem, f"t6-pid-{uid}")
        assert result == "", "pending-only 项目应返回空注入"


class TestP3ApiLayer:
    """P3: API 层扩展 — activate 端点 + pending 分组（T7-T10）"""

    def _create_task(self, mem, uid, status, goal):
        return mem.remember(
            json.dumps({"goal": goal}),
            metadata={
                "type": "task",
                "status": status,
                "project_id": uid,
                "project": uid,
                "goal": goal,
            },
        )

    def test_t7_activate_pending(self, unified_client):
        """T7: 激活 pending 记录 → 原 active→completed，目标→active"""
        uid = f"t7-{uuid.uuid4().hex[:8]}"
        mem = unified_client.app.state.context_memory

        active_id = self._create_task(mem, uid, "active", "Original Active")
        pending_id = self._create_task(mem, uid, "pending", "Pending to Activate")

        time.sleep(0.5)

        # 激活 pending
        resp = unified_client.post(f"/api/v2/tasks/{pending_id}/activate?project_id={uid}")
        assert resp.status_code == 200, f"激活失败: {resp.text}"
        data = resp.json()
        assert data["status"] == "active"

        # 验证原 active→completed
        active_item = mem.get_memory(active_id)
        assert active_item["metadata"]["status"] == "completed", "原 active 应为 completed"

        # 验证 pending→active
        pending_item = mem.get_memory(pending_id)
        assert pending_item["metadata"]["status"] == "active", "目标 pending 应为 active"

    def test_t8_activate_completed_reopen(self, unified_client):
        """T8: 激活已完成记录 → reopen 语义，目标→active"""
        uid = f"t8-{uuid.uuid4().hex[:8]}"
        mem = unified_client.app.state.context_memory

        completed_id = self._create_task(mem, uid, "completed", "Reopen me")
        self._create_task(mem, uid, "active", "Current Active")

        # 激活已完成
        resp = unified_client.post(f"/api/v2/tasks/{completed_id}/activate?project_id={uid}")
        assert resp.status_code == 200, f"重新激活失败: {resp.text}"

        completed_item = mem.get_memory(completed_id)
        assert completed_item["metadata"]["status"] == "active", "原 completed 应变为 active"

    def test_t9_activate_nonexistent_404(self, unified_client):
        """T9: 激活不存在的记录 → 404"""
        resp = unified_client.post("/api/v2/tasks/nonexistent-id-12345/activate")
        assert resp.status_code == 404, f"应返回 404，实际 {resp.status_code}"

    def test_t10_list_tasks_includes_pending(self, unified_client):
        """T10: list_tasks 返回 pending 分组统计"""
        uid = f"t10-{uuid.uuid4().hex[:8]}"
        mem = unified_client.app.state.context_memory

        # 创建 active + pending + completed
        for s in ("active", "pending", "pending", "completed"):
            self._create_task(mem, uid, s, f"{s} task")

        resp = unified_client.get(f"/api/v2/tasks?project_id={uid}")
        assert resp.status_code == 200
        data = resp.json()
        counts = data.get("counts", {})
        assert "pending" in counts, "counts 应包含 pending 字段"
        assert counts["pending"] == 2, f"pending 计数应为 2，实际 {counts['pending']}"
        assert counts["active"] == 1
        assert counts["completed"] == 1
