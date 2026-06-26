"""测试 F4: 旧 7 类数据迁移工具 (memos migrate types)。"""

import json
import os
import tempfile
import time
from unittest import mock

import pytest

# 与 cli/migrate.py 保持一致
MIGRATED_FROM_KEY = "_migrated_from"
MIGRATED_AT_KEY = "_migrated_at"


@pytest.fixture
def empty_mem():
    """创建一个使用临时 ChromaDB 目录的 ContextMemory 实例。

    自动销毁测试数据。仅使用非 real 标签的测试需要连接 ChromaDB。
    """
    from memos.engine.memory import ContextMemory
    from memos.storage.chroma import ChromaDBPersistentStore

    tmp = tempfile.mkdtemp(prefix="memos-migrate-test-")
    try:
        col_name = f"test_migrate_{int(time.time())}"
        store = ChromaDBPersistentStore(col_name)
        mem = ContextMemory(store=store)
        yield mem, tmp
    finally:
        import shutil

        try:
            # 清理所有测试数据
            all_ids = mem.store.get().get("ids", [])
            if all_ids:
                mem.store.delete(ids=all_ids)
            del mem
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


def _seed_old_types(mem, seed_data: list[tuple[str, str, str]]):
    """向 store 中注入旧类型测试数据。

    seed_data: [(content, type, project_id), ...]
    """
    import uuid

    from memos.config import config

    for content, typ, pid in seed_data:
        mid = uuid.uuid4().hex
        meta = {
            "type": typ,
            "timestamp": time.time(),
            "project_id": pid,
            "status": "active",
        }
        mem.store.add(
            documents=[content],
            embeddings=[[0.0] * config.model.vector_dim],
            metadatas=[meta],
            ids=[mid],
        )


class TestMigrateTypesDryRun:
    """13.1 --dry-run"""

    def test_dry_run_empty(self, empty_mem, capsys):
        """空数据库应正常输出无记录。"""
        mem, tmp_dir = empty_mem
        from memos.cli.migrate import _do_dry_run

        _do_dry_run(mem)
        captured = capsys.readouterr()
        assert "总记忆数: 0" in captured.out
        assert "无需手动确认" in captured.out

    def test_dry_run_with_old_types(self, empty_mem, capsys):
        """含旧类型数据时应正确分类。"""
        mem, tmp_dir = empty_mem
        _seed_old_types(mem, [
            ("Python 3.12 的异步编程", "fact", "proj1"),
            ("使用 FastAPI 作为后端框架", "decision", "proj1"),
            ("偏好 VS Code 进行开发", "preference", "proj1"),
            ("修复了登录页面的 XSS 漏洞", "bug_fix", "proj1"),
            ("优化了数据库查询性能", "code_optimize", "proj1"),
            ("设计了用户权限管理模块", "feature_design", "proj1"),
            ("了解了 Docker Compose 的网络配置", "tech_knowledge", "proj1"),
        ])

        from memos.cli.migrate import _do_dry_run

        _do_dry_run(mem)
        captured = capsys.readouterr()
        assert "总记忆数: 7" in captured.out
        assert "fact: 1 条" in captured.out
        assert "decision: 1 条" in captured.out
        assert "preference: 1 条" in captured.out
        assert "bug_fix: 1 条" in captured.out
        assert "code_optimize: 1 条" in captured.out
        assert "feature_design: 1 条" in captured.out
        assert "tech_knowledge: 1 条" in captured.out
        # 确认映射提示
        assert "自动迁移至: solution" in captured.out
        assert "自动迁移至: lesson" in captured.out
        assert "需手动确认" in captured.out

    def test_dry_run_with_new_types_only(self, empty_mem, capsys):
        """仅新类型时不应出现在扫描结果中。"""
        mem, tmp_dir = empty_mem
        _seed_old_types(mem, [
            ("这是 solution", "solution", "proj1"),
            ("这是 lesson", "lesson", "proj1"),
        ])

        from memos.cli.migrate import _do_dry_run

        _do_dry_run(mem)
        captured = capsys.readouterr()
        assert "总记忆数: 0" in captured.out

    def test_dry_run_knowledge_type(self, empty_mem, capsys):
        """旧类型带 knowledge 后缀的也应正确识别。"""
        mem, tmp_dir = empty_mem
        _seed_old_types(mem, [
            ("旧 tech_knowledge", "tech_knowledge", "proj1"),
        ])

        from memos.cli.migrate import _do_dry_run

        _do_dry_run(mem)
        captured = capsys.readouterr()
        assert "tech_knowledge: 1 条" in captured.out
        assert "需手动确认" in captured.out


