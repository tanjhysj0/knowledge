"""#45 ``GET /api/llm/status`` 端点的契约与分支测试。

#69：读取源切换为运行时默认模型单例，测试改为 patch
``get_runtime_model`` 注入假运行时配置。
"""
from fastapi.testclient import TestClient

from app.main import app
from app.services.runtime_config import RuntimeModelConfig


client = TestClient(app)


def _patch_runtime_model(monkeypatch, **fields):
    """Patch ``app.services.llm`` 与 ``app.api.llm_status`` 的
    ``get_runtime_model``，让端点与 preflight 读到同一份假运行时配置。"""
    instance = RuntimeModelConfig(**fields)
    factory = lambda: instance
    monkeypatch.setattr("app.services.llm.get_runtime_model", factory)
    monkeypatch.setattr("app.api.llm_status.get_runtime_model", factory)
    return instance


def test_status_reports_unconfigured_when_api_key_missing(monkeypatch):
    """默认模型 + 空 api_key → ``configured=false`` + reason。"""
    _patch_runtime_model(
        monkeypatch, provider_type="openai", api_key="", model_name="gpt-x"
    )

    response = client.get("/api/llm/status")

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "openai"
    assert body["configured"] is False
    assert "OpenAI" in body["reason"]
    assert "API Key" in body["reason"]


def test_status_reports_configured_when_default_ready(monkeypatch):
    _patch_runtime_model(
        monkeypatch,
        provider_type="anthropic",
        api_key="sk-ant-valid",
        model_name="claude-3-5-sonnet",
    )

    response = client.get("/api/llm/status")

    assert response.status_code == 200
    body = response.json()
    assert body == {"provider": "anthropic", "configured": True, "reason": ""}


def test_status_reports_unconfigured_when_model_missing(monkeypatch):
    _patch_runtime_model(
        monkeypatch,
        provider_type="anthropic",
        api_key="sk-ant-valid",
        model_name="",
    )

    response = client.get("/api/llm/status")

    body = response.json()
    assert body["configured"] is False
    assert "Model" in body["reason"]


def test_status_reports_unconfigured_when_runtime_empty(monkeypatch):
    """#69：无默认模型（运行时为空态）→ ``configured=false``。"""
    _patch_runtime_model(monkeypatch)

    response = client.get("/api/llm/status")

    body = response.json()
    assert body["configured"] is False
    assert body["provider"] == "openai"
