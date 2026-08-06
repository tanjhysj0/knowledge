"""统一 API 路由入口的架构与契约测试。"""
import ast
from pathlib import Path

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
    "/api/documents/{document_id}": {"DELETE"},
    "/api/chat": {"POST"},
    "/api/chat/stream": {"POST"},
    "/api/chat/history": {"GET"},
    "/api/settings": {"GET", "PUT"},
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


def test_settings_service_updates_selected_provider(monkeypatch):
    settings = Settings(llm_provider="openai", anthropic_api_key="old-key")
    reset_calls: list[bool] = []
    monkeypatch.setattr(settings_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        settings_service, "reset_providers", lambda: reset_calls.append(True)
    )
    update = SettingsUpdate(
        llm_provider="anthropic", llm_api_key="new-secret", llm_model="new-model"
    )

    response = settings_service.update_llm_settings(update)

    assert settings.anthropic_api_key == "new-secret"
    assert settings.anthropic_model == "new-model"
    assert response.settings.llm.provider == "anthropic"
    assert response.message == "Settings updated and providers reinitialized"
    assert reset_calls == [True]
