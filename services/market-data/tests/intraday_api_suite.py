"""HTTP contract of /stocks/intraday/* (design §11): admin-only, required mode, US-only, schema
status, generation disabled. The admin dependency and the DB session are replaced at the module
boundary (the real auth module needs Redis/JWT settings); the query layer is real, on SQLite.
Run by test_intraday_store.py with --noconftest.
"""
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "shared"), str(ROOT / "services" / "market-data")]

import pytest  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

ENGINE = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})


def _get_session():
    with Session(ENGINE) as s:
        yield s


def _admin_required():
    raise HTTPException(401, "Not authenticated")


sys.modules["db"] = types.SimpleNamespace(get_session=_get_session)
_auth = types.ModuleType("src.api.auth")
_auth.get_admin_user = _admin_required
import src.api  # noqa: E402,F401
sys.modules["src.api.auth"] = _auth

from src.api import intraday as api  # noqa: E402
from src.services.intraday import schema  # noqa: E402

with ENGINE.begin() as _c:
    schema.create(_c)

APP = FastAPI()
APP.include_router(api.router)


@pytest.fixture
def client():
    schema.SCHEMA_STATUS = "ok"
    APP.dependency_overrides.clear()
    APP.dependency_overrides[_admin_required] = lambda: "admin"
    yield TestClient(APP)
    APP.dependency_overrides.clear()


Q = "/stocks/intraday/identities?session=2026-10-28"


def test_missing_admin_is_refused():
    schema.SCHEMA_STATUS = "ok"
    APP.dependency_overrides.clear()
    assert TestClient(APP).get(Q + "&mode=observed").status_code == 401


def test_mode_is_required_and_validated(client):
    assert client.get(Q).status_code == 422
    r = client.get(Q + "&mode=replay")
    assert r.status_code == 422 and r.json()["detail"]["error"] == "bad_request"


def test_hk_is_refused(client):
    r = client.get(Q + "&mode=observed&market=HK")
    assert r.status_code == 422 and r.json()["detail"]["error"] == "unsupported_market"


def test_schema_failure_is_reported_not_hidden(client):
    schema.SCHEMA_STATUS = "failed: boom"
    r = client.get(Q + "&mode=observed")
    assert r.status_code == 503 and r.json()["detail"]["error"] == "schema_unavailable"


def test_ok_envelope_says_generation_disabled(client):
    r = client.get(Q + "&mode=observed")
    assert r.status_code == 200
    assert r.json()["production_generation"] == "disabled" and r.json()["identities"] == []
    assert client.get("/stocks/intraday/setups?session=2026-10-28&mode=observed").status_code == 422  # no identity
