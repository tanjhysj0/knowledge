"""统一 API 路由入口的架构与契约测试。"""
import ast
from pathlib import Path

from fastapi import FastAPI

from app.api.router import router
from app.main import app


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
    # #76：聊天端点迁移至 v1（旧 /api/chat 路径下线）
    "/api/v1/chat": {"POST"},
    "/api/v1/chat/stream": {"POST"},
    # #77：v2 聊天端点（接入层固定传入子集检索策略白名单，契约与 v1 一致）
    "/api/v2/chat": {"POST"},
    "/api/v2/chat/stream": {"POST"},
    # #36：全量 /api/chat/history 废除，统一走 /api/conversations/{id}/messages
    "/api/conversations": {"GET", "POST"},
    "/api/conversations/{conversation_id}": {"DELETE", "PATCH"},
    "/api/conversations/{conversation_id}/messages": {"GET"},
    # #45：聊天页 preflight 用的 LLM 可用性端点
    "/api/llm/status": {"GET"},
    # #68：模型列表 CRUD 五端点
    "/api/models": {"GET", "POST"},
    # #69：模型列表拉取代理
    "/api/models/fetch": {"POST"},
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


def test_legacy_chat_paths_removed():
    """#76：旧 ``/api/chat`` 与 ``/api/chat/stream`` 路径不再提供。"""
    test_app = FastAPI()
    test_app.include_router(router)
    routes = _openapi_methods(test_app)
    assert "/api/chat" not in routes
    assert "/api/chat/stream" not in routes


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
        "pgvector",
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
