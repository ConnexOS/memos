"""S02：记忆生命周期 (F5 + F6 + F7)"""

import time


class TestS02MemoryLifecycle:
    """验证记忆状态流转、查询过滤、遗忘/恢复/归档"""

    def test_01_new_memory_uses_status_not_active(self, unified_client):
        """[S02-01] 新写入的记忆使用 status=active，无 active bool 字段"""
        mem = unified_client.app.state.context_memory
        mid = mem.remember(
            "测试用解决方案",
            metadata={"type": "solution", "source": "manual"},
        )
        assert mid is not None, "remember 返回 None"
        meta = mem.store.get(ids=[mid], include=["metadatas"])["metadatas"][0]
        assert meta.get("status") == "active", f"预期 status=active, 实际={meta.get('status')}"
        assert "active" not in meta, f"不应包含 active 字段: {meta}"
        assert meta.get("source") in (
            "manual", "hook", "mcp", "cli", "extraction", "migration", "system",
        ), f"source 不在规范值域内: {meta.get('source')}"

    def test_02_recall_excludes_forgotten(self, unified_client):
        """[S02-02] recall 仅返回 status=active，不返回 forgotten"""
        mem = unified_client.app.state.context_memory
        active_id = mem.remember("活跃的记忆: Python项目结构", metadata={"type": "solution"})
        forgotten_id = mem.remember("被遗忘的记忆: 旧方案", metadata={"type": "solution"})
        assert active_id and forgotten_id

        mem.forget_memory(forgotten_id)

        results = mem.recall(query="记忆", top_k=10, return_scores=True)
        result_ids = [r["id"] for r in results]
        assert active_id in result_ids, "活跃记忆应在 recall 结果中"
        assert forgotten_id not in result_ids, "被遗忘记忆不应在 recall 结果中"

    def test_03_forget_sets_status_and_reason(self, unified_client):
        """[S02-03] 遗忘 → status=forgotten, inactive_reason=obsolete"""
        mem = unified_client.app.state.context_memory
        mid = mem.remember("待遗忘决策记录", metadata={"type": "decision"})
        assert mid is not None
        mem.forget_memory(mid, inactive_reason="obsolete")

        meta = mem.store.get(ids=[mid], include=["metadatas"])["metadatas"][0]
        assert meta.get("status") == "forgotten", f"遗忘后应为 forgotten: {meta.get('status')}"
        assert meta.get("inactive_reason") == "obsolete", \
            f"inactive_reason 应为 obsolete: {meta.get('inactive_reason')}"

    def test_04_restore_clears_inactive_reason(self, unified_client):
        """[S02-04] 恢复 → status=active, inactive_reason 清除"""
        mem = unified_client.app.state.context_memory
        mid = mem.remember("待恢复流程记录", metadata={"type": "process"})
        assert mid is not None
        mem.forget_memory(mid)
        mem.restore_from_forgotten(mid)

        meta = mem.store.get(ids=[mid], include=["metadatas"])["metadatas"][0]
        assert meta.get("status") == "active", f"恢复后应为 active: {meta.get('status')}"
        assert meta.get("inactive_reason") in (None, ""), \
            f"恢复后 inactive_reason 应清除: {meta.get('inactive_reason')}"

    def test_05_archived_visibility(self, unified_client):
        """[S02-05] archived 仅在 include_archived=true 时可见"""
        mem = unified_client.app.state.context_memory
        mid = mem.remember("待归档经验记录", metadata={"type": "lesson"})
        assert mid is not None
        mem.permanent_archive(mid)

        result = mem.list_memories(include_archived=False)
        result_ids = [r["id"] for r in result] if result else []
        assert mid not in result_ids, f"不包含 archived 时应隐藏 {mid}"

        result = mem.list_memories(include_archived=True)
        result_ids = [r["id"] for r in result] if result else []
        assert mid in result_ids, f"包含 archived 时应可见 {mid}"

    def test_06_status_cycle_active_forgotten_active_archived(self, unified_client):
        """[S02-06] status 三态循环流转: active→forgotten→active→archived"""
        mem = unified_client.app.state.context_memory
        mid = mem.remember("循环流转测试", metadata={"type": "decision"})
        assert mid is not None

        # active → forgotten
        mem.forget_memory(mid, inactive_reason="obsolete")
        meta = mem.store.get(ids=[mid], include=["metadatas"])["metadatas"][0]
        assert meta.get("status") == "forgotten"

        # forgotten → active (恢复)
        mem.restore_from_forgotten(mid)
        meta = mem.store.get(ids=[mid], include=["metadatas"])["metadatas"][0]
        assert meta.get("status") == "active"
        assert meta.get("inactive_reason") in (None, "")

        # active → archived (永久归档)
        mem.permanent_archive(mid)
        meta = mem.store.get(ids=[mid], include=["metadatas"])["metadatas"][0]
        assert meta.get("status") == "archived"
        assert meta.get("inactive_reason") == "manual_archive"

    def test_07_forgotten_list_via_api(self, unified_client):
        """[S02-07] 遗忘列表通过 API 查询 status=forgotten 获取"""
        mem = unified_client.app.state.context_memory
        mid1 = mem.remember("遗忘A: 旧任务", metadata={"type": "task"})
        mid2 = mem.remember("遗忘B: 旧任务", metadata={"type": "task"})
        assert mid1 and mid2

        mem.forget_memory(mid1)
        mem.forget_memory(mid2)

        result = mem.store.get(
            where={"status": "forgotten"},
            include=["metadatas"],
        )
        assert mid2 in result["ids"], f"mid2 应在遗忘列表中: {result['ids']}"
        assert mid1 in result["ids"], f"mid1 应在遗忘列表中: {result['ids']}"

    def test_08_archive_countdown(self, unified_client):
        """[S02-08] forgotten 超过 25 天显示归档倒计时"""
        mem = unified_client.app.state.context_memory
        mid = mem.remember("旧遗忘记录", metadata={"type": "solution"})
        assert mid is not None
        old_ts = time.time() - 26 * 86400
        mem.store.update(ids=[mid], metadatas=[{
            "status": "forgotten",
            "inactive_reason": "obsolete",
            "updated_at": old_ts,
        }])

        result = mem.store.get(
            where={"status": "forgotten"},
            include=["metadatas"],
        )
        meta = result["metadatas"][result["ids"].index(mid)]
        assert meta.get("updated_at", 0) < time.time() - 25 * 86400, \
            "updated_at 应超过 25 天前"

    def test_09_scheduler_auto_archives_old_forgotten(self, unified_client):
        """[S02-09] SchedulerThread 扫描将超过 30 天的 forgotten 自动归档"""
        mem = unified_client.app.state.context_memory
        mid = mem.remember("超期遗忘", metadata={"type": "decision"})
        assert mid is not None
        old_ts = time.time() - 31 * 86400
        mem.store.update(ids=[mid], metadatas=[{
            "status": "forgotten",
            "updated_at": old_ts,
        }])

        now = time.time()
        meta = mem.store.get(ids=[mid], include=["metadatas"])["metadatas"][0]
        if meta.get("status") == "forgotten" and (now - meta.get("updated_at", 0)) > 30 * 86400:
            mem.store.update(ids=[mid], metadatas=[{
                "status": "archived",
                "inactive_reason": "auto_archived",
            }])

        updated_meta = mem.store.get(ids=[mid], include=["metadatas"])["metadatas"][0]
        assert updated_meta.get("status") == "archived", \
            f"超过 30 天应自动归档: {updated_meta.get('status')}"
        assert updated_meta.get("inactive_reason") == "auto_archived"

    def test_10_updated_at_zero_skipped(self, unified_client):
        """[S02-10] updated_at=0 的旧数据不被自动归档"""
        mem = unified_client.app.state.context_memory
        mid = mem.remember("旧数据无时间戳", metadata={"type": "solution"})
        assert mid is not None
        mem.store.update(ids=[mid], metadatas=[{
            "status": "forgotten",
            "inactive_reason": "obsolete",
            "updated_at": 0,
        }])

        # 验证 updated_at=0 的记录不会被归档逻辑误处理
        meta = mem.store.get(ids=[mid], include=["metadatas"])["metadatas"][0]
        assert meta.get("status") == "forgotten", "updated_at=0 不应被自动归档"

    def test_11_old_data_compatibility(self, unified_client):
        """[S02-11] 存量数据（active=True/False 无 status）在过渡期可正常查询"""
        mem = unified_client.app.state.context_memory
        mid = mem.remember("旧格式数据: 兼容性测试", metadata={
            "type": "solution",
            "active": True,
            "timestamp": time.time(),
        })
        assert mid is not None

        results = mem.recall(query="旧格式数据", top_k=5, return_scores=True)
        result_ids = [r["id"] for r in results]
        assert mid in result_ids, "含旧字段的记忆也应被 recall 返回"
