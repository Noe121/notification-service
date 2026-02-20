"""
Novu Workflow Trigger Client
-----------------------------
Triggers role-based welcome workflows via the Novu API.

Workflow ID mapping (matches Novu identifiers):
  creator  → welcome-creator
  business → welcome-business
  admin    → welcome-admin

Usage:
    from .novu_client import trigger_welcome_workflow

    await trigger_welcome_workflow(
        user_id="abc123",
        email="user@example.com",
        first_name="Nick",
        persona_type="creator",
        persona_label="student_athlete",
    )
"""

import asyncio
import logging
import os
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

NOVU_API_URL = os.getenv("NOVU_API_URL", "https://api.novu.co/v1")
NOVU_SECRET_KEY = os.getenv("NOVU_SECRET_KEY", "")
EMAIL_VERIFICATION_WORKFLOW_ID = os.getenv("NOVU_EMAIL_VERIFICATION_WORKFLOW_ID", "email-verification")

PersonaType = Literal["creator", "business", "admin"]
VerificationMethod = Literal["otp", "magic_link", "both"]

PERSONA_WORKFLOW_MAP: dict[str, str] = {
    "creator": "welcome-creator",
    "business": "welcome-business",
    "admin": "welcome-admin",
}

PERSONA_LABELS_BY_TYPE: dict[str, set[str]] = {
    "creator": {"influencer", "student_athlete", "athlete"},
    "business": {"brand", "company", "organization", "parent", "lawyer"},
    "admin": {"school_admin", "college_admin", "nil_go"},
}

_WELCOME_TRIGGERED_KEYS: set[str] = set()
_WELCOME_TRIGGERED_LOCK = asyncio.Lock()
_EMAIL_VERIFICATION_TRIGGERED_KEYS: set[str] = set()
_EMAIL_VERIFICATION_TRIGGERED_LOCK = asyncio.Lock()


def reset_welcome_idempotency_cache() -> None:
    """Clear in-memory idempotency cache (used by tests/process restarts)."""
    _WELCOME_TRIGGERED_KEYS.clear()


def reset_email_verification_idempotency_cache() -> None:
    """Clear in-memory verification idempotency cache (used by tests/process restarts)."""
    _EMAIL_VERIFICATION_TRIGGERED_KEYS.clear()


