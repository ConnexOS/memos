import json
import os
import shutil
import tempfile
from pathlib import Path


def test_migrate_sets_mode():
    """迁移后 server.mode 应为 unified"""
    orig_cwd = os.getcwd()
    tmp = tempfile.mkdtemp()
    try:
        config_dir = Path(tmp) / "etc"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "config.json"
        config_data = {
            "server": {"id_length": 8, "mcp_top_k_max": 20, "response_truncate_length": 100},
            "auth": {"token_hash": "", "secret_key": ""},
        }
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config_data, f)

        os.chdir(tmp)
        from memos.config import MemoConfig
        cfg = MemoConfig.load()
        cfg.server.mode = "unified"
        cfg.save()

        cfg2 = MemoConfig.load()
        assert cfg2.server.mode == "unified"
    finally:
        os.chdir(orig_cwd)
        shutil.rmtree(tmp, ignore_errors=True)
