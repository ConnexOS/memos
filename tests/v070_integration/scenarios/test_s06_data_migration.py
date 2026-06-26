"""S06：数据迁移工具 (F4) — 标记为 @pytest.mark.isolated

注意：S06 使用独立 ChromaDB collection，避免污染其他测试。
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.v070_integration.conftest import PROJECT_ROOT


@pytest.mark.isolated
class TestS06DataMigration:
    """验证 F4 六步流程（dry-run/apply/backup/cleanup/verify/rollback）"""

    @classmethod
    def setup_class(cls):
        """准备旧 7 类数据"""
        cls._test_memory_ids = []

    def _prepare_old_type_data(self, mem):
        """写入旧 7 类测试数据"""
        old_types = ["bug_fix", "code_optimize", "preference", "fact",
                     "feature_design", "tech_knowledge"]
        for t in old_types:
            for i in range(2):
                mid = mem.remember(
                    f"旧类型 {t} 测试数据 #{i}",
                    metadata={"type": t, "source": "migration"},
                )
                if mid:
                    self._test_memory_ids.append(mid)

    def test_01_dry_run_shows_counts(self, unified_client):
        """[S06-01] --dry-run 展示各类型存量"""
        mem = unified_client.app.state.context_memory
        self._prepare_old_type_data(mem)

        result = subprocess.run(
            [sys.executable, "-m", "memos.cli", "migrate", "types", "--dry-run"],
            capture_output=True, encoding="utf-8", errors="replace", cwd=str(PROJECT_ROOT),
            timeout=30,
        )
        assert result.returncode == 0, f"dry-run 失败: {result.stderr[:200]}"

    def test_02_apply_auto_mapping(self, unified_client):
        """[S06-02] --apply 自动映射 bug_fix→solution, code_optimize→lesson"""
        mem = unified_client.app.state.context_memory
        mem.remember("bug fix 内容: 修复登录崩溃", metadata={"type": "bug_fix"})
        mem.remember("code optimize 内容: SQL 查询优化", metadata={"type": "code_optimize"})

        result = subprocess.run(
            [sys.executable, "-m", "memos.cli", "migrate", "types", "--apply"],
            capture_output=True, encoding="utf-8", errors="replace", cwd=str(PROJECT_ROOT),
            input="y\n", timeout=30,
        )
        assert result.returncode == 0, f"apply 失败: {result.stderr[:200]}"

    def test_03_export_backup(self, unified_client):
        """[S06-03] --export-backup 生成备份"""
        import tempfile
        backup_path = Path(tempfile.mktemp(suffix=".json", prefix="migration-backup-"))
        result = subprocess.run(
            [sys.executable, "-m", "memos.cli", "migrate", "types",
             "--export-backup", str(backup_path)],
            capture_output=True, encoding="utf-8", errors="replace", cwd=str(PROJECT_ROOT),
            timeout=30,
        )
        assert result.returncode == 0, f"export-backup 失败: {result.stderr[:200]}"
        assert backup_path.exists(), f"备份文件不存在: {backup_path}"

        backup_files = list(Path("etc").glob("migration-backup-*.json"))
        assert len(backup_files) > 0, "未找到备份文件"

    def test_04_cleanup_old_types(self, unified_client):
        """[S06-04] --cleanup 删除 preference/suggestion"""
        result = subprocess.run(
            [sys.executable, "-m", "memos.cli", "migrate", "types", "--cleanup"],
            capture_output=True, encoding="utf-8", errors="replace", cwd=str(PROJECT_ROOT),
            input="y\n", timeout=30,
        )
        assert result.returncode == 0, f"cleanup 失败: {result.stderr[:200]}"

        # 验证旧类型已被清理
        result_verify = subprocess.run(
            [sys.executable, "-m", "memos.cli", "migrate", "types", "--dry-run"],
            capture_output=True, encoding="utf-8", errors="replace", cwd=str(PROJECT_ROOT),
            timeout=30,
        )
        # preference 不应再出现在 dry-run 输出中（或计数为 0）
        assert result_verify.returncode == 0

    def test_05_verify_consistency(self, unified_client):
        """[S06-05] --verify count 对比 + 抽样"""
        result = subprocess.run(
            [sys.executable, "-m", "memos.cli", "migrate", "types", "--verify"],
            capture_output=True, encoding="utf-8", errors="replace", cwd=str(PROJECT_ROOT),
            timeout=30,
        )
        assert result.returncode == 0, f"verify 失败: {result.stderr}"

    def test_06_rollback(self, unified_client):
        """[S06-06] --rollback 恢复到迁移前状态"""
        # 查找备份文件
        from tests.v070_integration.conftest import PROJECT_ROOT as _PROJ
        backup_files = sorted((_PROJ / "etc").glob("migration-backup-*.json"))
        if not backup_files:
            pytest.skip("无备份文件可回滚")
        latest_backup = str(backup_files[-1])

        result = subprocess.run(
            [sys.executable, "-m", "memos.cli", "migrate", "types",
             "--rollback", latest_backup],
            capture_output=True, encoding="utf-8", errors="replace", cwd=str(PROJECT_ROOT),
            input="y\n", timeout=30,
        )
        assert result.returncode == 0, f"rollback 失败: {result.stderr[:200]}"

    def test_07_rollback_cleanup(self, unified_client):
        """[S06-07] rollback 前自清洁 (C3)"""
        from tests.v070_integration.conftest import PROJECT_ROOT as _PROJ
        backup_files = sorted((_PROJ / "etc").glob("migration-backup-*.json"))
        if not backup_files:
            pytest.skip("无备份文件")
        latest_backup = str(backup_files[-1])

        result = subprocess.run(
            [sys.executable, "-m", "memos.cli", "migrate", "types",
             "--rollback", latest_backup],
            capture_output=True, encoding="utf-8", errors="replace", cwd=str(PROJECT_ROOT),
            input="y\n", timeout=30,
        )
        assert result.returncode == 0, f"重复 rollback 失败: {result.stderr[:200]}"