class TestMigrateTypesApply:
    """13.2 --apply"""

    def test_apply_empty(self, empty_mem, capsys):
        """空数据库应正常提示。"""
        mem, tmp_dir = empty_mem
        from memos.cli.migrate import _do_apply

        # 模拟用户输入 n
        with mock.patch("builtins.input", return_value="y"):
            _do_apply(mem)
        captured = capsys.readouterr()
        assert "没有旧类型记忆需要迁移" in captured.out or "没有需要自动迁移" in captured.out

    def test_apply_auto_maps_bugfix_and_codeopt(self, empty_mem, capsys):
        """bug_fix→solution, code_optimize→lesson 自动映射。"""
        mem, tmp_dir = empty_mem
        _seed_old_types(mem, [
            ("修复了登录页面 XSS", "bug_fix", "proj1"),
            ("优化了查询性能", "code_optimize", "proj1"),
            ("这是一个决策记录", "decision", "proj1"),
        ])

        from memos.cli.migrate import _do_apply

        with mock.patch("builtins.input", return_value="y"):
            _do_apply(mem)

        captured = capsys.readouterr()
        assert "已迁移 2 条" in captured.out

        # 验证数据
        all_data = mem.store.get(include=["metadatas", "documents"])
        for i, mid in enumerate(all_data["ids"]):
            meta = (all_data["metadatas"] or [])[i] or {}
            if "XSS" in (all_data["documents"] or [])[i]:
                assert meta["type"] == "solution"
                assert meta["_migrated_from"] == "bug_fix"
            elif "查询性能" in (all_data["documents"] or [])[i]:
                assert meta["type"] == "lesson"
                assert meta["_migrated_from"] == "code_optimize"
            elif "决策" in (all_data["documents"] or [])[i]:
                assert meta["type"] == "decision"
                assert "_migrated_from" not in meta

    def test_apply_cancel(self, empty_mem, capsys):
        """取消确认不应修改数据。"""
        mem, tmp_dir = empty_mem
        _seed_old_types(mem, [
            ("bug fix content", "bug_fix", "proj1"),
        ])

        from memos.cli.migrate import _do_apply

        with mock.patch("builtins.input", return_value="n"):
            _do_apply(mem)

        captured = capsys.readouterr()
        assert "已取消" in captured.out

        # 验证未修改
        all_data = mem.store.get(include=["metadatas", "documents"])
        meta = (all_data["metadatas"] or [])[0] or {}
        assert meta["type"] == "bug_fix"


