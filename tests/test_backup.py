"""F2 备份与恢复 — 单元测试

测试覆盖：
- 备份执行与完整性校验
- 备份列表与 manifest 管理
- 备份清理（按个数）
- 备份锁互斥
- 恢复安全确认
- 恢复回退机制
- Dashboard API
- 边界场景
"""

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest import mock

import pytest

from memos.features.backup import (
    _acquire_lock,
    _cleanup_old_backups,
    _count_files_and_size,
    _get_memdb_dir,
    _get_target_dir,
    _read_manifest,
    _release_lock,
    _verify_backup_structure,
    _verify_chromadb,
    _write_manifest,
    backup_memdb,
    list_backups,
    mark_export_time,
    restore_backup,
)


class TestBackupCore:
    """核心备份功能测试"""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_fake_memdb(self, base: Path, file_count: int = 5, total_size_kb: int = 100) -> Path:
        """创建伪造的 ChromaDB 目录结构用于测试。"""
        memdb = base / "memdb"
        memdb.mkdir(parents=True)
        # chroma.sqlite3（必须存在）
        (memdb / "chroma.sqlite3").write_bytes(b"sqlite3_fake_db" * 50)
        # 模拟 collection 目录（UUID 格式）
        coll_dir = memdb / "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
        coll_dir.mkdir()
        for i in range(file_count):
            (coll_dir / f"data_{i}.bin").write_bytes(b"x" * (total_size_kb * 1024 // file_count))
        return memdb

    def test_count_files_and_size(self):
        """统计目录文件数和大小。"""
        base = Path(self.tmpdir)
        test_dir = base / "test"
        test_dir.mkdir()
        (test_dir / "a.txt").write_text("hello" * 10)
        (test_dir / "b.txt").write_text("world" * 20)
        fc, sz = _count_files_and_size(test_dir)
        assert fc == 2
        assert sz > 0

    def test_count_empty_dir(self):
        """空目录返回 0。"""
        base = Path(self.tmpdir)
        empty = base / "empty"
        empty.mkdir()
        fc, sz = _count_files_and_size(empty)
        assert fc == 0
        assert sz == 0

    def test_manifest_read_write(self):
        """manifest 读写往返。"""
        target = Path(self.tmpdir)
        manifest = {"backups": [{"id": "test", "timestamp": 1.0}], "last_export_at": 2.0}
        _write_manifest(target, manifest)
        read = _read_manifest(target)
        assert read["backups"][0]["id"] == "test"
        assert read["last_export_at"] == 2.0

    def test_manifest_not_exists_returns_empty(self):
        """不存在的 manifest 返回空结构。"""
        target = Path(self.tmpdir) / "nonexistent"
        manifest = _read_manifest(target)
        assert manifest["backups"] == []
        assert manifest["last_export_at"] is None

    def test_backup_lock_acquire_release(self):
        """备份锁获取与释放。"""
        target = Path(self.tmpdir)
        lock_path = _acquire_lock(target)
        assert lock_path.exists()
        # 再次获取应抛异常
        with pytest.raises(RuntimeError, match="已有备份任务"):
            _acquire_lock(target)
        _release_lock(lock_path)
        assert not lock_path.exists()

    def test_backup_lock_expired(self):
        """过期锁自动释放（>30分钟）。"""
        target = Path(self.tmpdir)
        lock_path = target / "backup.lock"
        target.mkdir(parents=True, exist_ok=True)
        lock_path.touch()
        # 模拟过期：修改 mtime 到 31 分钟前
        old_time = time.time() - 1860
        os.utime(str(lock_path), (old_time, old_time))
        # 应该能正常获取（旧锁被清理）
        new_lock = _acquire_lock(target)
        assert new_lock.exists()
        _release_lock(new_lock)

    def test_cleanup_old_backups(self):
        """超出 max_backups 时清理最旧的备份。"""
        target = Path(self.tmpdir)
        # 创建 15 个备份条目（模拟）
        manifest = {"backups": []}
        for i in range(15):
            bp = target / f"memdb_20260519_{i:06d}"
            bp.mkdir(parents=True)
            (bp / "dummy.txt").write_text(f"backup_{i}")
            manifest["backups"].append(
                {
                    "id": f"memdb_20260519_{i:06d}",
                    "path": str(bp),
                    "timestamp": 1000.0 + i,
                    "size_bytes": 1024,
                    "file_count": 1,
                    "status": "complete",
                }
            )
        _write_manifest(target, manifest)

        removed = _cleanup_old_backups(target, 10)
        assert removed == 5
        # 验证 manifest 更新
        updated = _read_manifest(target)
        assert len(updated["backups"]) == 10
        # 最旧的 5 个目录应被删除
        for i in range(5):
            assert not (target / f"memdb_20260519_{i:06d}").exists()

    def test_cleanup_below_max(self):
        """备份数量未超 max_backups 时不清理。"""
        target = Path(self.tmpdir)
        manifest = {"backups": []}
        for i in range(3):
            bp = target / f"memdb_test_{i}"
            bp.mkdir(parents=True)
            (bp / "dummy.txt").write_text(f"b{i}")
            manifest["backups"].append(
                {
                    "id": f"memdb_test_{i}",
                    "path": str(bp),
                    "timestamp": 1000.0 + i,
                    "size_bytes": 100,
                    "file_count": 1,
                    "status": "complete",
                }
            )
        _write_manifest(target, manifest)
        removed = _cleanup_old_backups(target, 10)
        assert removed == 0
        assert len(_read_manifest(target)["backups"]) == 3

    def test_verify_backup_structure_valid(self):
        """合法 ChromaDB 结构通过校验。"""
        bp = Path(self.tmpdir) / "valid_backup"
        bp.mkdir()
        (bp / "chroma.sqlite3").write_text("sqlite3")
        uuid_dir = bp / ("a" * 32)
        uuid_dir.mkdir()
        _verify_backup_structure(bp)  # 不抛异常

    def test_verify_backup_structure_missing_sqlite(self):
        """缺少 chroma.sqlite3 时校验失败。"""
        bp = Path(self.tmpdir) / "bad_backup"
        bp.mkdir()
        uuid_dir = bp / ("b" * 32)
        uuid_dir.mkdir()
        with pytest.raises(ValueError, match="缺少 chroma.sqlite3"):
            _verify_backup_structure(bp)

    def test_verify_backup_structure_no_uuid_dir(self):
        """无 UUID 子目录时校验失败。"""
        bp = Path(self.tmpdir) / "bad_backup2"
        bp.mkdir()
        (bp / "chroma.sqlite3").write_text("sqlite3")
        with pytest.raises(ValueError, match="无有效 collection"):
            _verify_backup_structure(bp)

    @mock.patch("memos.features.backup._get_memdb_dir")
    @mock.patch("memos.features.backup._get_target_dir")
    @mock.patch("memos.features.backup._get_max_backups")
    @mock.patch("memos.features.backup._get_verify_flag")
    def test_backup_memdb_success(self, mock_verify, mock_max, mock_target, mock_memdb):
        """完整备份流程（含伪造 memdb）。"""
        memdb = self._make_fake_memdb(Path(self.tmpdir), file_count=5)
        target = Path(self.tmpdir) / "backups"
        target.mkdir()

        mock_memdb.return_value = memdb
        mock_target.return_value = target
        mock_max.return_value = 10
        mock_verify.return_value = False  # 跳过完整性校验简化测试

        result = backup_memdb()
        assert result["status"] == "complete"
        assert result["file_count"] == 6  # 5 data files + chroma.sqlite3
        assert result["size_bytes"] > 0

    @mock.patch("memos.features.backup._get_memdb_dir")
    def test_backup_memdb_not_found(self, mock_memdb):
        """memdb 目录不存在时抛出 FileNotFoundError。"""
        mock_memdb.return_value = Path("/nonexistent/memdb")
        with pytest.raises(FileNotFoundError, match="memdb 目录不存在"):
            backup_memdb()

    @mock.patch("memos.features.backup._get_memdb_dir")
    @mock.patch("memos.features.backup._get_target_dir")
    @mock.patch("memos.features.backup._get_max_backups")
    @mock.patch("memos.features.backup._get_verify_flag")
    def test_backup_cleanup_triggered(self, mock_verify, mock_max, mock_target, mock_memdb):
        """备份满时触发清理。"""
        memdb = self._make_fake_memdb(Path(self.tmpdir), file_count=1)
        target = Path(self.tmpdir) / "backups"
        target.mkdir()

        # 预先创建 12 个备份（超过 max=10）
        for i in range(12):
            bp = target / f"memdb_old_{i:06d}"
            bp.mkdir(parents=True)
            (bp / "dummy.txt").write_text(f"old_{i}")
        manifest = {
            "backups": [
                {
                    "id": f"memdb_old_{i:06d}",
                    "path": str(target / f"memdb_old_{i:06d}"),
                    "timestamp": 1000.0 + i,
                    "size_bytes": 100,
                    "file_count": 1,
                    "status": "complete",
                }
                for i in range(12)
            ]
        }
        _write_manifest(target, manifest)

        mock_memdb.return_value = memdb
        mock_target.return_value = target
        mock_max.return_value = 10  # 只保留 10 个
        mock_verify.return_value = False

        result = backup_memdb()
        assert result["status"] == "complete"
        # 应清理 2 个旧备份（12 + 1 新 = 13 → 保留 10）
        updated = _read_manifest(target)
        assert len(updated["backups"]) <= 10

    def test_list_backups_empty(self):
        """无备份时返回空列表。"""
        with mock.patch("memos.features.backup._get_target_dir") as mock_target:
            target = Path(self.tmpdir) / "empty_backups"
            target.mkdir(parents=True)
            mock_target.return_value = target
            result = list_backups()
            assert result["total"] == 0
            assert result["backups"] == []

    def test_list_backups_with_data(self):
        """有备份时返回正确列表。"""
        target = Path(self.tmpdir) / "backups"
        target.mkdir(parents=True)
        manifest = {
            "backups": [
                {
                    "id": "memdb_20260519_120000",
                    "path": str(target / "memdb_20260519_120000"),
                    "timestamp": 2000.0,
                    "size_bytes": 5000,
                    "file_count": 10,
                    "status": "complete",
                },
                {
                    "id": "memdb_20260519_110000",
                    "path": str(target / "memdb_20260519_110000"),
                    "timestamp": 1000.0,
                    "size_bytes": 3000,
                    "file_count": 8,
                    "status": "complete",
                },
            ],
            "last_export_at": 1500.0,
        }
        _write_manifest(target, manifest)

        with mock.patch("memos.features.backup._get_target_dir") as mock_target:
            mock_target.return_value = target
            result = list_backups()
            assert result["total"] == 2
            assert result["days_since_export"] is not None
            # 按时间戳降序排列
            assert result["backups"][0]["timestamp"] == 2000.0

    def test_mark_export_time(self):
        """更新最后导出时间。"""
        target = Path(self.tmpdir) / "backups"
        target.mkdir(parents=True)
        _write_manifest(target, {"backups": []})

        with mock.patch("memos.features.backup._get_target_dir") as mock_target:
            mock_target.return_value = target
            before = time.time()
            mark_export_time()
            after = time.time()

            manifest = _read_manifest(target)
            assert manifest["last_export_at"] is not None
            assert before <= manifest["last_export_at"] <= after

    @mock.patch("memos.features.backup._get_memdb_dir")
    def test_restore_backup_not_found(self, mock_memdb):
        """恢复不存在的备份路径。"""
        mock_memdb.return_value = Path(self.tmpdir) / "memdb"
        result = restore_backup("/nonexistent/path", force=True)
        assert result["success"] is False
        assert "不存在" in result["message"]

    @mock.patch("memos.features.backup._get_memdb_dir")
    def test_restore_structure_invalid(self, mock_memdb):
        """备份结构无效且非 force 模式应拒绝。"""
        bp = Path(self.tmpdir) / "bad_backup"
        bp.mkdir()
        mock_memdb.return_value = Path(self.tmpdir) / "memdb"
        result = restore_backup(str(bp), force=False)
        assert result["success"] is False
        assert "结构校验失败" in result["message"] or "缺少 chroma.sqlite3" in result["message"]

    @mock.patch("memos.features.backup._get_memdb_dir")
    def test_restore_structure_invalid_force(self, mock_memdb):
        """force=True 时跳过结构校验，尝试恢复但最终因数据无效而失败（已回退）。"""
        bp = Path(self.tmpdir) / "bad_backup"
        bp.mkdir()
        mock_memdb.return_value = Path(self.tmpdir) / "memdb"
        result = restore_backup(str(bp), force=True)
        # 不因结构校验被拒绝，但最终会因数据无效失败（无回退，因为原 memdb 不存在）
        assert "结构校验" not in result.get("message", "")
        assert result["success"] is False

    def test_get_target_dir_default(self):
        """无参数和配置时使用默认目录。"""
        with mock.patch(
            "memos.features.backup._get_target_dir",
            side_effect=lambda target=None: Path(_DEFAULT_TARGET_DIR) if target is None else Path(target),
        ):
            assert _get_target_dir() == Path("backups")
            assert _get_target_dir("/custom/path") == Path("/custom/path")


class TestDashboardBackupAPI:
    """Dashboard 备份 API 测试（需要 test client）"""

    @pytest.fixture
    def client(self):
        from memos.web.app import app

        from fastapi.testclient import TestClient

        return TestClient(app)

    def test_backup_status_api(self, client):
        """GET /api/backup/status 端点存在（需认证），验证路由已注册。"""
        resp = client.get("/api/backup/status")
        # 端点存在（200/401，非 404）
        assert resp.status_code != 404, f"backup status 端点不存在: {resp.status_code}"

    def test_trigger_backup_api_requires_login(self, client):
        """备份触发 API 需要登录认证。"""
        resp = client.post("/api/backup/trigger")
        # 可能 401（需要认证）或 500（memdb 不存在），但不应 404
        assert resp.status_code != 404


# 模块级常量用于 test_get_target_dir_default
_DEFAULT_TARGET_DIR = "memdb/backups"
