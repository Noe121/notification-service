"""OWASP Phase 1-3 §A05 #1: _build_db_url fail-closed in prod.

Before: missing DB_PASSWORD silently fell back to SQLite even in prod.
After: RuntimeError at import/build time for prod/staging.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path("/Users/nicolasvalladares/NIL")
# Tests import the notification-service ``src.main`` which in turn
# imports ``shared.notification_contract``. Ensure shared/ is on
# sys.path before the test tries to import main.
_SHARED_PATH = str(REPO_ROOT)
if _SHARED_PATH not in sys.path:
    sys.path.insert(0, _SHARED_PATH)


class TestBuildDbUrlFailsClosedInProd:
    """Import the module ONCE under safe env, then call the builder
    directly with monkeypatched env so the import-time evaluation of
    ``DATABASE_URL = ... or _build_db_url()`` can't fire the exception
    during test collection."""

    @pytest.fixture(scope="class")
    def nm_module(self):
        # Import-safe env: DB_PASSWORD present, ENVIRONMENT=dev so all
        # downstream module-level ``RuntimeError`` guards stay quiet.
        os.environ.setdefault("DB_PASSWORD", "import-safe-password")
        os.environ.setdefault("UNSUBSCRIBE_TOKEN_HMAC_KEY", "import-safe-key-123")
        os.environ["ENVIRONMENT"] = "development"
        sys.path.insert(0, str(REPO_ROOT / "notification-service"))
        try:
            nm = importlib.import_module("src.main")
            return nm
        finally:
            sys.path.pop(0)

    def test_missing_password_raises_in_production(
        self, nm_module, monkeypatch,
    ):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("DB_PASSWORD", raising=False)
        with pytest.raises(RuntimeError, match="DB_PASSWORD"):
            nm_module._build_db_url()

    def test_missing_password_falls_back_to_sqlite_in_dev(
        self, nm_module, monkeypatch,
    ):
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.delenv("DB_PASSWORD", raising=False)
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            url = nm_module._build_db_url()
        assert url.startswith("sqlite://")
        assert any("DB_PASSWORD" in str(ww.message) for ww in w)
