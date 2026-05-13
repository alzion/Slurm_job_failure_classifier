import os
import pytest

# Point SCENARIO_DIR at the real scenarios before any imports
os.environ.setdefault(
    "SCENARIO_DIR",
    os.path.join(os.path.dirname(__file__), "..", "backend", "scenarios"),
)
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:///./test_simulator.db",
)

from fastapi.testclient import TestClient
from backend.main import app
from backend.models import init_db
from backend.incidents import load_scenario

# Create tables once at collection time (SQLite in-memory equivalent via file)
init_db()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def thermal_scenario():
    return load_scenario("02_thermal_throttle")


@pytest.fixture
def session_factory(client):
    def make(email="test@example.com"):
        r = client.post(
            "/api/v1/sessions",
            headers={"X-Auth-Request-Email": email},
        )
        assert r.status_code == 200
        return r.json()["session_id"]
    return make
