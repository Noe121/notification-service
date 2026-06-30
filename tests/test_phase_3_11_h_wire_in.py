"""Phase 3.11.H — pin the audit + idempotency wire-in for notification-service.

notification-service emits ``notification.sent`` events from the
orchestrator step 6 (cross-jurisdiction home-school compliance officer
notice).
"""
from __future__ import annotations

import os
from unittest.mock import patch
import sys
from pathlib import Path

# notification-service main.py imports shared.notification_contract at
# module load + requires UNSUBSCRIBE_TOKEN_HMAC_KEY outside dev.
# Path-mangle + env-stub before src.main is imported.
_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault(
    "UNSUBSCRIBE_TOKEN_HMAC_KEY", "test-hmac-key-minimum-32-chars-xxxxxx",
)

import pytest

from src import main as notification_main


class TestIdempotencyBackendSelection:
    def test_build_idempotency_backend_callable(self):
        assert hasattr(notification_main, "_build_idempotency_backend")
        backend = notification_main._build_idempotency_backend()
        assert backend is not None
        cls_name = type(backend).__name__
        assert cls_name in {
            "MysqlIdempotencyBackend",
            "InMemoryIdempotencyBackend",
        }

    def test_fallback_to_in_memory_when_mysql_import_fails(self):
        with patch.dict(
            sys.modules, {"shared.middleware.idempotency_mysql": None}
        ):
            try:
                backend = notification_main._build_idempotency_backend()
                assert type(backend).__name__ == "InMemoryIdempotencyBackend"
            except ImportError:
                pytest.fail(
                    "_build_idempotency_backend should NEVER raise"
                )

    def test_fallback_to_in_memory_when_engine_unavailable(self, monkeypatch):
        def boom():
            raise RuntimeError("db-down")

        monkeypatch.setattr(notification_main.engine, "raw_connection", boom)
        backend = notification_main._build_idempotency_backend()
        assert type(backend).__name__ == "InMemoryIdempotencyBackend"


class TestAuditAppendExposed:
    def test_audit_append_symbol_present(self):
        assert hasattr(notification_main, "_audit_append")
        if notification_main._audit_append is not None:
            assert callable(notification_main._audit_append)
