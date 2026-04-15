"""Constant-time service-token comparison (Phase-4 audit review round).

Pins that notification-service/src/auth.py compares the inbound
``X-Service-Token`` with :func:`hmac.compare_digest`, not Python's
``==`` operator. The latter short-circuits on the first mismatching
byte, producing a timing side-channel that an attacker can use to
enumerate the token byte-by-byte from an external network.

We can't measure real timing variance deterministically in a CI test,
so we use a structural assertion: the auth module must reference
``hmac.compare_digest`` in its token-compare path, and the live
behavior (accept matching, reject non-matching) must still be
preserved.
"""
from __future__ import annotations

import inspect

import pytest


def test_auth_module_uses_hmac_compare_digest_for_service_token():
    """The bypass-path source must use compare_digest, not ==."""
    import src.auth as auth
    src = inspect.getsource(auth)
    # Narrow to the service-token bypass block to avoid false positives
    # from other == uses elsewhere in the file.
    needle = "_INTERNAL_SERVICE_TOKEN"
    assert needle in src
    # Anywhere the two sides of the compare appear together, the
    # compare must go through hmac.compare_digest. String equality
    # on these two operands is the regression we're blocking.
    bad = "x_service_token == _INTERNAL_SERVICE_TOKEN"
    worse = "_INTERNAL_SERVICE_TOKEN == x_service_token"
    assert bad not in src, (
        f"notification-service/src/auth.py must not use `==` to compare "
        f"service tokens; use hmac.compare_digest. Found: {bad!r}"
    )
    assert worse not in src
    assert "hmac.compare_digest" in src, (
        "notification-service/src/auth.py must import/use hmac.compare_digest"
    )


def test_service_token_bypass_still_accepts_matching_token(monkeypatch):
    """Behavioural parity: constant-time compare must still return True
    for the correct token."""
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "super-secret-42-characters-minimum-length-token")
    import importlib, src.auth as auth
    importlib.reload(auth)

    class _FakeRequest: pass
    actor = auth.require_bearer_actor(
        request=_FakeRequest(),
        creds=None,
        x_service_token="super-secret-42-characters-minimum-length-token",
    )
    assert actor["role"] == "service"


def test_service_token_bypass_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "super-secret-42-characters-minimum-length-token")
    import importlib, src.auth as auth
    importlib.reload(auth)

    class _FakeRequest: pass
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as excinfo:
        auth.require_bearer_actor(
            request=_FakeRequest(),
            creds=None,
            x_service_token="wrong-token",
        )
    assert excinfo.value.status_code == 401


def test_service_token_bypass_rejects_empty_token_from_both_sides(monkeypatch):
    """Empty _INTERNAL_SERVICE_TOKEN must not silently accept any inbound
    token. Without a length check, compare_digest on two empty strings
    returns True — the existing ``if _INTERNAL_SERVICE_TOKEN`` guard is
    what prevents that. Pin the guard here."""
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "")
    import importlib, src.auth as auth
    importlib.reload(auth)

    class _FakeRequest: pass
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as excinfo:
        auth.require_bearer_actor(
            request=_FakeRequest(),
            creds=None,
            x_service_token="",  # empty would equal empty
        )
    assert excinfo.value.status_code == 401