class TestMigrateTypesConfirm:
    """13.3 --confirm"""

    def test_confirm_empty(self, empty_mem, capsys):
        """空数据库应正常提示。"""
        mem, tmp_dir = empty_mem
        from memos.cli.migrate import _do_confirm

        _do_confirm(mem)
        captured = capsys.readouterr()
        assert "没有旧类型记忆需要处理" in captured.out or "没有需要手动确认" in captured.out

    def test_confirm_skip(self, empty_mem, capsys):
        """确认时选跳过不应修改数据。"""
        mem, tmp_dir = empty_mem
        _seed_old_types(mem, [
            ("Python 3.12 异步编程知识", "fact", "proj1"),
        ])

        from memos.cli.migrate import _do_confirm

        with mock.patch("builtins.input", return_value="1"):
            _do_confirm(mem)

        captured = capsys.readouterr()
        assert "跳过 1 条" in captured.out

        # 验证未修改
        all_data = mem.store.get(include=["metadatas", "documents"])
        meta = (all_data["metadatas"] or [])[0] or {}
        assert meta["type"] == "fact"

    def test_confirm_map_solution(self, empty_mem, capsys):
        """确认时映射 fact→solution。"""
        mem, tmp_dir = empty_mem
        _seed_old_types(mem, [
            ("Python 3.12 异步编程知识", "fact", "proj1"),
        ])

        from memos.cli.migrate import _do_confirm

        with mock.patch("builtins.input", return_value="2"):
            _do_confirm(mem)

        captured = capsys.readouterr()
        assert "映射 1 条" in captured.out

        all_data = mem.store.get(include=["metadatas", "documents"])
        meta = (all_data["metadatas"] or [])[0] or {}
        assert meta["type"] == "solution"

    def test_confirm_map_lesson(self, empty_mem, capsys):
        """确认时映射 fact→lesson。"""
        mem, tmp_dir = empty_mem
        _seed_old_types(mem, [
            ("Python 3.12 异步编程知识", "fact", "proj1"),
        ])

        from memos.cli.migrate import _do_confirm

        with mock.patch("builtins.input", return_value="3"):
            _do_confirm(mem)

        captured = capsys.readouterr()
        assert "映射 1 条" in captured.out

        all_data = mem.store.get(include=["metadatas", "documents"])
        meta = (all_data["metadatas"] or [])[0] or {}
        assert meta["type"] == "lesson"

    def test_confirm_delete(self, empty_mem, capsys):
        """确认时选择删除。"""
        mem, tmp_dir = empty_mem
        _seed_old_types(mem, [
            ("feature design content", "feature_design", "proj1"),
        ])

        from memos.cli.migrate import _do_confirm

        with mock.patch("builtins.input", return_value="3"):
            _do_confirm(mem)

        captured = capsys.readouterr()
        assert "删除 1 条" in captured.out

        # 验证已删除
        all_data = mem.store.get(include=["metadatas", "documents"])
        for meta in (all_data["metadatas"] or []):
            if meta:
                assert meta.get("type") != "feature_design"

    def test_confirm_tech_knowledge(self, empty_mem, capsys):
        """tech_knowledge 只有一个选项 (map to lesson)。"""
        mem, tmp_dir = empty_mem
        _seed_old_types(mem, [
            ("Docker 网络配置知识", "tech_knowledge", "proj1"),
        ])

        from memos.cli.migrate import _do_confirm

        with mock.patch("builtins.input", return_value="2"):
            _do_confirm(mem)

        captured = capsys.readouterr()
        assert "映射 1 条" in captured.out

        all_data = mem.store.get(include=["metadatas", "documents"])
        meta = (all_data["metadatas"] or [])[0] or {}
        assert meta["type"] == "lesson"


class TestMigrateTypesMappingFile:
    """13.4 --mapping-file"""

    def test_mapping_file_not_found(self, empty_mem, capsys):
        """文件不存在时退出。"""
        mem, tmp_dir = empty_mem
        from memos.cli.migrate import _do_mapping_file

        with pytest.raises(SystemExit):
            _do_mapping_file(mem, "/nonexistent/file.json")

    def test_mapping_file_execute(self, empty_mem, capsys):
        """JSON 映射文件正确执行映射和删除。"""
        mem, tmp_dir = empty_mem
        _seed_old_types(mem, [
            ("事实 A", "fact", "proj1"),
            ("事实 B", "fact", "proj1"),
            ("pref X", "preference", "proj1"),
        ])

        # 获取 ID
        all_data = mem.store.get(include=["metadatas", "documents"])
        fact_a_id = all_data["ids"][0]
        fact_b_id = all_data["ids"][1]
        pref_id = all_data["ids"][2]

        # 写入映射文件
        mapping = {
            fact_a_id: "solution",
            fact_b_id: "lesson",
            pref_id: "_delete_",
        }
        mapping_path = os.path.join(tmp_dir, "mapping.json")
        with open(mapping_path, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False)

        from memos.cli.migrate import _do_mapping_file

        _do_mapping_file(mem, mapping_path)

        # 验证
        all_data = mem.store.get(include=["metadatas", "documents"])
        found_fact_a = found_fact_b = False
        for i, mid in enumerate(all_data["ids"]):
            meta = (all_data["metadatas"] or [])[i] or {}
            if mid == fact_a_id:
                assert meta["type"] == "solution"
                found_fact_a = True
            elif mid == fact_b_id:
                assert meta["type"] == "lesson"
                found_fact_b = True
        # preference 应已被删除
        for mid in all_data["ids"]:
            assert mid != pref_id
        assert found_fact_a
        assert found_fact_b

    def test_mapping_file_invalid_type(self, empty_mem, capsys):
        """无效类型应跳过并报告。"""
        mem, tmp_dir = empty_mem
        _seed_old_types(mem, [
            ("事实 A", "fact", "proj1"),
        ])
        fact_id = mem.store.get()["ids"][0]

        mapping = {fact_id: "invalid_type_xyz"}
        mapping_path = os.path.join(tmp_dir, "mapping_bad.json")
        with open(mapping_path, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False)

        from memos.cli.migrate import _do_mapping_file

        _do_mapping_file(mem, mapping_path)
        captured = capsys.readouterr()
        assert "无效类型" in captured.out


