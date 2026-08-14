"""#68/#69：llm_models 模型层、CRUD 服务与运行时同步单元测试。

覆盖：partial unique index 定义（数据库层唯一默认兜底）、列表脱敏、
新增（空列表必须默认 / 默认新增降级旧默认）、编辑（api_key 留空保持原值）、
删除（默认且有其它记录拒绝 / 仅剩一条默认允许）、设默认（原子 CASE 更新）、
路由层错误映射，以及 #69 的运行时单例同步（默认行镜像 / 空列表重置）与
模型列表拉取代理（OpenAI / Anthropic 响应解析与请求头）。使用内存假 db
Session，不依赖真实数据库。
"""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.api import models as models_api
from app.core.database import Base
from app.models.llm_model import LLMModel
from app.models.schemas import LLMModelCreate, LLMModelUpdate, ModelListFetchRequest
from app.services import models as models_service
from app.services.models import (
    ModelDefaultConflictError,
    ModelDefaultRequiredError,
    ModelNotFoundError,
)


def _model_row(model_id, provider_type="openai", is_default=False, **fields):
    """构造完整 ORM 行（含时间戳），缺省字段用可辨识的默认值。"""
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


class TestLLMModelTable:
    """模型表定义：随 create_all 创建 + partial unique index 保证唯一默认。"""

    def test_table_registered_in_base_metadata(self):
        assert LLMModel.__tablename__ == "llm_models"
        assert LLMModel.__table__ in Base.metadata.tables.values()

    def test_partial_unique_index_guards_single_default(self):
        index = next(
            ix
            for ix in LLMModel.__table__.indexes
            if ix.name == "uq_llm_models_single_default"
        )
        assert index.unique
        where = index.dialect_options["postgresql"]["where"]
        assert str(where) == "is_default"

    def test_is_default_defaults_false_not_null(self):
        col = LLMModel.__table__.c.is_default
        assert col.default.arg is False
        assert not col.nullable


class TestListModels:
    @pytest.mark.asyncio
    async def test_list_masks_api_keys(self):
        db = _FakeDb(
            specs=[
                {"rows": [_model_row(1, "openai", True), _model_row(2, "anthropic")]}
            ]
        )

        response = await models_service.list_models(db)

        assert [m.id for m in response] == [1, 2]
        assert response[0].api_key_masked == "sk-o...1234"
        assert response[1].api_key_masked == "sk-a...1234"

    @pytest.mark.asyncio
    async def test_list_empty_returns_empty(self):
        db = _FakeDb(specs=[{"rows": []}])

        assert await models_service.list_models(db) == []

    def test_mask_short_key_and_empty(self):
        assert models_service.mask_api_key("") == ""
        assert models_service.mask_api_key("abc123") == "***"


class TestCreateModel:
    @pytest.mark.asyncio
    async def test_create_first_model_requires_default(self):
        db = _FakeDb(specs=[{"rows": []}])
        payload = LLMModelCreate(provider_type="openai", is_default=False)

        with pytest.raises(ModelDefaultRequiredError):
            await models_service.create_model(db, payload)

        assert db.added == []
        assert db.commits == 0

    @pytest.mark.asyncio
    async def test_create_first_model_as_default(self):
        db = _FakeDb(specs=[{"rows": []}])
        payload = LLMModelCreate(
            provider_type="openai",
            api_key="sk-new-1234",
            model_name="gpt-test",
            is_default=True,
        )

        await models_service.create_model(db, payload)

        assert db.commits == 1
        assert len(db.added) == 1
        assert db.added[0].is_default is True
        assert db.added[0].provider_type == "openai"

    @pytest.mark.asyncio
    async def test_create_default_demotes_existing_default(self):
        db = _FakeDb(
            specs=[{"rows": [_model_row(1, "openai", True)]}]
        )
        payload = LLMModelCreate(provider_type="anthropic", is_default=True)

        await models_service.create_model(db, payload)

        # 列表非空 → 先加锁、降级 UPDATE，再插入新默认。
        assert len(db.statements) == 3
        demote_params = db.statements[2].compile().params
        assert demote_params["is_default"] is False
        assert db.added[0].is_default is True

    @pytest.mark.asyncio
    async def test_create_non_default_with_existing_default_skips_demote(self):
        db = _FakeDb(
            specs=[{"rows": [_model_row(1, "openai", True)]}]
        )
        payload = LLMModelCreate(provider_type="anthropic", is_default=False)

        await models_service.create_model(db, payload)

        # 非默认新增：只有列表查询一条语句，无降级 UPDATE。
        assert len(db.statements) == 1
        assert db.added[0].is_default is False


