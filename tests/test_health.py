"""基础健康检查与配置测试。"""

from fastapi.testclient import TestClient

from app.main import app
from config import AVAILABLE_MODELS, Config, get_model_settings


client = TestClient(app)


def test_health_endpoint() -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["app"] == Config.app_name


def test_root_endpoint() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["docs"] == "/docs"


def test_default_model_registered() -> None:
    assert Config.model in AVAILABLE_MODELS
    assert AVAILABLE_MODELS[Config.model]["default"] is True


def test_model_provider_routing() -> None:
    base_url, _ = get_model_settings("qwen-plus")
    assert base_url == Config.dashscope_base_url
    base_url, _ = get_model_settings("deepseek-chat")
    assert base_url == Config.deepseek_base_url
    base_url, _ = get_model_settings("unknown-model")
    assert base_url == Config.deepseek_base_url


def test_mysql_url_contains_database() -> None:
    url = Config.mysql_url(async_driver=True)
    assert url.startswith("mysql+aiomysql://")
    assert Config.database in url
