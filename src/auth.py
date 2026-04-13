"""
Auth gate for notification-service.

Before this module landed (2026-04-11), notification-service was the only
service mounted on the public ALB with NO authentication on any data route.
Anyone with network reach to https://dev.nilbx.com/api/notifications/* could
read every user's notification history, register channels, send messages,
etc. The CRM service has the same problem and is tracked separately.

This module mirrors the pattern used by payment-service:
  - `_require_bearer_actor` validates a Bearer token against
    auth-service's POST /validate-token and returns an `actor` dict
  - `require_self_or_admin` lets users only access their own data
  - `require_admin` gates platform-wide management routes
    (templates / batches / delivery worker)
  - `require_internal_service_token` lets a worker / cron job bypass the
    user-auth gate by sending an `X-Service-Token` header that matches
    the `INTERNAL_SERVICE_TOKEN` env var. Used by the data-sync /
    live-stream-notification consumers.

The dependency uses the **same auth-service URL convention** as
payment-service: `AUTH_SERVICE_URL` env var, defaulting to
`http://auth-service.dev.local:9000` so the dev VPC service-discovery
hostname resolves out of the box.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Set

import requests
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

AUTH_SERVICE_URL = os.getenv(
    "AUTH_SERVICE_URL",
    "http://auth-service.dev.local:9000",
).rstrip("/")

# Internal service-to-service token. When the caller sends
# `X-Service-Token: <this value>` AND the env var is non-empty, the user
# auth gate is bypassed and the actor is reported as `service`. Used by the
# SQS data-sync consumer + live-stream notification consumer that run
# inside the same VPC.
_INTERNAL_SERVICE_TOKEN = os.getenv("INTERNAL_SERVICE_TOKEN", "").strip()

# Roles that get blanket admin access to every notification route.
# Mirrors the bypass set used by payment-service after the platform_admin
# canonical-role rename.
ADMIN_BYPASS_ROLES: Set[str] = {
    "admin",
    "platform_admin",
    "platform_ops",
    "platform_system_admin",
    "platform_support_admin",
    "super_admin",
    "nilbx_admin",
    "service",  # internal service token caller
}

_bearer_scheme = HTTPBearer(auto_error=False)


def _canonicalize_role(role: Any) -> str:
    return str(role or "").strip().lower()


def _validate_bearer_via_auth_service(token: str) -> Dict[str, Any]:
    """Round-trip the bearer token through auth-service's /validate-token.

    Returns the actor dict on success. Raises 401 on invalid token, 503 if
    the auth-service is unreachable.
    """
    try:
        response = requests.post(
            f"{AUTH_SERVICE_URL}/validate-token",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        logger.error("notification-service auth validation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from exc

    if response.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
        )

    payload = response.json()
    raw_user_id = payload.get("user_id") or payload.get("id")
    user_id: Optional[int] = None
    if raw_user_id is not None:
        try:
            user_id = int(raw_user_id)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid bearer token subject",
            )

    role = _canonicalize_role(payload.get("canonical_role") or payload.get("role"))
    return {
        "user_id": user_id,
        "role": role,
        "canonical_role": role,
        "email": payload.get("email"),
        "permissions": payload.get("permissions") or [],
        "auth_mode": "bearer",
    }


def require_bearer_actor(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    x_service_token: Optional[str] = Header(default=None, alias="X-Service-Token"),
) -> Dict[str, Any]:
    """Resolve an actor from either:
      1. An internal X-Service-Token (matches INTERNAL_SERVICE_TOKEN env)
      2. A Bearer JWT validated by auth-service /validate-token

    Raises 401 if neither is present or valid.
    """
    # Internal service token bypass
    if _INTERNAL_SERVICE_TOKEN and x_service_token and x_service_token == _INTERNAL_SERVICE_TOKEN:
        return {
            "user_id": None,
            "role": "service",
            "canonical_role": "service",
            "email": None,
            "permissions": [],
            "auth_mode": "service_token",
        }

    if creds is None or creds.scheme.lower() != "bearer" or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required",
        )
    return _validate_bearer_via_auth_service(creds.credentials)


def assert_self_or_admin(actor: Dict[str, Any], target_user_id: int) -> None:
    """Helper called from inside handlers that have already resolved the
    bearer actor via `Depends(require_bearer_actor)`. The 2-arg form is
    needed because FastAPI's dependency-injection machinery doesn't bind
    the path's `user_id` parameter to a sibling Depends — sub-dependencies
    can only see request-scoped values, not other path params, so the
    earlier `require_self_or_admin(target_user_id, actor)` form picked
    `target_user_id` up as an unwanted query parameter.

    This is the canonical pattern across the codebase
    (payment-service uses the same `_assert_actor_matches_user(actor,
    target_user_id, ...)` shape).

    Raises 403 if the actor is neither the target user nor admin.
    """
    role = _canonicalize_role(actor.get("canonical_role") or actor.get("role"))
    if role in ADMIN_BYPASS_ROLES:
        return
    actor_user_id = actor.get("user_id")
    if actor_user_id is not None and int(actor_user_id) == int(target_user_id):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "permission_denied",
            "reason": "user_id mismatch — caller can only access their own resources",
        },
    )


# Backwards-compat alias kept for any caller still using the old name. New
# code should call `assert_self_or_admin` from inside the handler instead of
# wiring this as a Depends().
def require_self_or_admin(*args, **kwargs):  # pragma: no cover
    raise RuntimeError(
        "require_self_or_admin is no longer a Depends — call "
        "assert_self_or_admin(actor, target_user_id) from inside the handler. "
        "See notification-service/src/auth.py for the rationale."
    )


def require_admin(
    actor: Dict[str, Any] = Depends(require_bearer_actor),
) -> Dict[str, Any]:
    """Require admin / service role. Used for templates, batches, delivery
    worker endpoints, and other platform-wide management routes."""
    role = _canonicalize_role(actor.get("canonical_role") or actor.get("role"))
    if role not in ADMIN_BYPASS_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "permission_denied",
                "required": "admin",
                "actor_role": role,
            },
        )
    return actor
