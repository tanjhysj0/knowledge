"""#68：模型列表化后的 LLM 设置服务单元测试。

覆盖：旧 settings 单行迁移（拆分为 provider 记录 / 原 provider 为默认 /
回退第一条 / 幂等跳过 / 删旧行）、load（模型列表恢复内存 / 空列表清空）、
GET（默认模型 + 脱敏）、PUT（无记录新建默认 / 更新默认记录 /
切换 provider 设默认 / 切换无记录新建默认）。使用内存假 db Session，
不依赖真实数据库。
"""
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.models.llm_model import LLMModel
from app.models.schemas import SettingsUpdate
from app.models.setting import AppSetting
from app.services import settings as settings_service


def _model_row(model_id, provider_type="openai", is_default=False, **fields):
    """构造完整模型行（含时间戳）。"""
    return LLMModel(
        id=model_id,
        provider_type=provider_type,
        base_url=fields.get("base_url", f"https://{provider_type}.example"),
        model_name=fields.get("model_name", f"{provider_type}-model"),
        api_key=fields.get("api_key", f"sk-{provider_type}-key-1234"),
        is_default=is_default,
        created_at=fields.get("created_at", datetime(2026, 8, 1)),
        updated_at=fields.get("updated_at", datetime(2026, 8, 1)),
    )


def _legacy_row(**fields):
    """旧 settings 单行（id=1 固定）。"""
    defaults = dict(
        llm_provider="openai",
        openai_api_key="",
        openai_base_url="",
        openai_model="",
        anthropic_api_key="",
        anthropic_base_url="",
        anthropic_model="",
    )
    defaults.update(fields)
    return AppSetting(id=1, **defaults)