class TestUpdateModel:
    @pytest.mark.asyncio
    async def test_update_fields_and_keep_key_when_blank(self):
        row = _model_row(1, "openai", True, api_key="sk-original")
        db = _FakeDb(specs=[{"scalar": row}])
        payload = LLMModelUpdate(model_name="gpt-new", api_key="")

        response = await models_service.update_model(db, 1, payload)

        assert row.model_name == "gpt-new"
        assert row.api_key == "sk-original"  # 留空保持原值
        assert response.api_key_masked == "sk-o...inal"
        assert db.commits == 1

    @pytest.mark.asyncio
    async def test_update_key_when_provided(self):
        row = _model_row(1, "openai", api_key="sk-original")
        db = _FakeDb(specs=[{"scalar": row}])

        await models_service.update_model(
            db, 1, LLMModelUpdate(api_key="sk-rotated-9999")
        )

        assert row.api_key == "sk-rotated-9999"

    @pytest.mark.asyncio
    async def test_update_missing_model_raises(self):
        db = _FakeDb(specs=[{"scalar": None}])

        with pytest.raises(ModelNotFoundError):
            await models_service.update_model(db, 99, LLMModelUpdate())


class TestDeleteModel:
    @pytest.mark.asyncio
    async def test_delete_default_with_others_conflicts(self):
        target = _model_row(1, "openai", True)
        db = _FakeDb(
            specs=[{"scalar": target}, {"rows": [target, _model_row(2, "anthropic")]}]
        )

        with pytest.raises(ModelDefaultConflictError):
            await models_service.delete_model(db, 1)

        assert db.deleted == []
        assert db.commits == 0

    @pytest.mark.asyncio
    async def test_delete_single_default_allowed(self):
        target = _model_row(1, "openai", True)
        db = _FakeDb(specs=[{"scalar": target}, {"rows": [target]}])

        await models_service.delete_model(db, 1)

        assert db.deleted == [target]
        assert db.commits == 1

    @pytest.mark.asyncio
    async def test_delete_non_default_ok(self):
        target = _model_row(2, "anthropic", False)
        default = _model_row(1, "openai", True)
        db = _FakeDb(specs=[{"scalar": target}, {"rows": [default, target]}])

        await models_service.delete_model(db, 2)

        assert db.deleted == [target]

    @pytest.mark.asyncio
    async def test_delete_missing_model_raises(self):
        db = _FakeDb(specs=[{"scalar": None}])

        with pytest.raises(ModelNotFoundError):
            await models_service.delete_model(db, 99)


class TestSetDefaultModel:
    @pytest.mark.asyncio
    async def test_set_default_demotes_then_promotes(self):
        target = _model_row(2, "anthropic", False)
        refreshed = _model_row(2, "anthropic", True)
        db = _FakeDb(
            specs=[{"scalar": target}, {}, {}, {}, {"scalar": refreshed}]
        )

        response = await models_service.set_default_model(db, 2)

        # 同事务三步：先加锁、全量降级，再提升目标；提交后重查返回新状态。
        demote = db.statements[2].compile()
        assert demote.params["is_default"] is False
        promote = db.statements[3].compile()
        assert promote.params["is_default"] is True
        assert promote.params["id_1"] == 2
        assert response.is_default is True
        assert db.commits == 1

    @pytest.mark.asyncio
    async def test_set_default_missing_model_raises(self):
        db = _FakeDb(specs=[{"scalar": None}])

        with pytest.raises(ModelNotFoundError):
            await models_service.set_default_model(db, 99)


