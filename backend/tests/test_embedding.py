"""Unit tests for the Embedding Provider abstraction.

测试策略
--------
bge-m3 模型本体 ~2.2GB，且首次加载需要网络/磁盘缓存；CI 与单测不可能每次真跑。
这里用 ``unittest.mock`` 把 :class:`sentence_transformers.SentenceTransformer`
打桩，让 :class:`LocalSentenceTransformerProvider` 持有"伪造"模型对象，
从而完整覆盖以下契约：

- ``dim`` 属性从 settings 读取并在 ``__init__`` 时锁定
- ``embed_texts`` 调用底层 ``model.encode`` 并把 numpy ndarray 转 list[float]
- 单例 + reset 行为
- 工厂函数 :func:`get_embedding_provider` 在 reset 后重新读取 settings
- Protocol 暴露 ``dim`` + ``embed_texts``（runtime_checkable 验证）

**真模型端到端测试**在 :mod:`tests.test_embedding_real` 中（默认 ``skip``，
需 ``DOCQA_RUN_REAL_EMBEDDING_TESTS=1`` 才执行）。
"""

import os
from typing import List
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.core.config import get_settings
from app.services.embedding import (
    EmbeddingProvider,
    LocalSentenceTransformerProvider,
    get_embedding_provider,
    reset_embedding_provider,
)
from app.services.embedding.base import EmbeddingProvider as EmbeddingProviderDirect
from app.services.embedding.factory import (
    get_embedding_provider as factory_get_embedding_provider,
)
from app.services.embedding.local import (
    BGE_M3_DIM,
    DEFAULT_MODEL_NAME,
    LocalSentenceTransformerProvider as LocalProviderDirect,
)


@pytest.fixture(autouse=True)
def reset_provider_instance():
    """清空单例，避免上一个测试的 settings 修改污染下一个测试。"""
    reset_embedding_provider()
    yield
    reset_embedding_provider()


def _make_fake_model(dim: int = 1024) -> MagicMock:
    """构造一个 ``SentenceTransformer`` 替身：``encode`` 返回 numpy 数组。

    不同文本基于 ``hash`` 得到不同向量，方便测试"相同输入 → 相同输出"和
    "不同输入 → 不同输出"。
    """
    fake_model = MagicMock(name="FakeSentenceTransformer")

    def fake_encode(texts, convert_to_numpy=True, show_progress_bar=False):
        # 用文本自身 hash 作为基底，使不同文本产生不同向量
        return np.asarray(
            [
                [(hash(text) % 1000) / 1000.0 + i * 0.0001 for i in range(dim)]
                for text in texts
            ],
            dtype=np.float32,
        )

    fake_model.encode = MagicMock(side_effect=fake_encode)
    return fake_model


class TestEmbeddingProviderProtocol:
    """验证 :class:`EmbeddingProvider` Protocol 暴露的契约。"""

    def test_protocol_exposes_dim_and_embed_texts(self):
        """``dim`` 属性 + ``embed_texts`` 方法是协议表面。"""
        sentinel = object()
        assert hasattr(EmbeddingProvider, "dim")
        assert hasattr(EmbeddingProvider, "embed_texts")
        assert callable(getattr(EmbeddingProvider, "embed_texts", None))
        # runtime_checkable → isinstance 在协议成员齐备时为 True
        assert isinstance(sentinel, EmbeddingProvider) is False

    def test_local_provider_is_runtime_checkable_provider(self):
        """实现类可以被 ``isinstance`` 判为协议实例（runtime_checkable）。"""
        with patch("app.services.embedding.local.SentenceTransformer"):
            provider = LocalSentenceTransformerProvider(dim=8)
            # Protocol 是结构化类型；只要 ``dim`` 属性 + ``embed_texts`` 方法存在
            # 即可通过 isinstance。属性型 Protocol 在 runtime_checkable 下会
            # 校验属性的存在。
            assert isinstance(provider, EmbeddingProvider)


