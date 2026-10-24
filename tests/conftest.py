import os

# Force mock mode and a manually driven worker before the app is imported.
for name in ("MOCK_SOLARI", "MOCK_EXA", "MOCK_GEMINI", "MOCK_TERAC", "MOCK_WHOP"):
    os.environ[name] = "1"
os.environ["WORKER_ENABLED"] = "0"
os.environ["HUMAN_TEST_ENABLED"] = "1"  # hidden on the live site, but its flow is still tested

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config, main, urlguard  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    # Treat every test domain as a public address so tests don't need the network.
    monkeypatch.setattr(urlguard, "resolve_host", lambda host: ["93.184.215.14"])
    with TestClient(main.app) as c:
        yield c
