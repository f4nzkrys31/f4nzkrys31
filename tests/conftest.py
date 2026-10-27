import pytest

from alpha_scanner.config import Settings
from alpha_scanner.ingestion import http as http_mod
from alpha_scanner.storage.db import Store


@pytest.fixture(autouse=True)
def _reset_health():
    http_mod.HEALTH.clear()
    yield
    http_mod.HEALTH.clear()


@pytest.fixture
def settings():
    return Settings(db_url="sqlite:///:memory:")


@pytest.fixture
def store():
    return Store("sqlite:///:memory:")
