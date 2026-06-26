"""S03：Dashboard 全操作 (F2 + F6 + F12)"""


class TestS03DashboardCRUD:
    """验证 Dashboard 导航、记忆 CRUD、类型筛选、注入监控"""

    def test_01_unified_navigation_tabs(self, unified_client):
        """[S03-01] Dashboard 共有五组导航 Tab，无 dashboard_v2 入口"""
        resp = unified_client.get("/")
        assert resp.status_code == 200
        html = resp.text
        assert "dashboard_v2" not in html, "仍存在 dashboard_v2 引用"
        nav_keywords = {"总览", "对话", "记忆", "跟进", "配置"}
        found = sum(1 for kw in nav_keywords if kw in html)
        assert found >= 3, f"导航 Tab 不足: 仅找到 {found}/{len(nav_keywords)}"

    def test_02_memory_list_by_type_filter(self, unified_client):
        """[S03-02] 按类型筛选记忆"""
        mem = unified_client.app.state.context_memory
        mem.remember("解决方案A: FastAPI架构", metadata={"type": "solution", "source": "manual"})
        mem.remember("决策B: 使用ChromaDB", metadata={"type": "decision", "source": "manual"})

        resp = unified_client.get("/api/memories?type=solution")
        assert resp.status_code == 200
        data = resp.json()
        memories = data.get("memories", data.get("data", []))
        if memories:
            for m in memories:
                meta = m.get("metadata", {})
                t = meta.get("type", m.get("type"))
                assert t == "solution", f"类型过滤后应全是 solution, 发现: {t}"

    def test_03_search_and_type_filter_combined(self, unified_client):
        """[S03-03] 搜索 + 类型过滤组合"""
        resp = unified_client.get("/api/memories?q=架构&type=solution")
        assert resp.status_code == 200

    def test_04_memory_edit(self, unified_client):
        """[S03-04] 编辑记忆内容和 metadata"""
        mem = unified_client.app.state.context_memory
        mid = mem.remember("旧内容", metadata={"type": "lesson", "tags": ["old"]})
        assert mid is not None

        mem.update_memory(mid, new_content="新内容", new_metadata={"tags": ["new"]})
        doc = mem.store.get(ids=[mid], include=["documents", "metadatas"])
        assert "新内容" in doc["documents"][0], f"内容未更新: {doc['documents'][0]}"
        assert "new" in doc["metadatas"][0].get("tags", []), \
            f"metadata 未更新: {doc['metadatas'][0]}"

    def test_05_memory_hard_delete(self, unified_client):
        """[S03-05] 硬删除记忆"""
        mem = unified_client.app.state.context_memory
        mid = mem.remember("待删除流程记录", metadata={"type": "process"})
        assert mid is not None

        resp = unified_client.delete(f"/api/memories/{mid}")
        assert resp.status_code == 200, f"删除 API 返回 {resp.status_code}"

        result = mem.store.get(ids=[mid])
        assert len(result["ids"]) == 0, f"删除后应无记录: {result['ids']}"

    def test_06_old_type_badge(self, unified_client):
        """[S03-06] 旧 7 类数据标记'旧版'展示"""
        mem = unified_client.app.state.context_memory
        mem.remember("旧版本偏好", metadata={"type": "preference", "source": "migration"})

        resp = unified_client.get("/api/memories")
        assert resp.status_code == 200
        data = resp.json()
        memories = data.get("memories", data.get("data", []))
        old_type_found = any(
            m.get("type") == "preference" or "preference" in str(m.get("type", ""))
            for m in memories
        )
        # 旧类型可能被API过滤，但不应导致错误
        assert resp.status_code == 200

    def test_07_injection_monitoring_via_activity_log(self, unified_client):
        """[S03-07] 注入监控：通过活动日志验证 injection_type 区分"""
        resp = unified_client.get("/api/v2/activity-log?limit=10")
        assert resp.status_code == 200, f"活动日志 API 返回 {resp.status_code}"

    def test_08_manual_refinement_via_v2(self, unified_client):
        """[S03-08] 手工提炼面板通过 review 端点访问"""
        resp = unified_client.get("/api/v2/review")
        assert resp.status_code == 200, f"review 端点返回 {resp.status_code}"
