"""Day-6 finding 4: `response.json()` body-read hardening.

Before this fix, `_validate_bearer_via_auth_service` set `timeout=5`
on the POST but called `response.json()` with no timeout and no
exception handling. A slow / malformed / chunked JSON body from
auth-service would hang the FastAPI request thread indefinitely and
exhaust the ALB connection pool — observed during the 2026-04-16
Day-6 Batch-1 e2e aggregate run right after services scaled from 0→1.

OWASP / PII hardening pinned here:
  - Malformed body → fail-closed 503 (not a hang / not silent success).
  - Non-dict payload → 401 (rejects unexpected JSON-list / scalar).
  - Exception path must NOT log `str(exc)` (which can echo body
    fragments containing email / token / claim material). Only the
    exception class name is logged, matching the `_opaque_5xx`
    convention used by crm-service.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
import requests
from fastapi import HTTPException


def _fake_response(status_code: int, json_side_effect=None, json_return=None):
    r = MagicMock()
    r.status_code = status_code
    if json_side_effect is not None:
        r.json.side_effect = json_side_effect
    else:
        r.json.return_value = json_return
    return r


class TestValidateBearerBodyHardening:
    def test_raises_503_on_malformed_json(self, monkeypatch):
        """A JSONDecodeError from response.json() must surface as 503."""
        import src.auth as auth
        monkeypatch.setattr(
            auth.requests, "post",
            lambda *a, **kw: _fake_response(
                200, json_side_effect=ValueError("malformed"),
            ),
        )
        with pytest.raises(HTTPException) as excinfo:
            auth._validate_bearer_via_auth_service("token-xxx")
        assert excinfo.value.status_code == 503
        assert "unavailable" in excinfo.value.detail.lower()

    def test_raises_503_on_chunked_encoding_error(self, monkeypatch):
        """A requests.RequestException from .json() (slow/broken body)
        must also fail-closed to 503, not hang."""
        import src.auth as auth
        monkeypatch.setattr(
            auth.requests, "post",
            lambda *a, **kw: _fake_response(
                200,
                json_side_effect=requests.exceptions.ChunkedEncodingError(
                    "truncated"
                ),
            ),
        )
        with pytest.raises(HTTPException) as excinfo:
            auth._validate_bearer_via_auth_service("token-xxx")
        assert excinfo.value.status_code == 503

    def test_raises_401_on_non_dict_payload(self, monkeypatch):
        """A JSON list/scalar (instead of the expected dict) must 401.
        Guards against a downstream crash on `.get()` if auth-service
        ever returns an unexpected shape."""
        import src.auth as auth
        monkeypatch.setattr(
            auth.requests, "post",
            lambda *a, **kw: _fake_response(200, json_return=["not", "a", "dict"]),
        )
        with pytest.raises(HTTPException) as excinfo:
            auth._validate_bearer_via_auth_service("token-xxx")
        assert excinfo.value.status_code == 401

    def test_does_not_log_exc_detail_on_malformed_body(self, monkeypatch, caplog):
        """PII hardening: exception detail must NOT reach the logger.
        Only the exception class name should appear. This matches
        crm-service's `_opaque_*` pattern and keeps the DLP log filter
        from having to scrub auth-body fragments."""
        import src.auth as auth
        sentinel = "LEAK_email=victim@example.com_token=aaa.bbb.ccc"
        monkeypatch.setattr(
            auth.requests, "post",
            lambda *a, **kw: _fake_response(
                200, json_side_effect=ValueError(sentinel),
            ),
        )
        with caplog.at_level(logging.ERROR, logger="src.auth"):
            with pytest.raises(HTTPException):
                auth._validate_bearer_via_auth_service("token-xxx")
        joined = " ".join(rec.getMessage() for rec in caplog.records)
        assert sentinel not in joined, (
            f"Exception detail leaked into logs: {joined!r}"
        )
        # The class name ('ValueError') IS acceptable — it carries no PII.
        assert "ValueError" in joined

    def test_happy_path_still_returns_actor_dict(self, monkeypatch):
        """Regression guard: the hardening MUST NOT break the 200-path."""
        import src.auth as auth
        monkeypatch.setattr(
            auth.requests, "post",
            lambda *a, **kw: _fake_response(
                200,
                json_return={
                    "user_id": 88,
                    "canonical_role": "fan",
                    "email": "fan@dev.nilbx.com",
                    "permissions": [],
                },
            ),
        )
        actor = auth._validate_bearer_via_auth_service("token-xxx")
        assert actor["user_id"] == 88
        assert actor["role"] == "fan"
        assert actor["auth_mode"] == "bearer"