class TestModelsApiErrorMapping:
    """路由层：服务错误 → HTTP 状态码映射。"""

    @pytest.mark.asyncio
    async def test_create_maps_default_required_to_400(self, monkeypatch):
        async def _raise(db, payload):
            raise ModelDefaultRequiredError("列表为空时第一个模型必须设为默认")

        monkeypatch.setattr(models_service, "create_model", _raise)

        with pytest.raises(HTTPException) as exc:
            await models_api.create_model(
                payload=LLMModelCreate(provider_type="openai"), db=object()
            )

        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_update_maps_not_found_to_404(self, monkeypatch):
        async def _raise(db, model_id, payload):
            raise ModelNotFoundError(f"Model {model_id} not found")

        monkeypatch.setattr(models_service, "update_model", _raise)

        with pytest.raises(HTTPException) as exc:
            await models_api.update_model(
                model_id=99, payload=LLMModelUpdate(), db=object()
            )

        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_maps_conflict_to_400(self, monkeypatch):
        async def _raise(db, model_id):
            raise ModelDefaultConflictError("默认模型不可删除")

        monkeypatch.setattr(models_service, "delete_model", _raise)

        with pytest.raises(HTTPException) as exc:
            await models_api.delete_model(model_id=1, db=object())

        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_set_default_maps_not_found_to_404(self, monkeypatch):
        async def _raise(db, model_id):
            raise ModelNotFoundError(f"Model {model_id} not found")

        monkeypatch.setattr(models_service, "set_default_model", _raise)

        with pytest.raises(HTTPException) as exc:
            await models_api.set_default_model(model_id=99, db=object())

        assert exc.value.status_code == 404


class TestSyncRuntimeModel:
    """#69：默认行 → 运行时单例镜像。"""

    @pytest.fixture
    def capture_runtime(self, monkeypatch):
        """拦截 set/reset_runtime_model 并记录最终状态。"""
        from app.services.runtime_config import RuntimeModelConfig

        captured = {"set": [], "reset": 0}

        def _set(config):
            captured["set"].append(config)

        def _reset():
            captured["reset"] += 1
            captured["set"].append(RuntimeModelConfig())

        monkeypatch.setattr(models_service, "set_runtime_model", _set)
        monkeypatch.setattr(models_service, "reset_runtime_model", _reset)
        return captured

    @pytest.mark.asyncio
    async def test_sync_mirrors_default_row(self, capture_runtime):
        default = _model_row(
            1, "anthropic", True, api_key="sk-ant-1", model_name="claude-x"
        )
        db = _FakeDb(specs=[{"rows": [default, _model_row(2, "openai")]}])

        await models_service.sync_runtime_model_from_db(db)

        config = capture_runtime["set"][-1]
        assert config.provider_type == "anthropic"
        assert config.api_key == "sk-ant-1"
        assert config.model_name == "claude-x"

    @pytest.mark.asyncio
    async def test_sync_falls_back_to_first_row_without_default(self, capture_runtime):
        first = _model_row(3, "openai", False, model_name="gpt-first")
        db = _FakeDb(specs=[{"rows": [first]}])

        await models_service.sync_runtime_model_from_db(db)

        config = capture_runtime["set"][-1]
        assert config.provider_type == "openai"
        assert config.model_name == "gpt-first"

    @pytest.mark.asyncio
    async def test_sync_resets_when_no_rows(self, capture_runtime):
        db = _FakeDb(specs=[{"rows": []}])

        await models_service.sync_runtime_model_from_db(db)

        assert capture_runtime["reset"] == 1
        config = capture_runtime["set"][-1]
        assert config.is_configured() is False

    @pytest.mark.asyncio
    async def test_sync_reuses_rows_without_extra_query(self, capture_runtime):
        """调用方已取列表时传入 rows，不应再执行查询。"""
        db = _FakeDb(specs=[])
        default = _model_row(1, "openai", True, model_name="gpt-cached")

        await models_service.sync_runtime_model_from_db(db, rows=[default])

        assert db.statements == []
        assert capture_runtime["set"][-1].model_name == "gpt-cached"


