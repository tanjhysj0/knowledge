"""Unit tests for the DB-persisted LLM settings service (#67).

覆盖 load（启动恢复：有行覆盖 / 无行清空）、GET（DB 读取 + 脱敏）、
PUT（upsert 双写 DB 与内存 + reset_providers）。使用内存假 db Session，
不依赖真实数据库。
"""
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.models.schemas import SettingsUpdate
from app.models.setting import AppSetting
from app.services import settings as settings_service


class _FakeSettingsDb:
    """最小假 db Session：execute 返回可注入的行，并记录 upsert 语句与 commit。"""

    def __init__(self, row=None):
        self._row = row
        self.executed = []
        self.commits = 0

    async def execute(self, statement):
        self.executed.append(statement)
        result = MagicMock()
        result.scalar_one_or_none.return_value = self._row
        return result

    async def commit(self):
        self.commits += 1


def _row(**fields):
    return AppSetting(id=1, **fields)


@pytest.fixture
def fake_settings(monkeypatch):
    """替换 settings 服务的内存单例（模拟无行 load 后的空配置状态）。"""
    instance = Settings(
        llm_provider="openai",
        openai_api_key="",
        openai_base_url="",
        openai_model="",
        anthropic_api_key="",
        anthropic_base_url="",
        anthropic_model="",
    )
    monkeypatch.setattr(settings_service, "get_settings", lambda: instance)
    return instance


class TestLoadFromDb:
    """启动恢复：DB 是唯一事实源。"""

    @pytest.mark.asyncio
    async def test_load_applies_row_to_memory(self, fake_settings):
        db = _FakeSettingsDb(
            row=_row(
                llm_provider="anthropic",
                anthropic_api_key="sk-ant-abc",
                anthropic_model="claude-test",
            )
        )

        await settings_service.load_llm_settings_from_db(db)

        assert fake_settings.llm_provider == "anthropic"
        assert fake_settings.anthropic_api_key == "sk-ant-abc"
        assert fake_settings.anthropic_model == "claude-test"

    @pytest.mark.asyncio
    async def test_load_without_row_resets_memory_to_unconfigured(
        self, fake_settings, monkeypatch
    ):
        # 先污染内存（模拟环境变量/.env 残留的 key），load 后应被清空。
        fake_settings.openai_api_key = "leaked-from-env"
        fake_settings.anthropic_api_key = "leaked-too"

        await settings_service.load_llm_settings_from_db(_FakeSettingsDb())

        assert fake_settings.llm_provider == "openai"
        assert fake_settings.openai_api_key == ""
        assert fake_settings.anthropic_api_key == ""


class TestGetFromDb:
    """GET：从 DB 读取并脱敏。"""

    @pytest.mark.asyncio
    async def test_get_reads_row_and_masks_key(self):
        db = _FakeSettingsDb(
            row=_row(
                llm_provider="openai",
                openai_api_key="sk-abcdefgh1234",
                openai_base_url="https://openai.example",
                openai_model="gpt-test",
            )
        )

        response = await settings_service.get_settings_response(db)

        assert response.llm.provider == "openai"
        assert response.llm.api_key_masked == "sk-a...1234"
        assert response.llm.base_url == "https://openai.example"
        assert response.llm.model == "gpt-test"

    @pytest.mark.asyncio
    async def test_get_without_row_returns_empty_default(self):
        response = await settings_service.get_settings_response(_FakeSettingsDb())

        assert response.llm.provider == "openai"
        assert response.llm.api_key_masked == ""
        assert response.llm.base_url == ""
        assert response.llm.model == ""


class TestUpdatePersists:
    """PUT：upsert DB + 内存 + reset_providers。"""

    @pytest.mark.asyncio
    async def test_update_without_row_creates_and_commits(
        self, fake_settings, monkeypatch
    ):
        reset_calls = []
        monkeypatch.setattr(
            settings_service, "reset_providers", lambda: reset_calls.append(True)
        )
        db = _FakeSettingsDb()

        response = await settings_service.update_llm_settings(
            db=db,
            update=SettingsUpdate(llm_api_key="sk-new-key", llm_model="gpt-new"),
        )

        assert db.commits == 1
        assert len(db.executed) == 1
        params = db.executed[0].compile().params
        assert params["id"] == 1
        assert params["openai_api_key"] == "sk-new-key"
        assert params["openai_model"] == "gpt-new"
        assert response.settings.llm.api_key_masked == "sk-n...-key"
        assert reset_calls == [True]

    @pytest.mark.asyncio
    async def test_update_existing_row_merges_without_new_insert(
        self, fake_settings, monkeypatch
    ):
        monkeypatch.setattr(
            settings_service, "reset_providers", lambda: None
        )
        existing = _row(
            llm_provider="openai",
            openai_api_key="sk-old",
            openai_model="gpt-old",
        )
        # 模拟启动 load：内存镜像与 DB 行一致。
        fake_settings.openai_api_key = "sk-old"
        fake_settings.openai_model = "gpt-old"
        db = _FakeSettingsDb(row=existing)

        await settings_service.update_llm_settings(
            db=db,
            update=SettingsUpdate(llm_model="gpt-new"),
        )

        assert db.commits == 1
        # 单行 upsert：一条 ON CONFLICT 语句，无 insert 分支。
        assert len(db.executed) == 1
        params = db.executed[0].compile().params
        # 部分更新不改 key：内存镜像全量落库，key 保持原值。
        assert params["openai_api_key"] == "sk-old"
        assert params["openai_model"] == "gpt-new"
        assert fake_settings.openai_model == "gpt-new"
