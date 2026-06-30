"""Phase 3.11 — notification-service accepts HMAC S2S tokens.

The orchestration service client sends signed ``X-Service-Token``
headers. notification-service must continue to accept the legacy
INTERNAL_SERVICE_TOKEN while also accepting HMAC tokens from explicitly
allowed issuers.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ.setdefault("SERVICE_TOKEN_SECRET", "test-deterministic-secret")

_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from shared import s2s_auth  # noqa: E402


class _FakeRequest:
    pass


@pytest.fixture
def auth_module(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "legacy-static-token")
    monkeypatch.setenv("SERVICE_TOKEN_SECRET", "test-deterministic-secret")
    monkeypatch.setenv(
        "SERVICE_TOKEN_ALLOWED_CALLERS",
        "admin-dashboard-service",
    )
    import src.auth as auth
    return importlib.reload(auth)


def test_hmac_token_from_admin_dashboard_accepted(auth_module):
    token = s2s_auth.make_token("admin-dashboard-service")

    actor = auth_module.require_bearer_actor(
        request=_FakeRequest(),
        creds=None,
        x_service_token=token,
    )

    assert actor["role"] == "service"
    assert actor["auth_mode"] == "service_token"
    assert actor["service_name"] == "admin-dashboard-service"
    assert actor["issuer"] == "admin-dashboard-service"


def test_hmac_token_from_disallowed_issuer_rejected(auth_module):
    token = s2s_auth.make_token("crm-service")

    with pytest.raises(HTTPException) as exc:
        auth_module.require_bearer_actor(
            request=_FakeRequest(),
            creds=None,
            x_service_token=token,
        )

    assert exc.value.status_code == 401


def test_legacy_static_token_still_accepted(auth_module):
    actor = auth_module.require_bearer_actor(
        request=_FakeRequest(),
        creds=None,
        x_service_token="legacy-static-token",
    )

    assert actor["role"] == "service"
    assert actor["auth_mode"] == "service_token"

