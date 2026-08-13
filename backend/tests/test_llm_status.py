"""#45 ``GET /api/llm/status`` 端点的契约与分支测试。"""
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _patch_llm_get_settings(monkeypatch, **fields):
    """Patch ``app.services.llm.get_settings`` with a Settings built from ``fields``.

    The endpoint (``app.api.llm_status``) and the service (``app.services.llm``)
    each hold their own import binding to ``get_settings``, so both must be
    patched for a single endpoint call to see the same injected settings.
    """
    from app.core.config import Settings

    defaults = dict(
        llm_provider="openai",
        openai_api_key="sk-valid-key",
        openai_model="gpt-4o-mini",
        anthropic_api_key="sk-ant-valid",
        anthropic_model="claude-3-5-sonnet",
    )
    defaults.update(fields)
    instance = Settings(**defaults)
    factory = lambda: instance
    monkeypatch.setattr("app.services.llm.get_settings", factory)
    monkeypatch.setattr("app.api.llm_status.get_settings", factory)
    return instance


def test_status_reports_unconfigured_when_openai_key_missing(monkeypatch):
    """OpenAI provider + 空 api_key → ``configured=false`` + reason。"""
    _patch_llm_get_settings(
        monkeypatch, llm_provider="openai", openai_api_key="", openai_model="gpt-x"
    )

    response = client.get("/api/llm/status")

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "openai"
    assert body["configured"] is False
    assert "OpenAI" in body["reason"]
    assert "API Key" in body["reason"]


def test_status_reports_configured_when_provider_ready(monkeypatch):
    _patch_llm_get_settings(
        monkeypatch,
        llm_provider="anthropic",
        anthropic_api_key="sk-ant-valid",
        anthropic_model="claude-3-5-sonnet",
    )

    response = client.get("/api/llm/status")

    assert response.status_code == 200
    body = response.json()
    assert body == {"provider": "anthropic", "configured": True, "reason": ""}


def test_status_reports_unconfigured_when_anthropic_model_missing(monkeypatch):
    _patch_llm_get_settings(
        monkeypatch,
        llm_provider="anthropic",
        anthropic_api_key="sk-ant-valid",
        anthropic_model="",
    )

    response = client.get("/api/llm/status")

    body = response.json()
    assert body["configured"] is False
    assert "Model" in body["reason"]