async def _post_novu_event(name: str, to: dict, payload: dict) -> dict:
    """Send an event trigger to Novu."""
    event = {
        "name": name,
        "to": to,
        "payload": payload,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{NOVU_API_URL}/events/trigger",
            json=event,
            headers={
                "Authorization": f"ApiKey {NOVU_SECRET_KEY}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        return response.json()


async def trigger_welcome_workflow(
    user_id: str,
    email: str,
    first_name: str,
    persona_type: PersonaType,
    persona_label: str,
    app_url: str = "https://nilbx.com",
    support_email: str = "support@nilbx.com",
    idempotency_key: str | None = None,
) -> dict:
    """
    Trigger the correct welcome workflow for a newly registered user.

    Args:
        user_id:       Unique user ID (used as Novu subscriberId).
        email:         User's email address.
        first_name:    User's first name (used in email/in-app copy).
        persona_type:  One of "creator" | "business" | "admin".
        persona_label: Fine-grained role label (e.g. "student_athlete", "brand").
        app_url:       Deep link back into the app.
        support_email: Support address shown in email footer.
        idempotency_key: Optional key to prevent duplicate sends.

    Returns:
        Novu API response dict.

    Raises:
        ValueError:  If persona_type is not recognised.
        httpx.HTTPStatusError: If the Novu API returns a non-2xx response.
    """
    workflow_id = PERSONA_WORKFLOW_MAP.get(persona_type)
    if not workflow_id:
        raise ValueError(
            f"Unknown persona_type '{persona_type}'. "
            f"Must be one of: {list(PERSONA_WORKFLOW_MAP)}"
        )
    allowed_labels = PERSONA_LABELS_BY_TYPE[persona_type]
    if persona_label not in allowed_labels:
        raise ValueError(
            f"Invalid persona_label '{persona_label}' for persona_type '{persona_type}'. "
            f"Must be one of: {sorted(allowed_labels)}"
        )

    if not NOVU_SECRET_KEY:
        logger.error("NOVU_SECRET_KEY is not set — skipping welcome workflow trigger")
        return {}

    dedupe_key = idempotency_key or f"welcome:{user_id}:{persona_type}:{persona_label}"
    async with _WELCOME_TRIGGERED_LOCK:
        if dedupe_key in _WELCOME_TRIGGERED_KEYS:
            logger.info(
                "Skipping duplicate Novu welcome trigger | user=%s persona=%s/%s key=%s",
                user_id,
                persona_type,
                persona_label,
                dedupe_key,
            )
            return {"status": "skipped", "reason": "duplicate", "idempotency_key": dedupe_key}

    result = await _post_novu_event(
        name=workflow_id,
        to={
            "subscriberId": user_id,
            "email": email,
            "firstName": first_name,
        },
        payload={
            "personaType": persona_type,
            "personaLabel": persona_label,
            "appUrl": app_url,
            "supportEmail": support_email,
        },
    )

    async with _WELCOME_TRIGGERED_LOCK:
        _WELCOME_TRIGGERED_KEYS.add(dedupe_key)

    logger.info(
        "Novu welcome workflow triggered | user=%s persona=%s/%s workflow=%s key=%s",
        user_id,
        persona_type,
        persona_label,
        workflow_id,
        dedupe_key,
    )
    return result


async def trigger_email_verification(
    user_id: str,
    email: str,
    first_name: str = "",
    verification_method: VerificationMethod = "otp",
    verification_code: str | None = None,
    magic_link: str | None = None,
    expires_minutes: int = 10,
    app_url: str = "https://nilbx.com",
    support_email: str = "support@nilbx.com",
    idempotency_key: str | None = None,
) -> dict:
    """
    Trigger Novu email verification workflow (OTP and/or magic link).

    Args:
        user_id: User ID used as Novu subscriberId.
        email: User email.
        first_name: Optional first name for personalization.
        verification_method: otp | magic_link | both.
        verification_code: OTP value for code-based verification.
        magic_link: Single-use verification URL for magic-link verification.
        expires_minutes: Token/code expiration window in minutes.
        app_url: App base URL for fallback CTA.
        support_email: Contact email in template.
        idempotency_key: Optional dedupe key for retries/race protection.
    """
    if verification_method == "otp" and not verification_code:
        raise ValueError("verification_code is required when verification_method='otp'")
    if verification_method == "magic_link" and not magic_link:
        raise ValueError("magic_link is required when verification_method='magic_link'")
    if verification_method == "both" and (not verification_code or not magic_link):
        raise ValueError("verification_code and magic_link are required when verification_method='both'")
    if expires_minutes < 1:
        raise ValueError("expires_minutes must be >= 1")

    if not NOVU_SECRET_KEY:
        logger.error("NOVU_SECRET_KEY is not set — skipping email verification workflow trigger")
        return {}

    dedupe_key = idempotency_key or f"email-verification:{user_id}:{verification_method}:{verification_code or ''}:{magic_link or ''}"
    async with _EMAIL_VERIFICATION_TRIGGERED_LOCK:
        if dedupe_key in _EMAIL_VERIFICATION_TRIGGERED_KEYS:
            logger.info("Skipping duplicate email verification trigger | user=%s key=%s", user_id, dedupe_key)
            return {"status": "skipped", "reason": "duplicate", "idempotency_key": dedupe_key}

    result = await _post_novu_event(
        name=EMAIL_VERIFICATION_WORKFLOW_ID,
        to={
            "subscriberId": user_id,
            "email": email,
            "firstName": first_name,
        },
        payload={
            "verificationMethod": verification_method,
            "verificationCode": verification_code or "",
            "magicLink": magic_link or "",
            "expiresMinutes": expires_minutes,
            "appUrl": app_url,
            "supportEmail": support_email,
        },
    )

    async with _EMAIL_VERIFICATION_TRIGGERED_LOCK:
        _EMAIL_VERIFICATION_TRIGGERED_KEYS.add(dedupe_key)

    logger.info(
        "Novu email verification triggered | user=%s method=%s workflow=%s key=%s",
        user_id,
        verification_method,
        EMAIL_VERIFICATION_WORKFLOW_ID,
        dedupe_key,
    )
    return result
