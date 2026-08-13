"""统一 API 路由入口的架构与契约测试。"""
import ast
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from app.api.router import router
from app.core.config import Settings
from app.main import app
from app.models.schemas import SettingsUpdate
from app.services import settings as settings_service


EXPECTED_METHODS = {
    "/health": {"GET"},
    "/api/documents/upload": {"POST"},
    "/api/documents": {"GET"},
    # #53/#54：单文档 PATCH 编辑与 GET 详情（编辑页按 id 拉取预填数据）
    "/api/documents/{document_id}": {"DELETE", "GET", "PATCH"},
    # #65：重试索引——failed 小说重置 pending 并重新入队
    "/api/documents/{document_id}/reindex": {"POST"},
    # #47：封面静态资源端点
    "/api/covers/{filename}": {"GET"},
    "/api/chat": {"POST"},
    "/api/chat/stream": {"POST"},
    # #36：全量 /api/chat/history 废除，统一走 /api/conversations/{id}/messages
    "/api/conversations": {"GET", "POST"},
    "/api/conversations/{conversation_id}": {"DELETE", "PATCH"},
    "/api/conversations/{conversation_id}/messages": {"GET"},
    "/api/settings": {"GET", "PUT"},
    # #45：聊天页 preflight 用的 LLM 可用性端点
    "/api/llm/status": {"GET"},
    # #68：模型列表 CRUD 五端点
    "/api/models": {"GET", "POST"},
    "/api/models/{model_id}": {"DELETE", "PUT"},
    "/api/models/{model_id}/default": {"PUT"},
}


def _openapi_methods(application: FastAPI) -> dict[str, set[str]]:
    return {
        path: set(methods)
        for path, methods in application.openapi()["paths"].items()
    }


def _imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def test_unified_router_exposes_existing_endpoints():
    test_app = FastAPI()
    test_app.include_router(router)
    routes = _openapi_methods(test_app)
    for path, methods in EXPECTED_METHODS.items():
        assert routes.get(path, set()) >= {method.lower() for method in methods}


def test_application_mounts_unified_router():
    routes = _openapi_methods(app)
    for path, methods in EXPECTED_METHODS.items():
        assert routes.get(path, set()) >= {method.lower() for method in methods}


def test_router_module_contains_only_routing_dependencies():
    router_path = Path(__file__).parents[1] / "app" / "api" / "router.py"
    imports = _imported_modules(router_path.read_text(encoding="utf-8"))
    forbidden = {
        "aiofiles",
        "json",
        "os",
        "pymilvus",
        "sse_starlette.sse",
        "sqlalchemy",
        "app.core.database",
        "app.services.llm",
        "app.services.rag",
        "app.services.vector_store",
    }
    assert not imports.intersection(forbidden)


def test_main_only_mounts_the_unified_router():
    """main.py 仅包含一个 include_router 调用，且未直接定义端点装饰器。"""
    main_path = Path(__file__).parents[1] / "app" / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))

    include_router_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "include_router"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "app"
    ]
    assert len(include_router_calls) == 1
    assert ast.unparse(include_router_calls[0].args[0]) == "router"

    # main.py 不应在模块顶层定义 @app.get/@app.post 等端点装饰器
    route_decorators = [
        node.decorator for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for decorator in node.decorator_list
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.value.id == "app"  # type: ignore[union-attr]
            and decorator.func.attr.startswith(("get", "post", "put", "delete", "patch"))
        )
    ]
    assert route_decorators == []


def test_settings_service_reads_selected_provider(monkeypatch):
    settings = Settings(
        llm_provider="anthropic",
        anthropic_api_key="abcdefgh1234",
        anthropic_base_url="https://anthropic.example",
        anthropic_model="claude-test",
    )
    monkeypatch.setattr(settings_service, "get_settings", lambda: settings)

    config = settings_service.get_llm_config()

    assert config.api_key_masked == "abcd...1234"
    assert config.base_url == "https://anthropic.example"
    assert config.model == "claude-test"


class _FakeSettingsDb:
    """settings 服务所需的最小假 db Session（#68 队列式：按序弹出结果）。"""

    def __init__(self, specs=None):
        self._specs = list(specs or [])
        self.executed = []
        self.commits = 0
        self.added = []

    async def execute(self, statement):
        self.executed.append(statement)
        spec = self._specs.pop(0) if self._specs else {}
        result = MagicMock()
        result.scalar_one_or_none.return_value = spec.get("scalar")
        result.scalars.return_value.all.return_value = spec.get("rows", [])
        return result

    def add(self, row):
        if row.id is None:
            row.id = len(self.added) + 1
        if row.created_at is None:
            row.created_at = datetime(2026, 8, 1)
        if row.updated_at is None:
            row.updated_at = datetime(2026, 8, 1)
        self.added.append(row)

    async def commit(self):
        self.commits += 1

    async def refresh(self, row):
        return None


@pytest.mark.asyncio
async def test_settings_service_updates_selected_provider(monkeypatch):
    """#68：显式切换 provider 且无对应记录 → 新建默认记录并同步内存。"""
    from app.models.llm_model import LLMModel

    settings = Settings(llm_provider="openai", anthropic_api_key="old-key")
    reset_calls: list[bool] = []
    monkeypatch.setattr(settings_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        settings_service, "reset_providers", lambda: reset_calls.append(True)
    )
    post = LLMModel(
        id=1,
        provider_type="anthropic",
        api_key="new-secret",
        base_url="",
        model_name="new-model",
        is_default=True,
        created_at=None,
        updated_at=None,
    )
    db = _FakeSettingsDb(
        specs=[
            {"rows": []},  # update 前列表
            {"rows": []},  # create_model 内空列表校验
            {},  # create_model 内加锁
            {},  # create_model 内降级
            {"rows": [post]},  # load 重载
        ]
    )
    update = SettingsUpdate(
        llm_provider="anthropic", llm_api_key="new-secret", llm_model="new-model"
    )

    response = await settings_service.update_llm_settings(db=db, update=update)

    assert settings.llm_provider == "anthropic"
    assert settings.anthropic_api_key == "new-secret"
    assert settings.anthropic_model == "new-model"
    assert response.settings.llm.provider == "anthropic"
    assert response.message == "Settings updated and providers reinitialized"
    assert reset_calls == [True]
    assert len(db.added) == 1
    assert db.added[0].provider_type == "anthropic"
    assert db.commits >= 1
