"""Real notification delivery provider clients.

Wraps AWS SES (email), Twilio (SMS), and Firebase Cloud Messaging (push)
behind a common interface used by the delivery worker.

Security:
- Provider credentials loaded from environment variables only
- No PII (email, phone) logged — only notification IDs and hashed recipient refs
- Rate limiting enforced per user per hour via in-memory counter (Redis in production)

PII:
- channel_value (email/phone/FCM token) is passed to providers but NEVER logged
- Log lines use only notification_id and delivery_id as correlators
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger("notification-delivery-worker")

ENVIRONMENT = os.getenv("ENVIRONMENT", "dev").strip().lower()

# Rate limiting: max sends per user per hour
_MAX_SENDS_PER_USER_HOUR = int(os.getenv("MAX_SENDS_PER_USER_HOUR", "20"))
_RL_WINDOW_SECONDS = 3600
_send_counts: Dict[str, list] = defaultdict(list)

# OWASP A04/A05: shared Redis rate limiter. Without this the counter
# is per-worker, so behind N workers an attacker effectively gets
# ``N × _MAX_SENDS_PER_USER_HOUR`` deliveries/hour. Redis path uses a
# sorted-set sliding window; falls back to the in-process deque when
# Redis isn't configured (local/dev).
_REDIS_URL = os.getenv("REDIS_URL") or os.getenv("NOTIFICATION_REDIS_URL") or ""
_rl_redis = None
if _REDIS_URL:
    try:
        import redis as _redis  # type: ignore
        _rl_redis = _redis.from_url(
            _REDIS_URL,
            socket_connect_timeout=1,
            socket_timeout=1,
            decode_responses=True,
        )
        _rl_redis.ping()
        logger.info(
            "notification_rate_limiter backend=redis url_host=%s",
            _REDIS_URL.split("@")[-1].split("/")[0],
        )
    except Exception as _rl_err:
        logger.warning(
            "notification_rate_limiter fallback_to_memory err=%s",
            _rl_err,
        )
        _rl_redis = None


def _hash_recipient(value: str) -> str:
    """Hash a recipient identifier for PII-safe logging."""
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def _check_rate_limit(recipient_ref: str) -> bool:
    """Return True if the recipient has exceeded the hourly send limit.

    Uses Redis sorted-set sliding window when available (shared state
    across all workers); falls back to in-process deque otherwise.
    """
    now = datetime.utcnow()

    if _rl_redis is not None:
        import time as _rl_time
        now_wall = _rl_time.time()
        cutoff_wall = now_wall - _RL_WINDOW_SECONDS
        rkey = f"notify:rl:{recipient_ref}"
        try:
            pipe = _rl_redis.pipeline()
            pipe.zremrangebyscore(rkey, 0, cutoff_wall)
            pipe.zcard(rkey)
            pipe.zadd(rkey, {f"{now_wall}:{os.getpid()}:{_rl_time.monotonic_ns()}": now_wall})
            pipe.expire(rkey, _RL_WINDOW_SECONDS + 1)
            _removed, count_before_add, _added, _ttl = pipe.execute()
            return int(count_before_add or 0) >= _MAX_SENDS_PER_USER_HOUR
        except Exception as exc:
            # Redis hiccup: fail open to in-process counter rather than
            # blocking real users.
            logger.warning(
                "notification_rate_limiter redis_error fallback_to_memory err=%s",
                exc,
            )

    cutoff = now - timedelta(hours=1)
    _send_counts[recipient_ref] = [t for t in _send_counts[recipient_ref] if t > cutoff]
    if len(_send_counts[recipient_ref]) >= _MAX_SENDS_PER_USER_HOUR:
        return True
    _send_counts[recipient_ref].append(now)
    return False


# ---------------------------------------------------------------------------
# HTML email body sanitization (OWASP A03)
# ---------------------------------------------------------------------------
#
# Notifications can arrive with either a pre-rendered HTML body
# (``data_payload["html_body"]``) or a Jinja template
# (``data_payload["html_template"]`` + ``data_payload["context"]``).
#
# * Jinja path: rendered with ``autoescape=True`` against the provided
#   context — standard protection, context values are escaped.
# * Pre-rendered path: run through ``bleach`` with a narrow allowlist
#   of safe tags/attrs so admin-authored templates cannot embed
#   ``<script>`` / event handlers / ``javascript:`` URLs. Unknown tags
#   get their text preserved; attackers get a plain-text leak, not
#   executable HTML.
#
# Both paths are best-effort: if the optional deps aren't available in
# the runtime (jinja2 / bleach), we fall back to escaping the entire
# payload with ``html.escape`` so the worst case is a visually ugly
# email, never an executable one.

_HTML_ALLOWED_TAGS = frozenset({
    "a", "b", "br", "div", "em", "h1", "h2", "h3", "h4", "hr", "i",
    "img", "li", "ol", "p", "pre", "small", "span", "strong", "table",
    "tbody", "td", "th", "thead", "tr", "u", "ul",
})
_HTML_ALLOWED_ATTRS = {
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "title", "width", "height"],
    "span": ["style"],
    "div": ["style"],
    "table": ["cellpadding", "cellspacing", "border"],
    "th": ["align"],
    "td": ["align", "colspan", "rowspan"],
}
_HTML_ALLOWED_PROTOCOLS = frozenset({"http", "https", "mailto"})


def _safe_render_email_html(notification: Dict[str, Any]) -> str:
    """Return a sanitised HTML body for an email notification.

    Returns the empty string if no HTML payload is provided (plaintext-only
    email). Never raises — sanitisation failures fall back to an escaped
    copy of the raw payload so the worst case is a plaintext-looking email.
    """
    data_payload = notification.get("data_payload") or {}
    if not isinstance(data_payload, dict):
        return ""

    raw_html = str(data_payload.get("html_body") or "")
    template_str = str(data_payload.get("html_template") or "")
    context = data_payload.get("context") or {}
    if not isinstance(context, dict):
        context = {}

    if template_str:
        try:
            from jinja2 import Environment, BaseLoader, select_autoescape
            env = Environment(
                loader=BaseLoader(),
                autoescape=select_autoescape(enabled_extensions=("html", "xml"), default_for_string=True),
            )
            raw_html = env.from_string(template_str).render(**context)
        except Exception:
            logger.exception(
                "Jinja render failed for notification=%s — falling back to escaped plaintext",
                notification.get("id"),
            )
            import html as _html
            raw_html = "<pre>" + _html.escape(template_str) + "</pre>"

    if not raw_html:
        return ""

    try:
        import bleach  # type: ignore
        return bleach.clean(
            raw_html,
            tags=_HTML_ALLOWED_TAGS,
            attributes=_HTML_ALLOWED_ATTRS,
            protocols=_HTML_ALLOWED_PROTOCOLS,
            strip=True,
            strip_comments=True,
        )
    except Exception:
        # bleach not installed / bleach failure — fail closed with a
        # fully-escaped copy rather than passing the raw HTML through.
        logger.exception(
            "HTML sanitisation unavailable for notification=%s — escaping entire payload",
            notification.get("id"),
        )
        import html as _html
        return "<pre>" + _html.escape(raw_html) + "</pre>"


# ---------------------------------------------------------------------------
# Email — AWS SES
# ---------------------------------------------------------------------------

async def send_email_ses(channel: Dict[str, Any], notification: Dict[str, Any]) -> Dict[str, Any]:
    """Send email via AWS SES.

    Requires: AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (or IAM role)
    and SES_FROM_EMAIL in environment.
    """
    recipient_email = channel["channel_value"]
    notification_id = notification["id"]

    if _check_rate_limit(f"email:{_hash_recipient(recipient_email)}"):
        logger.warning("Rate limit exceeded for email delivery, notification=%s", notification_id)
        raise Exception("Rate limit exceeded for email delivery")

    logger.info("Sending email for notification=%s recipient_hash=%s",
                notification_id, _hash_recipient(recipient_email))

    try:
        import boto3
        ses = boto3.client("ses", region_name=os.getenv("AWS_REGION", "us-east-1"))

        from_email = os.getenv("SES_FROM_EMAIL", "noreply@nilbx.com")

        # OWASP A03 (XSS-in-email-client): the subject, text, and html
        # bodies may include user-supplied content (e.g. creator
        # display names rendered into a "Thanks for subscribing to
        # {{creator}}" template). Without escaping, an attacker-set
        # name like ``<script>...`` would render as script in HTML
        # email clients that support it, or as bogus subject-header
        # continuations in malformed clients.
        # * Subject & plaintext body: strip CR/LF to prevent SMTP
        #   header-injection style tricks.
        # * HTML body: render via Jinja2 ``Environment(autoescape=True)``
        #   over the caller-provided template string against a safe
        #   context derived from ``data_payload``. If a caller sends
        #   pre-rendered HTML with raw substrings, we still sanitize
        #   it with ``bleach`` against a narrow allowlist.
        subject_raw = str(notification.get("title", "NILBx Notification"))
        body_text_raw = str(notification.get("message", ""))
        subject = subject_raw.replace("\r", "").replace("\n", " ")[:998]
        body_text = body_text_raw.replace("\r\n", "\n")
        body_html = _safe_render_email_html(notification)

        response = ses.send_email(
            Source=from_email,
            Destination={"ToAddresses": [recipient_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": body_text, "Charset": "UTF-8"},
                    **({"Html": {"Data": body_html, "Charset": "UTF-8"}} if body_html else {}),
                },
            },
        )

        message_id = response.get("MessageId", f"ses-{notification_id}")
        logger.info("Email sent successfully notification=%s ses_message_id=%s", notification_id, message_id)

        return {
            "external_message_id": message_id,
            "response_metadata": {"provider": "ses", "status": "sent"},
        }

    except Exception as exc:
        logger.exception("SES email send failed notification=%s", notification_id)
        raise


# ---------------------------------------------------------------------------
# SMS — Twilio
# ---------------------------------------------------------------------------

async def send_sms_twilio(channel: Dict[str, Any], notification: Dict[str, Any]) -> Dict[str, Any]:
    """Send SMS via Twilio.

    Requires: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER in environment.
    """
    recipient_phone = channel["channel_value"]
    notification_id = notification["id"]

    if _check_rate_limit(f"sms:{_hash_recipient(recipient_phone)}"):
        logger.warning("Rate limit exceeded for SMS delivery, notification=%s", notification_id)
        raise Exception("Rate limit exceeded for SMS delivery")

    logger.info("Sending SMS for notification=%s recipient_hash=%s",
                notification_id, _hash_recipient(recipient_phone))

    try:
        from twilio.rest import Client as TwilioClient

        account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
        from_number = os.getenv("TWILIO_FROM_NUMBER", "")

        if not account_sid or not auth_token or not from_number:
            raise Exception("Twilio credentials not configured")

        client = TwilioClient(account_sid, auth_token)
        message = client.messages.create(
            body=notification.get("message", "NILBx Notification"),
            from_=from_number,
            to=recipient_phone,
        )

        logger.info("SMS sent notification=%s twilio_sid=%s", notification_id, message.sid)

        return {
            "external_message_id": message.sid,
            "response_metadata": {"provider": "twilio", "status": str(message.status)},
        }

    except Exception as exc:
        logger.exception("Twilio SMS send failed notification=%s", notification_id)
        raise


# ---------------------------------------------------------------------------
# Push — Firebase Cloud Messaging
# ---------------------------------------------------------------------------

_FCM_TOKEN_RE = __import__("re").compile(r"^[A-Za-z0-9_\-:]{100,300}$")


def _is_plausible_fcm_token(token: str) -> bool:
    """Cheap shape check on an FCM registration token.

    OWASP A04 (input validation): FCM tokens are ~150-180 chars of
    URL-safe alphanumerics plus ``:`` ``_`` ``-``. Anything outside
    that shape is almost certainly a bad / dev / spoofed token —
    sending to the provider wastes quota and fills error logs with
    garbage. We reject early rather than round-trip to FCM.
    """
    if not token or not isinstance(token, str):
        return False
    return bool(_FCM_TOKEN_RE.match(token))


async def send_push_fcm(channel: Dict[str, Any], notification: Dict[str, Any]) -> Dict[str, Any]:
    """Send push notification via Firebase Cloud Messaging.

    Requires: GOOGLE_APPLICATION_CREDENTIALS env var pointing to service account JSON,
    or FIREBASE_CREDENTIALS_JSON env var with inline JSON.
    """
    fcm_token = channel["channel_value"]
    notification_id = notification["id"]

    if not _is_plausible_fcm_token(fcm_token):
        logger.warning(
            "Refusing to send FCM push with malformed token notification=%s",
            notification_id,
        )
        raise ValueError("Invalid FCM token format")

    if _check_rate_limit(f"push:{_hash_recipient(fcm_token)}"):
        logger.warning("Rate limit exceeded for push delivery, notification=%s", notification_id)
        raise Exception("Rate limit exceeded for push delivery")

    logger.info("Sending push for notification=%s token_hash=%s",
                notification_id, _hash_recipient(fcm_token))

    try:
        import firebase_admin
        from firebase_admin import credentials, messaging

        # Initialize Firebase app if not already done
        if not firebase_admin._apps:
            creds_json = os.getenv("FIREBASE_CREDENTIALS_JSON", "")
            if creds_json:
                import json
                cred = credentials.Certificate(json.loads(creds_json))
            else:
                cred = credentials.ApplicationDefault()
            firebase_admin.initialize_app(cred)

        # Build the FCM message
        data_payload = notification.get("data_payload", {})
        if isinstance(data_payload, str):
            import json
            try:
                data_payload = json.loads(data_payload)
            except Exception:
                data_payload = {}

        fcm_message = messaging.Message(
            notification=messaging.Notification(
                title=notification.get("title", "NILBx"),
                body=notification.get("message", ""),
            ),
            data={k: str(v) for k, v in (data_payload or {}).items() if k not in ("html_body",)},
            token=fcm_token,
        )

        response = messaging.send(fcm_message)
        logger.info("Push sent notification=%s fcm_response=%s", notification_id, response)

        return {
            "external_message_id": response,
            "response_metadata": {"provider": "fcm", "status": "sent"},
        }

    except Exception as exc:
        logger.exception("FCM push send failed notification=%s", notification_id)
        raise