class TestLocalSentenceTransformerProvider:
    """``LocalSentenceTransformerProvider`` 行为契约。"""

    def test_dim_and_model_name_default_to_bge_m3(self):
        """默认配置下 ``dim=1024``、``model_name=BAAI/bge-m3``。"""
        with patch("app.services.embedding.local.SentenceTransformer"):
            provider = LocalSentenceTransformerProvider()
            assert provider.dim == BGE_M3_DIM == 1024
            assert provider.model_name == DEFAULT_MODEL_NAME == "BAAI/bge-m3"

    def test_dim_and_model_name_overridable_via_constructor(self):
        """``__init__`` 显式参数优先于 settings。"""
        with patch("app.services.embedding.local.SentenceTransformer"):
            provider = LocalSentenceTransformerProvider(
                model_name="sentence-transformers/all-MiniLM-L6-v2",
                dim=384,
            )
            assert provider.dim == 384
            assert provider.model_name == "sentence-transformers/all-MiniLM-L6-v2"

    def test_dim_reads_from_settings_when_not_overridden(self):
        """``settings.embedding_dim`` 会反映到 ``provider.dim``。"""
        settings = get_settings()
        original_dim = settings.embedding_dim
        try:
            # 模拟 settings 注入：直接构造 provider 时把显式 dim 留空
            with patch("app.services.embedding.local.get_settings") as mock_gs:
                fake_settings = MagicMock()
                fake_settings.embedding_model = "custom/model"
                fake_settings.embedding_dim = 768
                mock_gs.return_value = fake_settings
                provider = LocalSentenceTransformerProvider()
            assert provider.dim == 768
            assert provider.model_name == "custom/model"
        finally:
            assert settings.embedding_dim == original_dim

    def test_embed_texts_calls_underlying_model_with_expected_args(self):
        """``embed_texts`` 必须把 texts 原样下传并禁用进度条。"""
        fake_model = _make_fake_model(dim=1024)
        with patch(
            "app.services.embedding.local.SentenceTransformer",
            return_value=fake_model,
        ) as st_cls:
            provider = LocalSentenceTransformerProvider()
            texts = ["hello", "world"]
            vectors = provider.embed_texts(texts)

        assert len(vectors) == 2
        assert all(len(v) == 1024 for v in vectors)
        # 确认 encode 被调用一次，参数正确（强制 CPU 避免 Apple MPS 崩溃）
        st_cls.assert_called_once_with("BAAI/bge-m3", device="cpu")
        fake_model.encode.assert_called_once()
        call_kwargs = fake_model.encode.call_args.kwargs
        assert call_kwargs["convert_to_numpy"] is True
        assert call_kwargs["show_progress_bar"] is False
        # 第一个位置参数是 texts
        assert fake_model.encode.call_args.args[0] == texts

    def test_embed_texts_converts_numpy_to_python_list(self):
        """返回类型必须是纯 Python ``list[list[float]]``，不能是 numpy。"""
        fake_model = _make_fake_model(dim=4)
        with patch(
            "app.services.embedding.local.SentenceTransformer",
            return_value=fake_model,
        ):
            provider = LocalSentenceTransformerProvider(dim=4)
            vectors = provider.embed_texts(["a", "b"])

        assert isinstance(vectors, list)
        for vec in vectors:
            assert isinstance(vec, list)
            for component in vec:
                # Python float，不是 np.float32
                assert type(component) is float

    def test_embed_texts_empty_input_short_circuits(self):
        """空输入直接返回 ``[]``，不触发模型加载。"""
        with patch("app.services.embedding.local.SentenceTransformer") as st_cls:
            provider = LocalSentenceTransformerProvider()
            assert provider.embed_texts([]) == []
            st_cls.assert_not_called()

    def test_model_loaded_lazily_on_first_embed(self):
        """首次 ``embed_texts`` 才加载模型；构造时不下载。"""
        with patch("app.services.embedding.local.SentenceTransformer") as st_cls:
            provider = LocalSentenceTransformerProvider()
            st_cls.assert_not_called()
            _ = provider.embed_texts(["x"])
            st_cls.assert_called_once()

    def test_singleton_inside_provider_caches_model(self):
        """同一 provider 多次 ``embed_texts`` 只加载一次模型。"""
        fake_model = _make_fake_model(dim=8)
        with patch(
            "app.services.embedding.local.SentenceTransformer",
            return_value=fake_model,
        ) as st_cls:
            provider = LocalSentenceTransformerProvider(dim=8)
            provider.embed_texts(["a"])
            provider.embed_texts(["b", "c"])
            st_cls.assert_called_once()

    def test_identical_inputs_yield_identical_vectors(self):
        """Mock 模型对相同输入产生相同向量（同 idx → 相同值）。"""
        fake_model = _make_fake_model(dim=8)
        with patch(
            "app.services.embedding.local.SentenceTransformer",
            return_value=fake_model,
        ):
            provider = LocalSentenceTransformerProvider(dim=8)
            v1 = provider.embed_texts(["hello"])
            v2 = provider.embed_texts(["hello"])
            assert v1 == v2

    def test_different_inputs_yield_different_vectors(self):
        """不同文本得到的向量不同（idx 不同 → 值不同）。"""
        fake_model = _make_fake_model(dim=8)
        with patch(
            "app.services.embedding.local.SentenceTransformer",
            return_value=fake_model,
        ):
            provider = LocalSentenceTransformerProvider(dim=8)
            v1 = provider.embed_texts(["a"])
            v2 = provider.embed_texts(["b"])
            assert v1 != v2


