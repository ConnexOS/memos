"""测试 MEMOS_HOME 路径解析和环境变量覆盖。"""

import os
import tempfile
from pathlib import Path


class TestGetMemosHome:
    """测试 get_memos_home() 函数。"""

    def test_default_uses_dot_memos(self, monkeypatch, tmp_path):
        """无 MEMOS_HOME 且无本地 etc/config.json 时，默认使用 ~/.memos/。"""
        monkeypatch.delenv("MEMOS_HOME", raising=False)
        monkeypatch.chdir(tmp_path)  # 切换到一个没有 etc/config.json 的目录
        from memos.config import get_memos_home

        home = get_memos_home()
        assert home == Path.home() / ".memos"

    def test_env_var_overrides_home(self, monkeypatch):
        """$MEMOS_HOME 覆盖默认值。"""
        monkeypatch.setenv("MEMOS_HOME", "D:/tmp/custom-home")
        from memos.config import get_memos_home

        home = get_memos_home()
        assert home == Path("D:/tmp/custom-home")

    def test_env_var_affects_config_file(self, monkeypatch):
        """$MEMOS_HOME 影响配置文件和提示词文件路径。"""
        monkeypatch.setenv("MEMOS_HOME", "D:/tmp/my-memos")
        from memos.config import _get_config_file, _get_prompts_file

        assert _get_config_file() == Path("D:/tmp/my-memos/etc/config.json")
        assert _get_prompts_file() == Path("D:/tmp/my-memos/etc/prompts.json")


class TestEnsureMemosHome:
    """测试 ensure_memos_home() 目录创建。"""

    def test_creates_all_directories(self, monkeypatch):
        """ensure_memos_home() 创建 etc/memdb/model 三个子目录。"""
        tmp = tempfile.mkdtemp(prefix="memos-test-")
        try:
            home_path = os.path.join(tmp, ".memos")
            monkeypatch.setenv("MEMOS_HOME", home_path)
            from memos.config import ensure_memos_home

            home = ensure_memos_home()
            assert home == Path(home_path)
            for sub in ["etc", "memdb", "model"]:
                assert (home / sub).is_dir(), f"{sub} 目录未创建"
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


class TestConfigPathDefaults:
    """测试 Pydantic 模型的路径默认值使用 MEMOS_HOME。"""

    def test_chroma_path_default_uses_memos_home(self, monkeypatch):
        """ChromaConfig.path 默认值基于 MEMOS_HOME。"""
        monkeypatch.setenv("MEMOS_HOME", "D:/tmp/my-chroma-test")
        from memos.config import ChromaConfig

        c = ChromaConfig()
        assert c.path == str(Path("D:/tmp/my-chroma-test/memdb"))

    def test_model_path_default_uses_memos_home(self, monkeypatch):
        """ModelConfig.path 默认值基于 MEMOS_HOME。"""
        monkeypatch.setenv("MEMOS_HOME", "D:/tmp/my-model-test")
        from memos.config import ModelConfig

        m = ModelConfig()
        assert m.path == str(Path("D:/tmp/my-model-test/model/bge-large-zh-v1.5"))

    def test_chroma_path_overridden_by_env(self, monkeypatch):
        """MEMOS_CHROMA_PATH 环境变量覆盖 chroma.path。"""
        monkeypatch.setenv("MEMOS_HOME", "D:/tmp/base")
        monkeypatch.setenv("MEMOS_CHROMA_PATH", "D:/data/vectordb")
        from memos.config import MemoConfig

        cfg = MemoConfig.load()
        assert cfg.chroma.path == "D:/data/vectordb"

    def test_model_path_overridden_by_env(self, monkeypatch):
        """MEMOS_MODEL_PATH 环境变量覆盖 model.path。"""
        monkeypatch.setenv("MEMOS_HOME", "D:/tmp/base")
        monkeypatch.setenv("MEMOS_MODEL_PATH", "D:/models/minilm")
        from memos.config import MemoConfig

        cfg = MemoConfig.load()
        assert cfg.model.path == "D:/models/minilm"


class TestEnvVarPriority:
    """测试环境变量优先级：具体路径 > MEMOS_HOME > 默认。"""

    def test_specific_env_beats_memos_home(self, monkeypatch):
        """MEMOS_CHROMA_PATH 优先级高于 MEMOS_HOME。"""
        monkeypatch.setenv("MEMOS_HOME", "D:/tmp/home")
        monkeypatch.setenv("MEMOS_CHROMA_PATH", "D:/specific/db")
        from memos.config import MemoConfig

        cfg = MemoConfig.load()
        assert cfg.chroma.path == "D:/specific/db"

    def test_memos_home_beats_default(self, monkeypatch):
        """MEMOS_HOME 优先级高于默认值。"""
        monkeypatch.setenv("MEMOS_HOME", "D:/explicit/home")
        from memos.config import get_memos_home

        assert get_memos_home() == Path("D:/explicit/home")


class TestAutoDetection:
    """测试本地开发模式自动检测。"""

    def test_cwd_with_config_json_detected_as_home(self, monkeypatch, tmp_path):
        """当前目录存在 etc/config.json 时，自动使用当前目录作为 MEMOS_HOME。"""
        monkeypatch.delenv("MEMOS_HOME", raising=False)
        # 在 tmp_path 下创建 etc/config.json
        etc_dir = tmp_path / "etc"
        etc_dir.mkdir()
        (etc_dir / "config.json").write_text("{}")
        monkeypatch.chdir(tmp_path)

        from memos.config import get_memos_home

        home = get_memos_home()
        assert home == tmp_path

    def test_cwd_without_config_json_falls_back(self, monkeypatch, tmp_path):
        """当前目录无 etc/config.json 时，回退到 ~/.memos/。"""
        monkeypatch.delenv("MEMOS_HOME", raising=False)
        monkeypatch.chdir(tmp_path)  # tmp_path 没有 etc/config.json

        from memos.config import get_memos_home

        home = get_memos_home()
        assert home == Path.home() / ".memos"


class TestLazyEvaluation:
    """测试路径访问器的惰性求值——修改环境变量后路径随之变化。"""

    def test_changing_memos_home_changes_paths(self, monkeypatch):
        """同一进程内修改 MEMOS_HOME 后，_get_config_file 返回新路径。"""
        from memos.config import _get_config_file

        monkeypatch.setenv("MEMOS_HOME", "D:/first")
        first = _get_config_file()
        assert first == Path("D:/first/etc/config.json")

        monkeypatch.setenv("MEMOS_HOME", "D:/second")
        second = _get_config_file()
        assert second == Path("D:/second/etc/config.json")
        assert first != second