class _Result:
    """execute 返回的假结果：同时暴露单行与列表两个视图。"""

    def __init__(self, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        mock = MagicMock()
        mock.all.return_value = self._rows
        return mock


class _FakeDb:
    """最小假 db Session：execute 按调用顺序弹出预置结果，记录写操作。"""

    def __init__(self, specs=None):
        self._specs = list(specs or [])
        self.statements = []
        self.added = []
        self.deleted = []
        self.commits = 0

    async def execute(self, statement):
        self.statements.append(statement)
        spec = self._specs.pop(0) if self._specs else {}
        return _Result(spec.get("scalar"), spec.get("rows", []))

    def add(self, row):
        if row.id is None:
            row.id = len(self.added) + 1
        if row.created_at is None:
            row.created_at = datetime(2026, 8, 1)
        if row.updated_at is None:
            row.updated_at = datetime(2026, 8, 1)
        self.added.append(row)

    async def delete(self, row):
        self.deleted.append(row)

    async def commit(self):
        self.commits += 1

    async def refresh(self, row):
        return None


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


class TestMigrateLegacySettings:
    """#68：旧 settings 单行 → llm_models 一次性迁移。"""

    @pytest.mark.asyncio
    async def test_splits_legacy_row_into_provider_records(self):
        legacy = _legacy_row(
            llm_provider="anthropic",
            openai_api_key="sk-openai-1",
            openai_model="gpt-test",
            anthropic_api_key="sk-ant-1",
            anthropic_model="claude-test",
        )
        db = _FakeDb(specs=[{"rows": []}, {"scalar": legacy}])

        await settings_service.migrate_legacy_settings(db)

        assert db.commits == 1
        assert len(db.added) == 2
        added = {m.provider_type: m for m in db.added}
        assert added["openai"].is_default is False
        assert added["openai"].model_name == "gpt-test"
        assert added["anthropic"].is_default is True
        assert added["anthropic"].api_key == "sk-ant-1"
        # 旧行删除，一次性生效。
        assert db.deleted == [legacy]

    @pytest.mark.asyncio
    async def test_skips_provider_with_all_empty_fields(self):
        legacy = _legacy_row(anthropic_api_key="sk-ant-1")
        db = _FakeDb(specs=[{"rows": []}, {"scalar": legacy}])

        await settings_service.migrate_legacy_settings(db)

        # openai 三字段全空 → 不生成记录；仅 anthropic 一条。
        assert [m.provider_type for m in db.added] == ["anthropic"]

    @pytest.mark.asyncio
    async def test_falls_back_to_first_record_when_provider_missing(self):
        # 原 provider 为 openai，但 openai 字段全空 → 回退第一条（anthropic）为默认。
        legacy = _legacy_row(
            llm_provider="openai", anthropic_api_key="sk-ant-1", anthropic_model="c"
        )
        db = _FakeDb(specs=[{"rows": []}, {"scalar": legacy}])

        await settings_service.migrate_legacy_settings(db)

        assert len(db.added) == 1
        assert db.added[0].provider_type == "anthropic"
        assert db.added[0].is_default is True

    @pytest.mark.asyncio
    async def test_skips_when_models_already_exist(self):
        existing = _model_row(1, "openai", True)
        db = _FakeDb(specs=[{"rows": [existing]}])

        await settings_service.migrate_legacy_settings(db)

        assert db.added == []
        assert db.deleted == []
        assert db.commits == 0

    @pytest.mark.asyncio
    async def test_noop_when_legacy_row_absent(self):
        db = _FakeDb(specs=[{"rows": []}, {"scalar": None}])

        await settings_service.migrate_legacy_settings(db)

        assert db.added == []
        assert db.deleted == []
        assert db.commits == 0

    @pytest.mark.asyncio
    async def test_empty_legacy_row_is_deleted_without_records(self):
        legacy = _legacy_row()
        db = _FakeDb(specs=[{"rows": []}, {"scalar": legacy}])

        await settings_service.migrate_legacy_settings(db)

        assert db.added == []
        assert db.deleted == [legacy]
        assert db.commits == 1


class TestLoadFromDb:
    """启动恢复：llm_models 是唯一事实源。"""

    @pytest.mark.asyncio
    async def test_load_applies_models_to_memory(self, fake_settings):
        default = _model_row(
            1, "anthropic", True, api_key="sk-ant-abc", model_name="claude-test"
        )
        openai_row = _model_row(2, "openai", False, model_name="gpt-test")
        db = _FakeDb(specs=[{"rows": [default, openai_row]}])

        await settings_service.load_llm_settings_from_db(db)

        assert fake_settings.llm_provider == "anthropic"
        assert fake_settings.anthropic_api_key == "sk-ant-abc"
        assert fake_settings.anthropic_model == "claude-test"
        assert fake_settings.openai_model == "gpt-test"

    @pytest.mark.asyncio
    async def test_load_same_provider_prefers_default_config(self, fake_settings):
        default = _model_row(1, "openai", True, model_name="gpt-default")
        extra = _model_row(2, "openai", False, model_name="gpt-extra")
        db = _FakeDb(specs=[{"rows": [default, extra]}])

        await settings_service.load_llm_settings_from_db(db)

        # 默认记录最后应用，多记录同 provider 时默认配置优先生效。
        assert fake_settings.openai_model == "gpt-default"

    @pytest.mark.asyncio
    async def test_load_without_default_uses_first_row(self, fake_settings):
        first = _model_row(1, "openai", False, model_name="gpt-first")
        db = _FakeDb(specs=[{"rows": [first]}])

        await settings_service.load_llm_settings_from_db(db)

        assert fake_settings.llm_provider == "openai"
        assert fake_settings.openai_model == "gpt-first"

    @pytest.mark.asyncio
    async def test_load_without_rows_resets_memory_to_unconfigured(
        self, fake_settings
    ):
        fake_settings.openai_api_key = "leaked-from-env"
        fake_settings.anthropic_api_key = "leaked-too"

        await settings_service.load_llm_settings_from_db(_FakeDb(specs=[{"rows": []}]))

        assert fake_settings.llm_provider == "openai"
        assert fake_settings.openai_api_key == ""
        assert fake_settings.anthropic_api_key == ""


class TestGetFromDb:
    """GET /api/settings：读默认模型记录并脱敏。"""

    @pytest.mark.asyncio
    async def test_get_reads_default_model_and_masks_key(self):
        default = _model_row(
            1,
            "anthropic",
            True,
            api_key="sk-ant-secret-1234",
            base_url="https://anthropic.example",
            model_name="claude-test",
        )
        db = _FakeDb(specs=[{"rows": [default, _model_row(2, "openai")]}])

        response = await settings_service.get_settings_response(db)

        assert response.llm.provider == "anthropic"
        assert response.llm.api_key_masked == "sk-a...1234"
        assert response.llm.base_url == "https://anthropic.example"
        assert response.llm.model == "claude-test"

    @pytest.mark.asyncio
    async def test_get_without_rows_returns_empty_default(self):
        response = await settings_service.get_settings_response(
            _FakeDb(specs=[{"rows": []}])
        )

        assert response.llm.provider == "openai"
        assert response.llm.api_key_masked == ""
        assert response.llm.base_url == ""
        assert response.llm.model == ""


class TestUpdatePersists:
    """PUT /api/settings：写入 llm_models + 内存重载 + reset_providers。"""

    @pytest.mark.asyncio
    async def test_update_without_rows_creates_default_model(
        self, fake_settings, monkeypatch
    ):
        reset_calls = []
        monkeypatch.setattr(
            settings_service, "reset_providers", lambda: reset_calls.append(True)
        )
        post = _model_row(
            1, "openai", True, api_key="sk-new-key", model_name="gpt-new"
        )
        db = _FakeDb(
            specs=[
                {"rows": []},  # update 前列表
                {"rows": []},  # create_model 内空列表校验
                {},  # create_model 内加锁
                {},  # create_model 内降级
                {"rows": [post]},  # 落库后 load 重载
            ]
        )

        response = await settings_service.update_llm_settings(
            db=db,
            update=SettingsUpdate(llm_api_key="sk-new-key", llm_model="gpt-new"),
        )

        assert db.commits >= 1
        assert len(db.added) == 1
        assert db.added[0].is_default is True
        assert db.added[0].api_key == "sk-new-key"
        assert fake_settings.openai_model == "gpt-new"
        assert response.settings.llm.provider == "openai"
        assert response.settings.llm.api_key_masked == "sk-n...-key"
        assert reset_calls == [True]

    @pytest.mark.asyncio
    async def test_update_existing_default_record(
        self, fake_settings, monkeypatch
    ):
        monkeypatch.setattr(settings_service, "reset_providers", lambda: None)
        existing = _model_row(1, "openai", True, api_key="sk-old", model_name="gpt-old")
        db = _FakeDb(
            specs=[
                {"rows": [existing]},  # update 前列表
                {"scalar": existing},  # update_model 内取行
                {"rows": [existing]},  # load 重载
            ]
        )

        response = await settings_service.update_llm_settings(
            db=db, update=SettingsUpdate(llm_model="gpt-new")
        )

        # api_key 未提供 → 保持原值；内存镜像同步。
        assert existing.api_key == "sk-old"
        assert existing.model_name == "gpt-new"
        assert fake_settings.openai_model == "gpt-new"
        assert response.settings.llm.model == "gpt-new"

    @pytest.mark.asyncio
    async def test_switch_provider_sets_target_as_default(
        self, fake_settings, monkeypatch
    ):
        monkeypatch.setattr(settings_service, "reset_providers", lambda: None)
        openai_row = _model_row(1, "openai", True)
        anthropic_row = _model_row(2, "anthropic", False)
        refreshed = _model_row(2, "anthropic", True)
        db = _FakeDb(
            specs=[
                {"rows": [openai_row, anthropic_row]},  # update 前列表
                {"scalar": anthropic_row},  # update_model 内取行
                {"scalar": anthropic_row},  # set_default 前取行
                {},  # set_default 加锁
                {},  # set_default 降级
                {},  # set_default 提升
                {"scalar": refreshed},  # set_default 提交后重查
                {"rows": [refreshed, openai_row]},  # load 重载
            ]
        )

        response = await settings_service.update_llm_settings(
            db=db,
            update=SettingsUpdate(llm_provider="anthropic", llm_model="claude-new"),
        )

        assert anthropic_row.model_name == "claude-new"
        assert fake_settings.llm_provider == "anthropic"
        assert response.settings.llm.provider == "anthropic"

    @pytest.mark.asyncio
    async def test_switch_provider_without_record_creates_default(
        self, fake_settings, monkeypatch
    ):
        monkeypatch.setattr(settings_service, "reset_providers", lambda: None)
        openai_row = _model_row(1, "openai", True)
        created = _model_row(
            2, "anthropic", True, api_key="sk-ant-new", model_name="claude-new"
        )
        db = _FakeDb(
            specs=[
                {"rows": [openai_row]},  # update 前列表
                {"rows": [openai_row]},  # create_model 内空列表校验
                {},  # create_model 内加锁
                {},  # create_model 内降级
                {"rows": [created, openai_row]},  # load 重载
            ]
        )

        response = await settings_service.update_llm_settings(
            db=db,
            update=SettingsUpdate(
                llm_provider="anthropic", llm_api_key="sk-ant-new"
            ),
        )

        assert len(db.added) == 1
        assert db.added[0].provider_type == "anthropic"
        assert db.added[0].is_default is True
        assert fake_settings.llm_provider == "anthropic"
        assert fake_settings.anthropic_api_key == "sk-ant-new"
        assert response.settings.llm.provider == "anthropic"