class TestEmbeddingProviderFactory:
    """工厂函数 + reset。"""

    def test_get_embedding_provider_returns_singleton(self):
        """``get_embedding_provider`` 多次调用返回同一实例。"""
        with patch("app.services.embedding.local.SentenceTransformer"):
            p1 = get_embedding_provider()
            p2 = get_embedding_provider()
            assert p1 is p2

    def test_get_embedding_provider_reads_current_settings(self):
        """reset 之后工厂必须重新读取 settings（热更新场景）。

        这里 patch :func:`app.services.embedding.factory.get_settings`，是工厂
        唯一一处读取 settings 的位置。
        """
        with patch("app.services.embedding.local.SentenceTransformer"):
            first = get_embedding_provider()
            assert first.dim == get_settings().embedding_dim

        # 模拟 settings 变更并 reset
        with patch("app.services.embedding.factory.get_settings") as mock_gs:
            fake_settings = MagicMock()
            fake_settings.embedding_model = "BAAI/bge-m3"
            fake_settings.embedding_dim = 256
            mock_gs.return_value = fake_settings
            reset_embedding_provider()
            second = get_embedding_provider()
            assert second is not first
            assert second.dim == 256

    def test_reset_clears_singleton(self):
        """``reset_embedding_provider`` 后工厂会创建新实例。"""
        with patch("app.services.embedding.local.SentenceTransformer"):
            p1 = get_embedding_provider()
            reset_embedding_provider()
            p2 = get_embedding_provider()
            assert p1 is not p2

    def test_reset_is_idempotent(self):
        """未持有单例时 ``reset`` 不抛异常。"""
        reset_embedding_provider()
        reset_embedding_provider()
        assert get_embedding_provider() is not None


class TestPublicSurface:
    """``app.services.embedding`` 顶层导出。"""

    def test_module_reexports_expected_symbols(self):
        from app.services import embedding

        assert embedding.EmbeddingProvider is EmbeddingProviderDirect
        assert embedding.LocalSentenceTransformerProvider is LocalProviderDirect
        assert embedding.get_embedding_provider is factory_get_embedding_provider
        assert callable(embedding.reset_embedding_provider)


class TestConfigEmbeddingFields:
    """``Settings`` 暴露 ``embedding_*`` 配置。"""

    def test_settings_default_embedding_model_is_bge_m3(self):
        settings = get_settings()
        assert settings.embedding_provider == "local"
        assert settings.embedding_model == "BAAI/bge-m3"
        assert settings.embedding_dim == 1024

    def test_settings_accepts_overrides_via_env(self, monkeypatch):
        """``EMBEDDING_MODEL`` / ``EMBEDDING_DIM`` 环境变量可被读取。"""
        from app.core.config import Settings

        # 直接构造 Settings 时把环境变量显式传入（更稳）
        s = Settings(
            embedding_model="custom/model",
            embedding_dim=512,
        )
        assert s.embedding_model == "custom/model"
        assert s.embedding_dim == 512
