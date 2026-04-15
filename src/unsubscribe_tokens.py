"""RFC 8058 one-click unsubscribe token mint/verify.

OWASP A04 (Insecure Design): unsubscribe links embedded in marketing/
transactional emails MUST be cryptographically signed and single-use so an
attacker can't forge them off a stolen user_id + channel_id, and can't
re-consume a captured link against another recipient.

Token shape (compact, URL-safe, opaque):
    base64url(json({user_id, channel_id, category, exp, jti})).hmac_sha256[:32]

The single-use property is enforced at accept time by the caller — they
record `jti` in a dedup table; a repeat submission is a hard reject.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Dict

_UNSUB_KEY = os.getenv("UNSUBSCRIBE_TOKEN_HMAC_KEY", "").strip()
_DEV_ENVS = frozenset({"dev", "local", "test", "development", "testing"})

if not _UNSUB_KEY:
    env = (os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "").strip().lower()
    if env not in _DEV_ENVS:
        raise RuntimeError(
            "UNSUBSCRIBE_TOKEN_HMAC_KEY is required outside dev — "
            "unsubscribe links MUST be signed."
        )
    # In dev we allow an empty key; mint/verify will still work together
    # because both sides use the same (empty) key and every token carries
    # its own `jti`. Log this once at import so ops sees it.
    import logging as _log
    _log.getLogger(__name__).warning(
        "UNSUBSCRIBE_TOKEN_HMAC_KEY unset — running with an empty key "
        "(dev only). Set the env var to test the signed path."
    )

DEFAULT_TTL_SECONDS = 90 * 86400  # 90 days — RFC 8058 allows long-lived


def mint_unsubscribe_token(
    user_id: str, channel_id: str, category: str, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> str:
    """Return an opaque signed token for the given (user, channel, category).

    `jti` is a fresh 96-bit random so every token mint is unique even when
    all other claims repeat — the single-use dedup table keys on `jti`.
    """
    claims = {
        "user_id": str(user_id),
        "channel_id": str(channel_id),
        "category": category,
        "exp": int(time.time()) + int(ttl_seconds),
        "jti": secrets.token_urlsafe(12),
    }
    body = base64.urlsafe_b64encode(
        json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).rstrip(b"=")
    sig = hmac.new(_UNSUB_KEY.encode("utf-8"), body, hashlib.sha256).hexdigest()[:32]
    return f"{body.decode('ascii')}.{sig}"


def verify_unsubscribe_token(token: str) -> Dict[str, str]:
    """Verify a token and return its claims dict.

    Raises ValueError on any failure: malformed, signature mismatch, or
    expired. Single-use enforcement (`jti` dedup) is the caller's job —
    verify_unsubscribe_token ONLY validates cryptographic integrity +
    expiration.
    """
    if not token or "." not in token:
        raise ValueError("malformed_token")
    body_b64, _, sig = token.rpartition(".")
    if not body_b64 or not sig:
        raise ValueError("malformed_token")
    expected = hmac.new(
        _UNSUB_KEY.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256
    ).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):
        raise ValueError("signature_mismatch")
    padded = body_b64 + "=" * ((4 - len(body_b64) % 4) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except Exception as exc:
        raise ValueError("claims_decode_failed") from exc
    if not isinstance(claims, dict):
        raise ValueError("claims_not_dict")
    exp = claims.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        raise ValueError("expired")
    if not claims.get("jti"):
        raise ValueError("jti_missing")
    return claims