class TestMigrateTypesExportBackup:
    """13.5 --export-backup"""

    def test_export_backup_empty(self, empty_mem, capsys):
        """空数据库应提示无可导出记录。"""
        mem, tmp_dir = empty_mem
        backup_path = os.path.join(tmp_dir, "backup.json")
        from memos.cli.migrate import _do_export_backup

        _do_export_backup(mem, backup_path)
        captured = capsys.readouterr()
        assert "没有旧类型记忆需要备份" in captured.out

    def test_export_backup_content(self, empty_mem, capsys):
        """导出文件应包含所有旧类型记忆。"""
        mem, tmp_dir = empty_mem
        _seed_old_types(mem, [
            ("bug fix", "bug_fix", "proj1"),
            ("fact", "fact", "proj1"),
            ("preference", "preference", "proj1"),
        ])

        backup_path = os.path.join(tmp_dir, "backup.json")
        from memos.cli.migrate import _do_export_backup

        _do_export_backup(mem, backup_path)

        # 验证文件内容
        with open(backup_path, encoding="utf-8") as f:
            backup = json.load(f)
        assert backup["format_version"] == "1.0"
        assert backup["total"] == 3
        assert len(backup["records"]) == 3
        types_in_backup = {r["metadata"]["type"] for r in backup["records"]}
        assert types_in_backup == {"bug_fix", "fact", "preference"}

    def test_export_backup_only_old_types(self, empty_mem, capsys):
        """新类型不应出现在备份中。"""
        mem, tmp_dir = empty_mem
        _seed_old_types(mem, [
            ("old fact", "fact", "proj1"),
            ("new solution", "solution", "proj1"),
        ])

        backup_path = os.path.join(tmp_dir, "backup.json")
        from memos.cli.migrate import _do_export_backup

        _do_export_backup(mem, backup_path)

        with open(backup_path, encoding="utf-8") as f:
            backup = json.load(f)
        assert backup["total"] == 1
        assert backup["records"][0]["metadata"]["type"] == "fact"


