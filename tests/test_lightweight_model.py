"""Phase 3 — F2: 模型轻量化选项测试"""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from memos.config import ModelConfig


class TestModelConfig:
    """F2.1: ModelConfig 新增字段"""

    def test_default_model_name(self):
        cfg = ModelConfig()
        assert cfg.name == "bge-large-zh-v1.5"

    def test_light_model_name(self):
        cfg = ModelConfig()
        assert cfg.light_model_name == "all-MiniLM-L6-v2"

    def test_vector_dim_default(self):
        cfg = ModelConfig()
        assert cfg.vector_dim == 1024

    def test_model_name_persists(self):
        cfg = ModelConfig(name="all-MiniLM-L6-v2", vector_dim=384)
        assert cfg.name == "all-MiniLM-L6-v2"
        assert cfg.vector_dim == 384

    def test_name_field_in_schema(self):
        """验证 name 字段在 model_dump 中"""
        cfg = ModelConfig()
        d = cfg.model_dump()
        assert "name" in d
        assert "light_model_name" in d


class TestSimilarityThreshold:
    """F2.4: 去重阈值按维度适配（v0.4.0 MED-2: 精确映射）"""

    def test_bge_threshold(self, monkeypatch):
        """1024 维 → 阈值 0.55"""
        import memos.engine.memory

        monkeypatch.setattr(memos.engine.memory, "_THRESHOLD_MAP", {1024: 0.55, 384: 0.65})
        monkeypatch.setattr(memos.engine.memory.config.model, "vector_dim", 1024)
        assert memos.engine.memory._get_similarity_threshold() == 0.55

    def test_minilm_threshold(self, monkeypatch):
        """384 维 → 阈值 0.65"""
        import memos.engine.memory

        monkeypatch.setattr(memos.engine.memory.config.model, "vector_dim", 384)
        assert memos.engine.memory._get_similarity_threshold() == 0.65

    def test_threshold_384_boundary(self, monkeypatch):
        """384 维精确命中 threshold_map"""
        import memos.engine.memory

        monkeypatch.setattr(memos.engine.memory.config.model, "vector_dim", 384)
        assert memos.engine.memory._get_similarity_threshold() == 0.65

    def test_unknown_dim_fallback(self, monkeypatch):
        """未知维度回退到 config.memory.similarity_threshold"""
        import memos.engine.memory

        monkeypatch.setattr(memos.engine.memory.config.model, "vector_dim", 768)
        assert memos.engine.memory._get_similarity_threshold() == 0.55  # 默认

    def test_SIMILARITY_THRESHOLD_backward_compat(self):
        """SIMILARITY_THRESHOLD 模块常量保持向后兼容"""
        from memos.engine.memory import SIMILARITY_THRESHOLD

        assert SIMILARITY_THRESHOLD == 0.55


class TestWizardModelPathUpdate:
    """验证 wizard _step_4 更新 config.model.path/name/vector_dim"""

    def test_minilm_updates_all_fields(self, tmp_path, monkeypatch):
        """选择 MiniLM 后 config.model 的三个字段全部更新"""
        from memos.features.wizard import InitWizard
        from memos.config import MemoConfig

        cfg = MemoConfig()
        cfg.model.path = str(tmp_path / "model" / "bge-large-zh-v1.5")
        cfg.model.name = "bge-large-zh-v1.5"
        cfg.model.vector_dim = 1024

        wizard = InitWizard(cfg, home=tmp_path)
        wizard._state["model_name"] = "all-MiniLM-L6-v2"

        # Mock 避免污染真实文件和触发网络下载
        monkeypatch.setattr(
            "memos.storage.embeddings.get_model_path",
            lambda model_name=None: tmp_path / "model" / (model_name or "bge-large-zh-v1.5"),
        )
        monkeypatch.setattr("memos.storage.embeddings.download_model", lambda *a, **kw: True)
        monkeypatch.setattr("memos.storage.embeddings.model_exists", lambda *a: True)
        # 防止 cfg.save() 写入真实 etc/config.json
        monkeypatch.setattr("memos.config.MemoConfig.save", lambda self: None)

        result = wizard._step_4()
        assert result is True
        assert "all-MiniLM-L6-v2" in cfg.model.path
        assert cfg.model.name == "all-MiniLM-L6-v2"
        assert cfg.model.vector_dim == 384