class TestModelsApiSyncsRuntime:
    """#69：CRUD 写路径提交成功后必须同步运行时单例并重建 provider。"""

    def _patch_sync(self, monkeypatch):
        calls = []

        async def _spy(db):
            calls.append(db)

        monkeypatch.setattr(models_api, "_sync_runtime", _spy)
        return calls

    @pytest.mark.asyncio
    async def test_create_syncs_runtime(self, monkeypatch):
        calls = self._patch_sync(monkeypatch)
        created = _model_row(1, "openai", True)
        db = _FakeDb(specs=[{"rows": []}])
        monkeypatch.setattr(
            models_service,
            "create_model",
            AsyncMock(return_value=models_service.to_response(created)),
        )

        await models_api.create_model(
            payload=LLMModelCreate(provider_type="openai", is_default=True), db=db
        )

        assert calls == [db]

    @pytest.mark.asyncio
    async def test_update_syncs_runtime(self, monkeypatch):
        calls = self._patch_sync(monkeypatch)
        row = _model_row(1, "openai", True)
        db = _FakeDb(specs=[])
        monkeypatch.setattr(
            models_service,
            "update_model",
            AsyncMock(return_value=models_service.to_response(row)),
        )

        await models_api.update_model(
            model_id=1, payload=LLMModelUpdate(), db=db
        )

        assert calls == [db]

    @pytest.mark.asyncio
    async def test_delete_syncs_runtime(self, monkeypatch):
        calls = self._patch_sync(monkeypatch)
        db = _FakeDb(specs=[])
        monkeypatch.setattr(models_service, "delete_model", AsyncMock())

        await models_api.delete_model(model_id=1, db=db)

        assert calls == [db]

    @pytest.mark.asyncio
    async def test_set_default_syncs_runtime(self, monkeypatch):
        calls = self._patch_sync(monkeypatch)
        row = _model_row(2, "anthropic", True)
        db = _FakeDb(specs=[])
        monkeypatch.setattr(
            models_service,
            "set_default_model",
            AsyncMock(return_value=models_service.to_response(row)),
        )

        await models_api.set_default_model(model_id=2, db=db)

        assert calls == [db]


class TestFetchProviderModels:
    """#69：后端代理拉取 provider 模型列表。"""

    @pytest.mark.asyncio
    async def test_fetch_openai_models_uses_bearer_auth(self):
        payload = ModelListFetchRequest(
            provider_type="openai",
            base_url="https://api.openai.com/v1/",
            api_key="sk-test",
        )
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("app.services.models.httpx.AsyncClient") as mock_client_class:
            mock_client_class.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)

            models = await models_service.fetch_provider_models(payload)

        assert models == ["gpt-4o", "gpt-4o-mini"]
        # 尾部斜杠剥离 + 追加 /models；api_key 走 Bearer 头。
        assert mock_client.get.call_args.args[0] == "https://api.openai.com/v1/models"
        assert mock_client.get.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-test"

    @pytest.mark.asyncio
    async def test_fetch_anthropic_models_uses_api_key_header(self):
        payload = ModelListFetchRequest(
            provider_type="anthropic",
            base_url="https://api.anthropic.com/v1",
            api_key="sk-ant-test",
        )
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [{"id": "claude-3-5-sonnet-20241022"}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("app.services.models.httpx.AsyncClient") as mock_client_class:
            mock_client_class.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)

            models = await models_service.fetch_provider_models(payload)

        assert models == ["claude-3-5-sonnet-20241022"]
        headers = mock_client.get.call_args.kwargs["headers"]
        assert headers["x-api-key"] == "sk-ant-test"
        assert headers["anthropic-version"] == "2023-06-01"

    @pytest.mark.asyncio
    async def test_fetch_skips_items_without_ids(self):
        payload = ModelListFetchRequest(
            provider_type="openai", base_url="https://api.example.com", api_key="k"
        )
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": [{"object": "model"}, {"id": "m1"}]}
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("app.services.models.httpx.AsyncClient") as mock_client_class:
            mock_client_class.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)

            models = await models_service.fetch_provider_models(payload)

        assert models == ["m1"]

    @pytest.mark.asyncio
    async def test_fetch_maps_http_error_to_502(self, monkeypatch):
        async def _raise(payload):
            raise httpx.ConnectError("upstream down")

        monkeypatch.setattr(models_service, "fetch_provider_models", _raise)

        with pytest.raises(HTTPException) as exc:
            await models_api.fetch_provider_models(
                payload=ModelListFetchRequest(
                    provider_type="openai", base_url="", api_key=""
                )
            )

        assert exc.value.status_code == 502