class TestMigrateTypesCleanup:
    """13.6 --cleanup"""

    def test_cleanup_empty(self, empty_mem, capsys):
        """空数据库应正常提示。"""
        mem, tmp_dir = empty_mem
        from memos.cli.migrate import _do_cleanup

        with mock.patch("builtins.input", return_value="y"):
            _do_cleanup(mem)
        captured = capsys.readouterr()
        assert "没有旧类型记忆需要清理" in captured.out or "没有需要清理" in captured.out

    def test_cleanup_deletes_preference(self, empty_mem, capsys):
        """preference 应被删除。"""
        mem, tmp_dir = empty_mem
        _seed_old_types(mem, [
            ("pref content", "preference", "proj1"),
            ("decision content", "decision", "proj1"),
        ])

        from memos.cli.migrate import _do_cleanup

        with mock.patch("builtins.input", return_value="y"):
            _do_cleanup(mem)

        captured = capsys.readouterr()
        assert "已删除" in captured.out

        all_data = mem.store.get(include=["metadatas"])
        remaining_types = {(m or {}).get("type") for m in (all_data["metadatas"] or [])}
        assert "preference" not in remaining_types
        assert "decision" in remaining_types

    def test_cleanup_unmapped_manual_types(self, empty_mem, capsys):
        """未映射的 fact/feature_design/tech_knowledge 应被删除。"""
        mem, tmp_dir = empty_mem
        _seed_old_types(mem, [
            ("unmapped fact", "fact", "proj1"),
            ("decision", "decision", "proj1"),
        ])

        from memos.cli.migrate import _do_cleanup

        with mock.patch("builtins.input", return_value="y"):
            _do_cleanup(mem)

        captured = capsys.readouterr()
        assert "已删除" in captured.out

        all_data = mem.store.get(include=["metadatas"])
        remaining_types = {(m or {}).get("type") for m in (all_data["metadatas"] or [])}
        assert "fact" not in remaining_types
        assert "decision" in remaining_types

    def test_cleanup_auto_creates_backup(self, empty_mem, capsys):
        """清理前自动创建备份。"""
        mem, tmp_dir = empty_mem
        _seed_old_types(mem, [
            ("pref", "preference", "proj1"),
        ])

        from memos.cli.migrate import _do_cleanup

        with (
            mock.patch("builtins.input", return_value="y"),
        ):
            _do_cleanup(mem)

        captured = capsys.readouterr()
        assert "自动备份" in captured.out or "已有备份" in captured.out
        assert "已删除" in captured.out


class TestMigrateTypesVerify:
    """13.7 --verify"""

    def test_verify_empty(self, empty_mem, capsys):
        """空数据库应正常提示。"""
        mem, tmp_dir = empty_mem
        from memos.cli.migrate import _do_verify

        _do_verify(mem)
        captured = capsys.readouterr()
        assert "没有旧类型记忆需要验证" in captured.out

    def test_verify_unmigrated(self, empty_mem, capsys):
        """未迁移的记录应显示未迁移状态。"""
        mem, tmp_dir = empty_mem
        _seed_old_types(mem, [
            ("fact content", "fact", "proj1"),
        ])

        from memos.cli.migrate import _do_verify

        _do_verify(mem)
        captured = capsys.readouterr()
        assert "未迁移" in captured.out

    def test_verify_migrated_ok(self, empty_mem, capsys):
        """已迁移且映射正确的记录应验证通过。"""
        mem, tmp_dir = empty_mem
        import uuid

        from memos.config import config

        # 插入一条带迁移标记的记录
        mid = uuid.uuid4().hex
        meta = {
            "type": "solution",
            "_migrated_from": "bug_fix",
            "_migrated_at": time.time(),
            "timestamp": time.time(),
            "project_id": "proj1",
            "status": "active",
        }
        mem.store.add(
            documents=["fixed a bug"],
            embeddings=[[0.0] * config.model.vector_dim],
            metadatas=[meta],
            ids=[mid],
        )

        from memos.cli.migrate import _do_verify

        _do_verify(mem)
        captured = capsys.readouterr()
        assert "已迁移标记: 1 条" in captured.out


