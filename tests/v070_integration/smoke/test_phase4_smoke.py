"""Phase 4 烟雾测试 — 收尾层 (F4) ~3min"""

import subprocess
import sys

from tests.v070_integration.conftest import PROJECT_ROOT


class TestPhase4Smoke:
    """验证 Phase 4 (F4) 基本可用"""

    def test_f4_dry_run_executes(self):
        """[Phase4-F4] migrate types --dry-run 可执行"""
        result = subprocess.run(
            [sys.executable, "-m", "memos.cli", "migrate", "types", "--dry-run"],
            capture_output=True, encoding="utf-8", errors="replace", cwd=str(PROJECT_ROOT),
            timeout=30,
        )
        assert result.returncode == 0, f"dry-run 失败: {result.stderr[:200]}"
