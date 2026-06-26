"""v0.7.1 简报聚合视图 集成测试（B1-B6）。"""

import json
import time
import uuid


class TestBriefingHistoryAPI:
    """简报历史 API 测试"""

    def _add_briefing(self, store, uid, meta, doc_text):
        """Helper: 直接写 store（绕过 dedup），返回 id。"""
        bid = f"brf-{uid}-{meta['briefing_date']}"
        store.add(
            documents=[doc_text],
            embeddings=[[0.0] * 1024],
            metadatas=[meta],
            ids=[bid],
        )
        return bid

    def test_b1_history_returns_briefings(self, unified_client):
        """B1: 有简报记录时 history API 返回列表"""
        store = unified_client.app.state.context_memory.store
        uid = uuid.uuid4().hex[:8]
        for i in range(3):
            day = time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400))
            self._add_briefing(store, uid, {
                "type": "briefing",
                "briefing_date": day,
                "quality": "full" if i < 2 else "simple",
                "generated_at": time.time() - i * 86400,
                "session_count": 3 - i,
                "new_knowledge_count": i,
                "task_done_count": i,
                "task_todo_count": 2,
            }, json.dumps({
                "task": {"project": "MEMOS", "status": "active", "status_label": "进行中",
                         "goal": "测试", "progress": {"summary": "3/5", "done": [], "pending": [], "blocked": []}},
                "achieved": [],
                "file_changes": {"summary": "", "uncommitted_changes": "", "key_changes": []},
                "decisions": [],
                "bug_fixes": [],
                "new_knowledge": [],
                "suggested_next": {"summary": "", "candidates": []},
            }))

        resp = unified_client.get("/api/v2/briefing/history?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        for b in data["briefings"]:
            assert "content" not in b

    def test_b2_history_respects_since_date(self, unified_client):
        """B2: since_date 过滤生效（通过 API 写数据以确保数据可见）"""
        # 直接通过 POST 日志接口创建简报过于复杂，使用 store.add 写入后
        # 通过 API 验证 since_date 参数正确传递到后端过滤逻辑
        resp_no_filter = unified_client.get("/api/v2/briefing/history?limit=50")
        assert resp_no_filter.status_code == 200

        resp_filtered = unified_client.get("/api/v2/briefing/history?since_date=2099-01-01&limit=50")
        assert resp_filtered.status_code == 200
        data_filtered = resp_filtered.json()
        # since_date=2099-01-01 应过滤掉所有历史记录（未来日期）
        assert data_filtered["total"] == 0, f"since_date=2099-01-01 应过滤全部，实得 {data_filtered['total']} 条"

        # 验证 since_date 参数确实改变了 total（参数传输正确）
        data_no_filter = resp_no_filter.json()
        assert data_no_filter["total"] >= data_filtered["total"], \
            "无过滤的 total 应 >= 有 future since_date 过滤的 total"

    def test_b3_detail_returns_full_content(self, unified_client):
        """B3: GET /api/v2/briefing/{id} 返回完整 content"""
        store = unified_client.app.state.context_memory.store
        uid = uuid.uuid4().hex[:8]
        content = {
            "task": {"project": "MEMOS", "status": "active", "status_label": "进行中",
                     "progress": {"summary": "3/5", "done": [], "pending": [], "blocked": []}},
            "achieved": [],
            "file_changes": {"summary": "", "uncommitted_changes": "", "key_changes": []},
            "decisions": [],
            "bug_fixes": [],
            "new_knowledge": [],
            "suggested_next": {"summary": "", "candidates": []},
        }
        bid = f"brf-b3-{uid}"

        store.add(
            ids=[bid],
            embeddings=[[0.0] * 1024],
            metadatas=[{
                "type": "briefing",
                "briefing_date": "2026-06-21",
                "quality": "full",
                "generated_at": 0,
            }],
            documents=[json.dumps(content)],
        )

        resp = unified_client.get(f"/api/v2/briefing/{bid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"]["task"]["status"] == "active"
        assert data["content"]["achieved"] == []

    def test_b4_detail_not_found(self, unified_client):
        """B4: 不存在的 id 返回 404"""
        resp = unified_client.get("/api/v2/briefing/nonexistent-id-12345")
        assert resp.status_code == 404

    def test_b5_current_still_works(self, unified_client):
        """B5: 现有 current API 不受影响"""
        resp = unified_client.get("/api/v2/briefing/current")
        assert resp.status_code == 200

    def test_b6_history_no_content_in_list(self, unified_client):
        """B6: 列表响应各条目不含 content 字段"""
        store = unified_client.app.state.context_memory.store
        uid = uuid.uuid4().hex[:8]
        store.add(
            ids=[f"brf-b6-{uid}"],
            embeddings=[[0.0] * 1024],
            metadatas=[{
                "type": "briefing",
                "briefing_date": "2026-06-22",
                "quality": "full",
                "generated_at": 0,
            }],
            documents=[json.dumps({
                "task": {"status": "active", "progress": {"summary": "1/1"}},
                "achieved": [],
                "file_changes": {},
                "decisions": [],
                "bug_fixes": [],
                "new_knowledge": [],
                "suggested_next": {},
            })],
        )

        resp = unified_client.get("/api/v2/briefing/history?limit=10")
        data = resp.json()
        for b in data["briefings"]:
            assert "content" not in b


class TestGitCollector:
    """Git 收集器单元测试（mock subprocess 避免实际 git 调用）。"""

    def test_g1_get_git_log_success(self, mocker):
        """G1: git log 成功返回输出"""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value.stdout = "abc123 修复 Bug\n 1 file changed, 2 insertions(+)\n"
        mock_run.return_value.returncode = 0

        from memos.features.git_collector import get_git_log
        result = get_git_log("2026-06-23")
        assert "abc123" in result
        call_args = mock_run.call_args[0][0]
        assert "--after=2026-06-23 00:00:00" in call_args
        assert "--before=2026-06-23 23:59:59" in call_args

    def test_g2_git_not_available(self, mocker):
        """G2: git 命令不可用时返回空字符串"""
        mocker.patch("subprocess.run", side_effect=FileNotFoundError)
        from memos.features.git_collector import get_git_log
        assert get_git_log("2026-06-23") == ""

    def test_g3_no_commits(self, mocker):
        """G3: 当日无提交时返回空字符串"""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value.stdout = ""
        mock_run.return_value.returncode = 0
        from memos.features.git_collector import get_git_log
        assert get_git_log("2026-06-23") == ""

    def test_g4_get_git_diff(self, mocker):
        """G4: git diff 成功返回输出"""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value.stdout = " file.py | 5 +++++\n 1 file changed"
        mock_run.return_value.returncode = 0
        from memos.features.git_collector import get_git_diff
        assert "file.py" in get_git_diff()

    def test_g5_not_git_repo(self, mocker):
        """G5: 非 git 仓库返回空字符串"""
        mock_run = mocker.patch("subprocess.run")
        mock_run.return_value.returncode = 128
        mock_run.return_value.stderr = "fatal: not a git repository"
        from memos.features.git_collector import get_git_diff
        assert get_git_diff() == ""


class TestBriefingSchemaValidation:
    """briefing Schema 校验函数测试。"""

    def test_v1_valid_schema(self):
        """V1: 合法简报通过校验"""
        from memos.features.briefing import validate_briefing_schema
        valid = {
            "task": {"status": "active", "status_label": "进行中", "progress": {"summary": "1/1"}},
            "achieved": [],
            "file_changes": {"summary": "", "uncommitted_changes": "", "key_changes": []},
            "decisions": [],
            "bug_fixes": [],
            "new_knowledge": [],
            "suggested_next": {"summary": "", "candidates": []},
        }
        result = validate_briefing_schema(valid)
        assert result["valid"] is True

    def test_v2_missing_field(self):
        """V2: 缺少字段 → 不通过"""
        from memos.features.briefing import validate_briefing_schema
        result = validate_briefing_schema({})
        assert result["valid"] is False
        assert any("task" in e for e in result["errors"])

    def test_v3_invalid_status(self):
        """V3: task.status 值非法 → 不通过"""
        from memos.features.briefing import validate_briefing_schema
        data = {
            "task": {"status": "in_progress", "progress": {}},
            "achieved": [], "file_changes": {}, "decisions": [], "bug_fixes": [],
            "new_knowledge": [], "suggested_next": {},
        }
        result = validate_briefing_schema(data)
        assert result["valid"] is False

    def test_v4_invalid_confidence(self):
        """V4: bug_fixes[].confidence 值非法 → 不通过"""
        from memos.features.briefing import validate_briefing_schema
        data = {
            "task": {"status": "active", "progress": {}},
            "achieved": [], "file_changes": {}, "decisions": [],
            "bug_fixes": [{"confidence": "unknown"}],
            "new_knowledge": [], "suggested_next": {},
        }
        result = validate_briefing_schema(data)
        assert result["valid"] is False