class TestMigrateTypesRollback:
    """13.8 --rollback"""

    def test_rollback_file_not_found(self, empty_mem, capsys):
        """备份文件不存在时退出。"""
        mem, tmp_dir = empty_mem
        from memos.cli.migrate import _do_rollback

        with pytest.raises(SystemExit):
            _do_rollback(mem, "/nonexistent/backup.json")

    def test_rollback_restores(self, empty_mem, capsys):
        """从备份恢复应还原元数据和文档。"""
        mem, tmp_dir = empty_mem
        import uuid

        from memos.config import config

        # 1. 插入一条旧类型记录
        mid = uuid.uuid4().hex
        original_meta = {
            "type": "bug_fix",
            "timestamp": time.time(),
            "project_id": "proj1",
            "status": "active",
        }
        mem.store.add(
            documents=["original bug fix content"],
            embeddings=[[0.0] * config.model.vector_dim],
            metadatas=[original_meta],
            ids=[mid],
        )

        # 2. 在迁移前创建备份（备份的是原始旧类型数据）
        backup_path = os.path.join(tmp_dir, "rollback_backup.json")
        from memos.cli.migrate import _do_export_backup

        _do_export_backup(mem, backup_path)

        # 3. 修改类型 (模拟迁移)
        mod_meta = dict(original_meta)
        mod_meta["type"] = "solution"
        mod_meta["_migrated_from"] = "bug_fix"
        mod_meta["_migrated_at"] = time.time()
        mem.store.update(ids=[mid], metadatas=[mod_meta])

        # 4. 回滚到备份状态
        from memos.cli.migrate import _do_rollback

        with mock.patch("builtins.input", return_value="y"):
            _do_rollback(mem, backup_path)

        captured = capsys.readouterr()
        assert "已恢复 1 条" in captured.out

        # 5. 验证已恢复到原始状态
        # ChromaDB update() 无法删除 metadata 字段，只能置空
        result = mem.get_memory(mid)
        assert result is not None
        meta = result["metadata"]
        assert meta["type"] == "bug_fix"
        assert not meta.get(MIGRATED_FROM_KEY, ""), f"_migrated_from should be empty, got: {meta.get('_migrated_from')}"
        assert not meta.get(MIGRATED_AT_KEY, 0), f"_migrated_at should be 0, got: {meta.get('_migrated_at')}"
        assert result["document"] == "original bug fix content"

    def test_rollback_cleans_migration_state(self, empty_mem, capsys):
        """回滚前清理迁移状态标记。"""
        mem, tmp_dir = empty_mem
        import uuid

        from memos.config import config

        # 插入两条记录: 一条有迁移标记, 一条没有
        mid1 = uuid.uuid4().hex
        mid2 = uuid.uuid4().hex
        now = time.time()
        mem.store.add(
            documents=["migrated"],
            embeddings=[[0.0] * config.model.vector_dim],
            metadatas=[{
                "type": "solution", "_migrated_from": "bug_fix",
                "_migrated_at": now, "timestamp": now,
                "project_id": "p1", "status": "active",
            }],
            ids=[mid1],
        )
        mem.store.add(
            documents=["clean"],
            embeddings=[[0.0] * config.model.vector_dim],
            metadatas=[{
                "type": "decision", "timestamp": now,
                "project_id": "p1", "status": "active",
            }],
            ids=[mid2],
        )

        # 创建备份
        backup_path = os.path.join(tmp_dir, "state_clean_backup.json")
        from memos.cli.migrate import _do_export_backup, _do_rollback

        _do_export_backup(mem, backup_path)

        with mock.patch("builtins.input", return_value="y"):
            _do_rollback(mem, backup_path)

        captured = capsys.readouterr()
        assert "已清理" in captured.out


class TestCategorizeHelper:
    """测试辅助分类函数。"""

    def test_categorize_by_type(self):
        from memos.cli.migrate import _categorize_by_type

        items = [
            {"metadata": {"type": "fact"}},
            {"metadata": {"type": "fact"}},
            {"metadata": {"type": "decision"}},
            {"metadata": {"type": "unknown_type"}},
        ]
        cats = _categorize_by_type(items)
        assert len(cats["fact"]) == 2
        assert len(cats["decision"]) == 1
        assert len(cats["unknown_type"]) == 1


class TestCliDispatchIntegration:
    """测试 CLI dispatch 正确路由到 types 子命令。"""

    def test_migrate_types_command_routes(self, monkeypatch, capsys):
        """memos migrate types --help-types 应显示帮助。"""
        import argparse

        monkeypatch.setattr(argparse._sys, "argv", ["memos", "migrate", "types", "--help-types"])

        from memos.cli import main

        try:
            main()
        except SystemExit:
            pass
        captured = capsys.readouterr()
        assert "旧 7 类型" in captured.out
        assert "dry-run" in captured.out
        assert "auto-mapping" in captured.out or "自动" in captured.out
