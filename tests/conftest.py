import pytest
from fastapi.testclient import TestClient

from pwb import contract
from pwb.api import create_app
from pwb.config import CONTRACT_DIR, Settings
from pwb.seed import seed_examples

API = "/api/v1"


def assert_contract(body, kind=None):
    """返回体必须符合约定里对应 kind 的 schema。"""
    if kind:
        assert body["kind"] == kind, body
    errs = contract.errors(body["kind"], body)
    assert not errs, errs
    return body


@pytest.fixture(autouse=True)
def no_openalex(monkeypatch):
    """测试里不联网查 OpenAlex；要用的测试自己换成假的。"""
    from pwb import authors

    def offline(path, params):
        raise RuntimeError("测试里不联网")
    monkeypatch.setattr(authors, "_oa", offline)


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", token="", start_worker=False)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def store(app):
    return app.state.store


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def seeded(store):
    seed_examples(store, CONTRACT_DIR / "examples", log=lambda _: None)
    return store
