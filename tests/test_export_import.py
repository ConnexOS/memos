"""测试 F1 - 记忆导入导出标准格式（CLI + Dashboard API）"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from memos.engine.memory import ContextMemory

from .conftest import clean_collection

COLLECTION = "test_export_import"


def _memories_only(items):
    """过滤掉 _header 项，仅返回记忆记录"""
    return [i for i in items if "_header" not in i]


class TestExportMemories:
    """ContextMemory.export_memories()"""

    @classmethod
    def setup_class(cls):
        cls.mem = ContextMemory(collection_name=COLLECTION)

    def setup_method(self):
        clean_collection(self.mem)

    def test_export_all(self):
        """全量导出"""
        for i in range(5):
            self.mem.remember(f"记忆{i + 1}", {"type": "fact"})
        items = _memories_only(self.mem.export_memories())
        assert len(items) == 5
        for item in items:
            assert "id" in item
            assert "content" in item
            assert "metadata" in item
            # 默认不含 embedding
            assert item["embedding"] is None

    def test_export_with_type_filter(self):
        """按类型过滤导出"""
        self.mem.remember("事实1", {"type": "fact"})
        self.mem.remember("决策1", {"type": "decision"})
        self.mem.remember("偏好1", {"type": "preference"})
        items = _memories_only(self.mem.export_memories(type_filter=["fact", "decision"]))
        types = {item["metadata"]["type"] for item in items}
        assert types == {"fact", "decision"}

    def test_export_with_embeddings(self):
        """导出含向量"""
        self.mem.remember("测试", {"type": "fact"})
        items = _memories_only(self.mem.export_memories(include_embeddings=True))
        assert len(items) == 1
        assert items[0]["embedding"] is not None
        assert len(items[0]["embedding"]) > 0

    def test_export_empty(self):
        """空数据集导出"""
        items = list(self.mem.export_memories())
        assert items == []


class TestImportMemories:
    """ContextMemory.import_memories()"""

    @classmethod
    def setup_class(cls):
        cls.mem = ContextMemory(collection_name=COLLECTION)
        cls.pid = "test-project"

    def setup_method(self):
        clean_collection(self.mem)

    def test_import_basic(self):
        """基本导入"""
        lines = [
            json.dumps({"content": "测试事实", "metadata": {"type": "fact", "project_id": self.pid}}),
        ]
        result = self.mem.import_memories(lines, target_project_id=self.pid)
        assert result["imported"] == 1
        assert result["failed"] == 0

    def test_import_multiple(self):
        """导入多条（duplicate 策略避免短句去重误判）"""
        lines = [
            json.dumps(
                {
                    "content": f"这是一条关于项目配置的记忆记录第{i}号",
                    "metadata": {"type": "fact", "project_id": self.pid},
                }
            )
            for i in range(10)
        ]
        result = self.mem.import_memories(lines, strategy="duplicate")
        assert result["imported"] == 10

    def test_import_skip_duplicate(self):
        """skip 策略跳过重复"""
        line = json.dumps({"content": "唯一记忆", "metadata": {"type": "fact", "project_id": self.pid}})
        r1 = self.mem.import_memories([line])
        assert r1["imported"] == 1
        r2 = self.mem.import_memories([line], strategy="skip")
        assert r2["skipped"] == 1
        assert r2["imported"] == 0

    def test_import_duplicate_strategy(self):
        """duplicate 策略强制新增"""
        line = json.dumps({"content": "重复记忆", "metadata": {"type": "fact", "project_id": self.pid}})
        r1 = self.mem.import_memories([line])
        r2 = self.mem.import_memories([line], strategy="duplicate")
        assert r2["imported"] == 1

    def test_import_missing_content(self):
        """缺少 content 字段"""
        lines = [json.dumps({"metadata": {"type": "fact"}})]
        result = self.mem.import_memories(lines)
        assert result["failed"] == 1

    def test_import_invalid_type(self):
        """非法 type 值"""
        lines = [json.dumps({"content": "测试", "metadata": {"type": "invalid_type"}})]
        result = self.mem.import_memories(lines)
        assert result["failed"] == 1

    def test_import_invalid_json(self):
        """非法 JSON"""
        lines = ["这不是 JSON"]
        result = self.mem.import_memories(lines)
        assert result["failed"] == 1

    def test_import_overwrite_strategy(self):
        """overwrite 策略覆盖旧值"""
        line = json.dumps({"content": "旧内容", "metadata": {"type": "fact", "project_id": self.pid}})
        self.mem.import_memories([line])
        line_new = json.dumps({"content": "新内容", "metadata": {"type": "fact", "project_id": self.pid}})
        result = self.mem.import_memories([line_new], strategy="overwrite")
        assert result["imported"] == 1
        # 验证只有一条记忆且内容为新
        items = self.mem.list_memories(project_id=self.pid)
        assert len(items) == 1
        assert items[0]["document"] == "新内容"


class TestCLIExportImport:
    """memos export / import CLI"""

    def test_export_to_file(self, monkeypatch, capsys):
        tmp = tempfile.mkdtemp(prefix="memos-exp-test-")
        try:
            home_path = Path(tmp) / ".memos"
            monkeypatch.setenv("MEMOS_HOME", str(home_path))
            (home_path / "etc").mkdir(parents=True, exist_ok=True)

            # 创建测试记忆
            mem = ContextMemory(collection_name="test_cli_export")
            clean_collection(mem)
            for i in range(5):
                mem.remember(f"记忆{i + 1}", {"type": "fact"})

            with patch("memos.engine.memory.ContextMemory", return_value=mem):
                from memos.cli.dispatch import cmd_export
                import argparse

                output_file = str(Path(tmp) / "export.jsonl")
                args = argparse.Namespace(
                    format="jsonl",
                    output=output_file,
                    project_id=None,
                    type=None,
                    include_embeddings=False,
                )
                cmd_export(args)

            # 验证文件（首行为格式头部 # 注释）
            with open(output_file, encoding="utf-8") as f:
                lines = f.readlines()
            memory_lines = [l for l in lines if not l.startswith("# ")]
            assert len(memory_lines) == 5
            for line in memory_lines:
                item = json.loads(line)
                assert "content" in item

            captured = capsys.readouterr()
            assert "导出完成" in captured.err
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
            clean_collection(mem)

    def test_export_with_type_filter(self, monkeypatch):
        tmp = tempfile.mkdtemp(prefix="memos-exp-test-")
        try:
            home_path = Path(tmp) / ".memos"
            monkeypatch.setenv("MEMOS_HOME", str(home_path))
            (home_path / "etc").mkdir(parents=True, exist_ok=True)

            mem = ContextMemory(collection_name="test_cli_export2")
            clean_collection(mem)
            mem.remember("事实", {"type": "fact"})
            mem.remember("决策", {"type": "decision"})
            mem.remember("偏好", {"type": "preference"})

            with patch("memos.engine.memory.ContextMemory", return_value=mem):
                from memos.cli.dispatch import cmd_export
                import argparse

                output_file = str(Path(tmp) / "typed.jsonl")
                args = argparse.Namespace(
                    format="jsonl",
                    output=output_file,
                    project_id=None,
                    type=["fact", "decision"],
                    include_embeddings=False,
                )
                cmd_export(args)

            with open(output_file, encoding="utf-8") as f:
                lines = f.readlines()
            memory_lines = [l for l in lines if not l.startswith("# ")]
            types = {json.loads(l)["metadata"]["type"] for l in memory_lines}
            assert types == {"fact", "decision"}
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
            clean_collection(mem)

    def test_import_from_file(self, monkeypatch):
        tmp = tempfile.mkdtemp(prefix="memos-imp-test-")
        try:
            home_path = Path(tmp) / ".memos"
            monkeypatch.setenv("MEMOS_HOME", str(home_path))
            (home_path / "etc").mkdir(parents=True, exist_ok=True)

            # 准备输入文件（用区分度高的长句避免短句去重误判）
            import_file = str(Path(tmp) / "input.jsonl")
            with open(import_file, "w", encoding="utf-8") as f:
                for i in range(3):
                    json.dump(
                        {"content": f"这是第{i + 1}条导入的测试记忆数据记录内容", "metadata": {"type": "fact"}},
                        f,
                        ensure_ascii=False,
                    )
                    f.write("\n")

            mem = ContextMemory(collection_name="test_cli_import")
            clean_collection(mem)

            with patch("memos.engine.memory.ContextMemory", return_value=mem):
                from memos.cli.dispatch import cmd_import
                import argparse

                args = argparse.Namespace(file=import_file, project_id=None, strategy="duplicate")
                cmd_import(args)

            items = mem.list_memories()
            assert len(items) == 3
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
            clean_collection(mem)


class TestDashboardExportImport:
    """Dashboard 导出/导入 API"""

    @pytest.fixture
    def client(self, monkeypatch):
        import sys

        mock_instance = MagicMock()
        mock_instance.export_memories.return_value = (x for x in [])
        mock_instance.import_memories.return_value = {"imported": 0, "skipped": 0, "failed": 0, "errors": []}
        monkeypatch.setattr(sys.modules["memos.web.app"], "ContextMemory", lambda *a, **kw: mock_instance)
        monkeypatch.setattr(sys.modules["memos.web.app"], "verify_session_token", lambda *a, **kw: {"token_hash": "test", "exp": 9999999999})

        from memos.web.app import app

        with TestClient(app, cookies={"memos_session": "test"}) as c:
            c.app.state.mem = mock_instance
            yield c, mock_instance

    def test_export_api_returns_ok(self, client):
        c, mem = client
        mem.export_memories.return_value = iter([{"id": "1", "content": "测试", "metadata": {"type": "fact"}}])
        resp = c.get("/api/memories/export")
        assert resp.status_code == 200

    def test_import_api_rejects_non_jsonl(self, client):
        c, mem = client
        resp = c.post("/api/memories/import", files={"file": ("test.txt", b"hello")})
        assert resp.status_code == 400

    def test_import_api_returns_result(self, client):
        c, mem = client
        mem.import_memories.return_value = {"imported": 2, "skipped": 1, "failed": 0, "errors": []}
        resp = c.post(
            "/api/memories/import", files={"file": ("data.jsonl", b'{"content":"test","metadata":{"type":"fact"}}\n')}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 2
