"""Inbound event signature verification for notification-service.

OWASP A08: payment-service, deliverable-service, live-commerce-service, and
live-streaming-service all sign their SNS events. This module verifies
signatures BEFORE any notification is dispatched — an unverified event is
dropped + logged + metric-emitted + deleted from the queue.

Canonical form (must match every sender's canonical form byte-for-byte):
    to_sign = {k: v for k, v in payload.items() if k not in
               {"signature", "signature_alg", "signature_issued_at"}}
    canonical = json.dumps(to_sign, sort_keys=True, separators=(",",":"), default=str)
    sig = hmac.new(key.encode(), canonical.encode(), hashlib.sha256).hexdigest()
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_DEV_ENVS = frozenset({"dev", "local", "test", "development", "testing"})
_MAX_AGE_SECONDS = 300  # 5-minute replay window


def _is_dev_environment() -> bool:
    env = (os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "").strip().lower()
    return env in _DEV_ENVS


def signature_required() -> bool:
    """Return True when signature verification must be enforced.

    Default: enforced outside dev environments. Env
    ``NOTIFICATION_EVENT_SIGNATURE_REQUIRED`` can be set to ``"false"`` in
    dev to opt out; outside dev it is always required.
    """
    override = (os.getenv("NOTIFICATION_EVENT_SIGNATURE_REQUIRED") or "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        # Only honored in dev.
        return not _is_dev_environment()
    # Unset → enforced unless dev
    return not _is_dev_environment()


class EventSignatureError(Exception):
    """Raised when an inbound event signature is missing, expired, or invalid."""


def require_event_hmac_key(var_name: str) -> str:
    """Load and validate an HMAC key; boot-fail outside dev if missing."""
    key = (os.getenv(var_name) or "").strip()
    if not key:
        if _is_dev_environment():
            logger.warning(
                "%s is unset — event signature verification will fail closed "
                "in dev. Set the env var to test the verified path.",
                var_name,
            )
            return ""
        raise RuntimeError(
            f"{var_name} is required outside dev — upstream services sign "
            f"every event and notification-service MUST verify."
        )
    return key


def verify_signed_event(
    payload: dict,
    key: str,
    *,
    source: str = "unknown",
    max_age_seconds: int = _MAX_AGE_SECONDS,
) -> None:
    """Verify a signed event; raise EventSignatureError on any failure."""
    if not isinstance(payload, dict):
        raise EventSignatureError("event_not_dict")
    if not key:
        # Only reachable when dev skipped the env; still fail closed.
        raise EventSignatureError("event_key_unset")

    sig = payload.get("signature")
    alg = payload.get("signature_alg")
    issued_at = payload.get("signature_issued_at")

    if not sig:
        raise EventSignatureError("signature_missing")
    if alg and str(alg).upper() not in {"HMAC-SHA256", "SHA256"}:
        raise EventSignatureError("alg_not_allowed")

    if issued_at:
        try:
            # Accept both `Z` and `+00:00` suffixes
            ts_str = str(issued_at).replace("Z", "+00:00")
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            if age > max_age_seconds or age < -30:  # allow 30s clock-skew future
                raise EventSignatureError("signature_expired")
        except EventSignatureError:
            raise
        except Exception as exc:
            raise EventSignatureError("signature_issued_at_malformed") from exc

    to_sign = {
        k: v
        for k, v in payload.items()
        if k not in {"signature", "signature_alg", "signature_issued_at"}
    }
    canonical = json.dumps(to_sign, sort_keys=True, separators=(",", ":"), default=str)
    expected = hmac.new(
        key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(str(sig), expected):
        raise EventSignatureError("signature_mismatch")
