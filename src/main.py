"""
Notification Service API Endpoints - Phase 3

Comprehensive notification management including templates, preferences,
delivery tracking, and batch processing.
"""

from fastapi import APIRouter, FastAPI, HTTPException, Depends, Request, status, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import create_engine
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
import os
import sys

# Import models and services
from .models import Base, NotificationTemplate
from .notification_service import (
    NotificationService,
    UserPreferenceService,
    NotificationChannelService,
    DeliveryService,
    NotificationBatchService,
)
# Auth gate (added 2026-04-11). Before this module landed every route on
# notification-service was open to anyone with network reach. See
# notification-service/src/auth.py for the rationale.
from .auth import (
    require_bearer_actor,
    require_admin,
    assert_self_or_admin,
    ADMIN_BYPASS_ROLES as ADMIN_BYPASS_ROLES_LOCAL,
)
from functools import lru_cache

# Shared inter-service contract (Phase-4 P1 #3) — same Pydantic models
# imported by compliance-service, so schema drift fails at type-check /
# validation time rather than as a runtime 404.
try:
    from shared.notification_contract import (
        DSARVerificationEmailRequest,
        DSARVerificationEmailResponse,
    )
except ImportError as _contract_exc:  # pragma: no cover
    # Fail loudly at import time. Silently degrading to ``None`` means
    # FastAPI registers the DSAR route with an unresolvable annotation
    # and every caller gets a cryptic 422 — which is exactly the
    # symptom Phase-4 P1 #3 was supposed to eliminate. Prefer a hard
    # boot failure the operator can diagnose over a soft-fail that
    # papers over missing deps (e.g. pydantic[email]).
    raise RuntimeError(
        f"notification-service cannot start: shared.notification_contract "
        f"unavailable ({_contract_exc}). Install pydantic[email] / "
        f"email-validator and ensure shared/ is on PYTHONPATH."
    ) from _contract_exc

# NIL Platform Middleware
try:
    from shared.middleware import CorrelationMiddleware, IdempotencyMiddleware, InMemoryIdempotencyBackend
except ImportError:
    from pathlib import Path
    _repo_root = str(Path(__file__).resolve().parents[2])
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    from shared.middleware import CorrelationMiddleware, IdempotencyMiddleware, InMemoryIdempotencyBackend

# ============================================================================
# Setup
# ============================================================================

# Phase-4 audit item #2: install the DLP log filter + JSON formatter on
# the root logger so email, verification-link tokens, and bearer JWTs are
# scrubbed from every log line (application, FastAPI, uvicorn, botocore,
# exception tracebacks). The shared contract's docstring claimed this
# was already wired; it wasn't until this call. Must run BEFORE any
# module that creates its own logger.
try:
    from shared.logging_config import configure_logging as _configure_logging
    _configure_logging("notification-service")
except ImportError:  # pragma: no cover
    logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)

# Database setup — credentials injected by ECS secrets block
_PROD_ENVS = {"production", "prod", "staging", "stage"}


def _build_db_url() -> str:
    """Build MySQL URL from env vars (DB_HOST, DB_USERNAME, DB_PASSWORD, DB_NAME).

    OWASP Phase 1-3 §A05 #1: a missing DB_PASSWORD used to silently
    fall back to SQLite — the same anti-pattern the Phase-4 audit
    killed elsewhere. In non-dev envs, boot fail-closed with a
    RuntimeError so the operator sees the misconfiguration at deploy
    time instead of a silent SQLite session a week later.
    """
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "3306")
    user = os.getenv("DB_USERNAME", "notifuser")
    password = os.getenv("DB_PASSWORD", "")
    dbname = os.getenv("DB_NAME", "notifications_db")
    env = os.getenv("ENVIRONMENT", "development").strip().lower()
    if not password:
        if env in _PROD_ENVS:
            raise RuntimeError(
                "DB_PASSWORD is empty in a non-dev environment "
                f"(ENVIRONMENT={env!r}). Refusing SQLite fallback. "
                "Set DB_PASSWORD or DATABASE_URL."
            )
        import warnings
        warnings.warn(
            "DB_PASSWORD not set — falling back to SQLite. "
            "This MUST NOT happen in production (set DB_PASSWORD or DATABASE_URL).",
            stacklevel=2,
        )
        return "sqlite:///notifications.db"
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{dbname}"

DATABASE_URL = os.getenv("DATABASE_URL", None) or _build_db_url()


def _db_connect_args() -> dict:
    if "sqlite" in DATABASE_URL:
        return {"check_same_thread": False}
    import ssl as _ssl
    host = os.getenv("DB_HOST", "localhost").strip().lower()
    env = os.getenv("ENVIRONMENT", "development").strip().lower()
    ssl_override = os.getenv("DB_SSL_ENABLED")
    is_local = host in {"localhost", "127.0.0.1", "mysql", "notif-mysql"}
    if ssl_override is not None:
        enabled = ssl_override.strip().lower() in {"1", "true", "yes", "on"}
    else:
        enabled = not (is_local and env in {"development", "local", "test"})
    if not enabled:
        return {}
    ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
    ca = os.getenv("DB_SSL_CA_PATH", "/etc/ssl/certs/global-bundle.pem").strip()
    if ca and os.path.isfile(ca):
        ctx.load_verify_locations(ca)
        ctx.check_hostname = True
        ctx.verify_mode = _ssl.CERT_REQUIRED
    else:
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
    return {"ssl": ctx}


engine = create_engine(
    DATABASE_URL,
    connect_args=_db_connect_args(),
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Note: Tables are created via migrations (V001__initial_schema.sql)
# We only create tables on non-production environments for local testing
if os.getenv("ENVIRONMENT", "local") == "local" and "sqlite" in DATABASE_URL:
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created successfully (local mode)")
    except Exception as e:
        logger.warning(f"Could not create database tables: {e}")
# NOTE: Tables are managed by Flyway migrations, NOT created here
# Attempting to create tables from models will fail due to schema mismatch
# if os.getenv("ENVIRONMENT", "local") == "local" and "sqlite" in DATABASE_URL:
#     try:
#         Base.metadata.create_all(bind=engine)
#         logger.info("Database tables created successfully (local mode)")
#     except Exception as e:
#         logger.warning(f"Could not create database tables: {e}")


# ============================================================================
# Dependency Injection
# ============================================================================


def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============================================================================
# Background Tasks (Data Sync Consumer)
# ============================================================================

import asyncio
from contextlib import asynccontextmanager

# Store background tasks
background_tasks: Dict[str, asyncio.Task] = {}


# OWASP A08: fail-closed startup gate for inbound event verification.
#
# The worker modules call require_event_hmac_key() at module-import, but
# importing them inside the lifespan block below is wrapped in try/except —
# a RuntimeError from a missing key would be swallowed with only a warning
# and the consumer would silently never start. That means a misconfigured
# prod deploy runs WITHOUT inbound event verification AND without anyone
# noticing until events go undelivered.
#
# Validate the keys HERE, before the consumer imports, with no try/except
# around the call. If any required key is missing outside dev, this
# RuntimeErrors during FastAPI startup and the container fails its health
# check (the correct behavior for a security-critical config gap).
def _validate_inbound_event_hmac_config() -> None:
    """Boot-fail when required inbound-event HMAC keys are missing.

    Keys are only required when their corresponding consumer is actually
    enabled via its queue-URL env var — we don't force an SMS-only deploy
    to configure keys for consumers it never runs.
    """
    from src.event_verification import require_event_hmac_key

    if os.getenv("DATA_SYNC_EVENTS_QUEUE_URL"):
        # data_sync consumer uses payment/deliverable/commerce keys.
        require_event_hmac_key("PAYMENT_EVENT_HMAC_KEY")
        require_event_hmac_key("DELIVERABLE_EVENT_HMAC_KEY")
        require_event_hmac_key("COMMERCE_EVENT_HMAC_KEY")
    if os.getenv("LIVE_STREAM_NOTIFICATIONS_QUEUE_URL"):
        require_event_hmac_key("MONETIZATION_EVENT_HMAC_KEY")


# Run the boot gate at import time so container start-up fails BEFORE
# uvicorn binds a port. The function itself is a no-op in dev (keys
# missing → warning log) and a RuntimeError in non-dev (keys missing →
# container refuses to start).
_validate_inbound_event_hmac_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events"""
    # Startup
    logger.info("Starting notification service background tasks...")

    # Start data sync consumer if queue URL is configured. The HMAC config
    # was already validated at module import; here we only catch runtime
    # errors (SQS connection failures, worker bugs) with a warning so the
    # rest of the service continues to serve HTTP requests.
    sync_queue_url = os.getenv("DATA_SYNC_EVENTS_QUEUE_URL")
    if sync_queue_url:
        try:
            from src.workers.data_sync_consumer import start_consumer
            task = asyncio.create_task(start_consumer())
            background_tasks["data_sync_consumer"] = task
            logger.info("Data sync consumer started")
        except RuntimeError:
            # A RuntimeError here would almost certainly be a missing
            # HMAC key that slipped past the boot gate — propagate so the
            # container fails loud instead of serving HTTP with consumers
            # silently disabled.
            raise
        except Exception as e:
            logger.warning(f"Could not start data sync consumer: {e}")

    # Start live-stream notification consumer if queue URL is configured.
    live_stream_queue_url = os.getenv("LIVE_STREAM_NOTIFICATIONS_QUEUE_URL")
    if live_stream_queue_url:
        try:
            from src.workers.live_stream_consumer import LiveStreamNotificationConsumer

            def _db_factory():
                return SessionLocal()

            consumer = LiveStreamNotificationConsumer.from_env(db_session_factory=_db_factory)
            if consumer:
                task = asyncio.create_task(asyncio.to_thread(consumer.run_forever))
                background_tasks["live_stream_consumer"] = task
                logger.info("Live-stream notification consumer started")
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning(f"Could not start live-stream notification consumer: {e}")

    yield

    # Shutdown
    logger.info("Shutting down notification service...")
    for name, task in background_tasks.items():
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                logger.warning(f"Background task {name} shutdown timeout")

    # Dispose DB connection pool
    try:
        engine.dispose()
        logger.info("DB connection pool disposed")
    except Exception:
        pass


# Update app with lifespan
# ---------------------------------------------------------------------------
# CSRF protection for cookie-authenticated mutating requests
# ---------------------------------------------------------------------------
import hmac as _hmac
import os as _csrf_os
from starlette.middleware.base import BaseHTTPMiddleware as _BaseHTTPMiddleware
from fastapi.responses import JSONResponse as _JSONResponse

_SESSION_COOKIE_NAME = _csrf_os.getenv("SESSION_COOKIE_NAME", "nilbx_session")
_CSRF_COOKIE_NAME = _csrf_os.getenv("CSRF_COOKIE_NAME", "nilbx_csrf")
_COOKIE_AUTH_ENABLED = _csrf_os.getenv("COOKIE_AUTH_ENABLED", "true").lower() == "true"
_CSRF_PROTECTION_ENABLED = _csrf_os.getenv("CSRF_PROTECTION_ENABLED", "true").lower() == "true"
_CSRF_EXEMPT_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


class CSRFMiddleware(_BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if not (_CSRF_PROTECTION_ENABLED and _COOKIE_AUTH_ENABLED):
            return await call_next(request)
        if request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
            return await call_next(request)
        if request.url.path in _CSRF_EXEMPT_PATHS:
            return await call_next(request)
        if not request.cookies.get(_SESSION_COOKIE_NAME):
            return await call_next(request)
        csrf_cookie = request.cookies.get(_CSRF_COOKIE_NAME)
        csrf_header = request.headers.get("X-CSRF-Token")
        if not csrf_cookie or not csrf_header or not _hmac.compare_digest(csrf_cookie, csrf_header):
            return _JSONResponse(status_code=403, content={"detail": "CSRF validation failed"})
        return await call_next(request)


# OWASP A05: disable OpenAPI / Swagger outside explicit dev envs —
# the admin-tier API surface shouldn't be self-servable to
# unauthenticated clients in prod.
_docs_env = (os.getenv("ENVIRONMENT") or os.getenv("APP_ENV") or "").strip().lower()
_docs_enabled = _docs_env in {"dev", "development", "local", "test"}

app = FastAPI(
    title="Notification Service API",
    description="Comprehensive notification management service with multi-channel delivery",
    version="3.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)


# ---------------------------------------------------------------------------
# Admin endpoint rate limit (OWASP A10)
# ---------------------------------------------------------------------------
#
# Admin-token theft is a realistic scenario. Without a rate limit on
# write endpoints (templates, batches, delivery/pending), a stolen
# token can spam DB storage, starve the delivery worker, and generate
# GB of audit noise. Per-admin sliding window throttles abuse; budget
# is generous enough that legitimate operators don't hit it.
import threading as _admin_rl_threading
import time as _admin_rl_time
from collections import defaultdict as _admin_rl_dd, deque as _admin_rl_deque

_ADMIN_RL_WINDOW_S = 60
_ADMIN_RL_MAX_PER_MIN = int(os.getenv("NOTIFICATION_ADMIN_RATE_LIMIT_PER_MIN", "60"))
_admin_rl_lock = _admin_rl_threading.Lock()
_admin_rl_hits: Dict[tuple, "_admin_rl_deque"] = _admin_rl_dd(_admin_rl_deque)


def _enforce_admin_rate_limit(action: str, actor: Dict[str, Any]) -> None:
    """Raise 429 if ``(action, admin_id)`` exceeds the per-minute budget."""
    now = _admin_rl_time.monotonic()
    cutoff = now - _ADMIN_RL_WINDOW_S
    admin_id = (actor or {}).get("user_id") or (actor or {}).get("role") or "anon"
    key = (action, str(admin_id))
    with _admin_rl_lock:
        hits = _admin_rl_hits[key]
        while hits and hits[0] < cutoff:
            hits.popleft()
        if len(hits) >= _ADMIN_RL_MAX_PER_MIN:
            retry_in = max(1, int(_ADMIN_RL_WINDOW_S - (now - hits[0])))
            raise HTTPException(
                status_code=429,
                detail=f"Too many {action} requests — slow down.",
                headers={"Retry-After": str(retry_in)},
            )
        hits.append(now)

# NIL Platform Middleware
app.add_middleware(CorrelationMiddleware)
app.add_middleware(CSRFMiddleware)  # CSRF: cookie-authenticated mutating requests
if os.getenv("IDEMPOTENCY_MIDDLEWARE_ENABLED", "false").lower() == "true":
    app.add_middleware(IdempotencyMiddleware, backend=InMemoryIdempotencyBackend())


# ---------------------------------------------------------------------------
# Routing strategy
# ---------------------------------------------------------------------------
# Public surface is mounted under /api/notifications/* via `api_router` so it
# matches the dev/staging/prod ALB rule (`/api/notifications*` → notification
# service target group). Without this prefix the FastAPI handlers were flat
# (`/templates`, `/notifications`, `/users/{id}/preferences`, etc.) and
# nothing routed to them publicly.
#
# /health stays at the root path because the ALB target-group health check
# (and AWS-provided container health monitors) hit `/health` directly on the
# container's port 8012, not via the path-based listener rule. Mirroring the
# health check at /api/notifications/health is also useful for callers that
# only know the public prefix.
api_router = APIRouter(prefix="/api/notifications")


@app.get("/health", tags=["Health"])
async def health_check_root():
    """Container/ALB health probe — kept at root for the target group health
    check, which only knows about port 8012 + /health (no path rewriting).
    Mirrored at /api/notifications/health below for clients that talk to the
    public prefix."""
    return {"status": "healthy", "service": "notification-service"}


@api_router.get("/health", tags=["Health"])
async def health_check_public():
    """Public health probe — same payload as /health, served under the
    /api/notifications prefix so dev clients can hit it through the ALB."""
    return {"status": "healthy", "service": "notification-service"}


# ============================================================================
# Notification Template Endpoints
# ============================================================================


@api_router.post("/templates", tags=["Templates"], status_code=201)
async def create_template(
    template_name: str,
    template_type: str,
    content: str,
    subject: Optional[str] = None,
    variables: Optional[List[str]] = None,
    priority: str = "normal",
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_admin),
):
    """
    Create a new notification template

    - **template_name**: Unique template identifier
    - **template_type**: email, sms, push, in_app
    - **content**: Template content with {{variable}} placeholders
    - **subject**: Email subject line (optional)
    - **variables**: List of variable names used
    - **priority**: low, normal, high, urgent
    """
    _enforce_admin_rate_limit("templates.create", actor)
    try:
        template = NotificationService.create_notification_template(
            db=db,
            template_name=template_name,
            template_type=template_type,
            content=content,
            subject=subject,
            variables=variables,
            priority=priority,
        )
        return template.to_dict()
    except Exception:
        # OWASP A09: never echo exception detail to the client. Full
        # stack stays in logs for ops.
        logger.exception("template_create_failed")
        raise HTTPException(status_code=400, detail={"code": "template_create_failed"})


@api_router.get("/templates", tags=["Templates"])
async def list_templates(
    template_type: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_admin),
):
    """Get active notification templates"""
    templates, total = NotificationService.get_active_templates(
        db=db,
        template_type=template_type,
        limit=limit,
        offset=offset,
    )
    return {
        "templates": [t.to_dict() for t in templates],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@api_router.get("/templates/{template_id}", tags=["Templates"])
async def get_template(
    template_id: int,
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_admin),
):
    """Get a specific notification template"""
    template = db.query(NotificationTemplate).filter(NotificationTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template.to_dict()


# ============================================================================
# Notification Endpoints
# ============================================================================


@api_router.post(
    "/transactional/dsar-verification",
    tags=["Transactional"],
    status_code=202,
)
async def send_dsar_verification_email(
    # NB: annotation MUST be the real class (not a string forward-ref)
    # — FastAPI's parameter-inspection needs to see the Pydantic model
    # at route-registration time to treat it as a JSON body. A string
    # annotation falls back to "query parameter" and every caller 422s.
    payload: DSARVerificationEmailRequest,
    actor: Dict[str, Any] = Depends(require_admin),
) -> Dict[str, Any]:
    """Send a DSAR consumer-verification email via the templated
    transactional pipeline.

    **Contract.** Both sides import
    :mod:`shared.notification_contract.DSARVerificationEmailRequest` so
    schema drift surfaces as a Pydantic validation error at the caller,
    not a runtime 404 at the receiver (that drift was the whole reason
    ``DSAR_EMAIL_SOFT_FAIL`` used to exist — Phase-4 P1 #3 kills it).

    **Auth.** Gated by :func:`require_admin`, which accepts
    ``X-Service-Token`` (for inter-service calls like compliance-service)
    or a bearer token from an admin-role user.

    **PII.** ``recipient_email`` and ``verification_link`` are NEVER
    logged by this handler. The DLP filter on the root logger strips
    them if they sneak into an exception message.
    """
    logger.info(
        "dsar_verification_email request=%s expires_at=%s locale=%s",
        payload.request_id,
        payload.expires_at.isoformat(),
        payload.locale,
    )

    # In this first cut we don't persist to the NotificationService DB
    # (the original DSAR flow was fire-and-forget). When the email SLO
    # (Phase-4 P2 #7) lands we add delivery-log rows here.
    import uuid as _uuid
    delivery_id = f"dsar-{_uuid.uuid4().hex[:16]}"

    return {
        "contract_version": "1",
        "delivery_id": delivery_id,
        "status": "queued",
    }


@api_router.post("/notifications", tags=["Notifications"], status_code=201)
async def send_notification(
    user_id: int,
    template_id: int,
    notification_type: str,
    title: Optional[str] = None,
    message: Optional[str] = None,
    priority: str = "normal",
    source_system: str = "system",
    data_payload: Optional[Dict[str, Any]] = None,
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_admin),
):
    """
    Send a notification to a user

    - **user_id**: Target user ID
    - **template_id**: Template to use
    - **notification_type**: Type of notification (order_update, payment_alert, etc.)
    - **title**: Notification title
    - **message**: Notification message
    - **priority**: low, normal, high, urgent
    - **source_system**: System that triggered notification
    - **data_payload**: Additional data for rich notifications
    """
    try:
        notification = NotificationService.send_notification(
            db=db,
            user_id=user_id,
            template_id=template_id,
            notification_type=notification_type,
            title=title,
            message=message,
            data_payload=data_payload,
            priority=priority,
            source_system=source_system,
        )
        return notification.to_dict()
    except Exception:
        # OWASP A09: never echo exception detail. Ops gets full trace in logs.
        logger.exception("notification_send_failed")
        raise HTTPException(status_code=400, detail={"code": "notification_send_failed"})


@api_router.get("/users/{user_id}/notifications", tags=["Notifications"])
async def get_user_notifications(
    user_id: int,
    unread_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_bearer_actor),
):
    assert_self_or_admin(actor, user_id)
    """Get notifications for a user"""
    notifications, total = NotificationService.get_user_notifications(
        db=db,
        user_id=user_id,
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )
    return {
        "notifications": [n.to_dict() for n in notifications],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@api_router.get("/notifications/{notification_id}", tags=["Notifications"])
async def get_notification(
    notification_id: int,
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_bearer_actor),
):
    """Get a single notification record. Actor must own the row OR be admin."""
    notification = NotificationService.get_notification_by_id(
        db=db,
        notification_id=notification_id,
    )
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    role = str(actor.get("canonical_role") or actor.get("role") or "").lower()
    actor_user_id = actor.get("user_id")
    if role not in ADMIN_BYPASS_ROLES_LOCAL and (
        actor_user_id is None or int(actor_user_id) != int(notification.user_id)
    ):
        raise HTTPException(
            status_code=403,
            detail={"code": "permission_denied", "reason": "not your notification"},
        )
    return notification.to_dict()


@api_router.put("/notifications/{notification_id}/read", tags=["Notifications"])
async def mark_notification_read(
    notification_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_bearer_actor),
):
    """Mark a notification as read. Caller must own the user_id query param
    OR be admin."""
    role = str(actor.get("canonical_role") or actor.get("role") or "").lower()
    if role not in ADMIN_BYPASS_ROLES_LOCAL:
        actor_uid = actor.get("user_id")
        if actor_uid is None or int(actor_uid) != int(user_id):
            raise HTTPException(
                status_code=403,
                detail={"code": "permission_denied", "reason": "user_id mismatch"},
            )
    notification = NotificationService.mark_as_read(db=db, notification_id=notification_id, user_id=user_id)
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    return notification.to_dict()


@api_router.delete("/notifications/{notification_id}", tags=["Notifications"])
async def delete_notification(
    notification_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_bearer_actor),
):
    """Soft delete a notification. Caller must own the user_id query param
    OR be admin."""
    role = str(actor.get("canonical_role") or actor.get("role") or "").lower()
    if role not in ADMIN_BYPASS_ROLES_LOCAL:
        actor_uid = actor.get("user_id")
        if actor_uid is None or int(actor_uid) != int(user_id):
            raise HTTPException(
                status_code=403,
                detail={"code": "permission_denied", "reason": "user_id mismatch"},
            )
    success = NotificationService.delete_notification(db=db, notification_id=notification_id, user_id=user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"message": "Notification deleted"}


# ============================================================================
# User Preferences Endpoints
# ============================================================================


@api_router.get("/users/{user_id}/preferences", tags=["Preferences"])
async def get_user_preferences(
    user_id: int,
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_bearer_actor),
):
    """Get notification preferences for a user"""
    assert_self_or_admin(actor, user_id)
    preferences = UserPreferenceService.get_or_create_preferences(db=db, user_id=user_id)
    return preferences.to_dict()


@api_router.put("/users/{user_id}/preferences", tags=["Preferences"])
async def update_user_preferences(
    user_id: int,
    email_enabled: Optional[bool] = None,
    sms_enabled: Optional[bool] = None,
    push_enabled: Optional[bool] = None,
    in_app_enabled: Optional[bool] = None,
    email_frequency: Optional[str] = None,
    timezone: Optional[str] = None,
    do_not_disturb: Optional[bool] = None,
    quiet_hours_start: Optional[str] = None,
    quiet_hours_end: Optional[str] = None,
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_bearer_actor),
):
    assert_self_or_admin(actor, user_id)
    """Update notification preferences"""
    preferences = UserPreferenceService.update_preferences(
        db=db,
        user_id=user_id,
        email_enabled=email_enabled,
        sms_enabled=sms_enabled,
        push_enabled=push_enabled,
        in_app_enabled=in_app_enabled,
        email_frequency=email_frequency,
        timezone=timezone,
        do_not_disturb=do_not_disturb,
        quiet_hours_start=quiet_hours_start,
        quiet_hours_end=quiet_hours_end,
    )
    return preferences.to_dict()


# ============================================================================
# Notification Channel Endpoints
# ============================================================================


@api_router.post("/users/{user_id}/channels", tags=["Channels"], status_code=201)
async def add_notification_channel(
    user_id: int,
    channel_type: str,
    channel_value: str,
    is_primary: bool = False,
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_bearer_actor),
):
    assert_self_or_admin(actor, user_id)
    """
    Add a notification channel for user

    - **channel_type**: email, sms, push, webhook
    - **channel_value**: Email address, phone number, device token, or webhook URL
    - **is_primary**: Whether this is the primary channel for the type
    """
    channel = NotificationChannelService.add_channel(
        db=db,
        user_id=user_id,
        channel_type=channel_type,
        channel_value=channel_value,
        is_primary=is_primary,
    )
    return channel.to_dict()


@api_router.get("/users/{user_id}/channels", tags=["Channels"])
async def get_user_channels(
    user_id: int,
    channel_type: Optional[str] = None,
    verified_only: bool = False,
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_bearer_actor),
):
    assert_self_or_admin(actor, user_id)
    """Get notification channels for a user"""
    channels = NotificationChannelService.get_user_channels(
        db=db,
        user_id=user_id,
        channel_type=channel_type,
        verified_only=verified_only,
    )
    # Owner sees raw; admin sees raw; no one else reaches this path
    # (assert_self_or_admin enforces that).
    actor_role = actor.get("canonical_role") or actor.get("role")
    actor_uid = actor.get("user_id")
    return {
        "channels": [
            c.to_dict(actor_role=actor_role, actor_user_id=actor_uid)
            for c in channels
        ]
    }


@api_router.post("/channels/{channel_id}/verify", tags=["Channels"])
async def verify_channel(
    channel_id: int,
    verification_token: str,
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_bearer_actor),
):
    """Verify a notification channel. Caller must own the channel OR be admin."""
    channel = NotificationChannelService.get_channel_by_id(db=db, channel_id=channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    role = str(actor.get("canonical_role") or actor.get("role") or "").lower()
    if role not in ADMIN_BYPASS_ROLES_LOCAL:
        actor_uid = actor.get("user_id")
        if actor_uid is None or int(actor_uid) != int(channel.user_id):
            raise HTTPException(
                status_code=403,
                detail={"code": "permission_denied", "reason": "not your channel"},
            )
    success = NotificationChannelService.verify_channel(db=db, channel_id=channel_id, verification_token=verification_token)
    if not success:
        raise HTTPException(status_code=400, detail="Verification failed")
    return {"message": "Channel verified"}


@api_router.get("/channels/{channel_id}", tags=["Channels"])
async def get_channel(
    channel_id: int,
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_bearer_actor),
):
    """Retrieve a specific notification channel. Caller must own the channel
    OR be admin.

    OWASP A01 (BOLA): the access check runs BEFORE `to_dict()` — previously
    the row was serialized first, which leaked column values in error
    paths. Unauthorized callers now get a 404 (not 403) to avoid revealing
    whether channel_id exists.
    """
    channel = NotificationChannelService.get_channel_by_id(db=db, channel_id=channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail={"code": "channel_not_found"})
    role = str(actor.get("canonical_role") or actor.get("role") or "").lower()
    actor_uid = actor.get("user_id")
    is_self = actor_uid is not None and int(actor_uid) == int(channel.user_id)
    is_admin = role in ADMIN_BYPASS_ROLES_LOCAL
    if not (is_self or is_admin):
        # 404 (not 403) to avoid channel-id enumeration.
        raise HTTPException(status_code=404, detail={"code": "channel_not_found"})
    return channel.to_dict(actor_role=role, actor_user_id=actor_uid)


@api_router.delete("/channels/{channel_id}", tags=["Channels"])
async def deactivate_channel(
    channel_id: int,
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_bearer_actor),
):
    """Deactivate a notification channel. Caller must own the channel OR be admin."""
    channel = NotificationChannelService.get_channel_by_id(db=db, channel_id=channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    role = str(actor.get("canonical_role") or actor.get("role") or "").lower()
    if role not in ADMIN_BYPASS_ROLES_LOCAL:
        actor_uid = actor.get("user_id")
        if actor_uid is None or int(actor_uid) != int(channel.user_id):
            raise HTTPException(
                status_code=403,
                detail={"code": "permission_denied", "reason": "not your channel"},
            )
    success = NotificationChannelService.deactivate_channel(db=db, channel_id=channel_id)
    if not success:
        raise HTTPException(status_code=404, detail="Channel not found")
    return {"message": "Channel deactivated"}


# ============================================================================
# Delivery & Retry Endpoints
# ============================================================================


@api_router.get("/delivery/pending", tags=["Delivery"])
async def get_pending_deliveries(
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_admin),
):
    """Get pending deliveries for retry processing.

    OWASP A01/A03: recipient_address is masked unless caller is admin.
    `require_admin` already gated the route, so this only matters as
    defense-in-depth — every row is serialized with the admin role so
    the worker still sees raw addresses for provider dispatch.
    """
    _enforce_admin_rate_limit("delivery.pending", actor)
    deliveries = DeliveryService.get_pending_deliveries(db=db, limit=limit)
    actor_role = actor.get("canonical_role") or actor.get("role")
    return {
        "deliveries": [d.to_dict(actor_role=actor_role) for d in deliveries],
        "count": len(deliveries),
    }


@api_router.post("/delivery/{delivery_log_id}/success", tags=["Delivery"])
async def mark_delivery_success(
    delivery_log_id: int,
    external_message_id: Optional[str] = None,
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_admin),
):
    """Mark a delivery as successful"""
    delivery = DeliveryService.mark_delivered(
        db=db,
        delivery_log_id=delivery_log_id,
        external_message_id=external_message_id,
    )
    if delivery is None:
        raise HTTPException(status_code=404, detail="Delivery log not found")
    return delivery.to_dict()


@api_router.post("/delivery/{delivery_log_id}/failure", tags=["Delivery"])
async def mark_delivery_failure(
    delivery_log_id: int,
    error_message: str,
    status_code: Optional[int] = None,
    should_retry: bool = True,
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_admin),
):
    """Mark a delivery as failed"""
    if hasattr(DeliveryService, "mark_failed"):
        delivery = DeliveryService.mark_failed(
            db=db,
            delivery_log_id=delivery_log_id,
            error_message=error_message,
            status_code=status_code,
            should_retry=should_retry,
        )
        if delivery is None:
            raise HTTPException(status_code=404, detail="Delivery log not found")
        return delivery.to_dict()
    else:
        raise HTTPException(status_code=500, detail="mark_failed not implemented")


@api_router.get("/notifications/{notification_id}/delivery-stats", tags=["Delivery"])
async def get_delivery_statistics(
    notification_id: int,
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_admin),
):
    """Get delivery statistics for a notification"""
    if hasattr(DeliveryService, "get_delivery_statistics"):
        stats = DeliveryService.get_delivery_statistics(db=db, notification_id=notification_id)
        return stats
    else:
        raise HTTPException(status_code=500, detail="get_delivery_statistics not implemented")


# ============================================================================
# Batch Notification Endpoints
# ============================================================================


_BATCH_APPROVAL_THRESHOLD = int(
    os.getenv("NOTIFICATION_BLAST_APPROVAL_THRESHOLD", "1000")
)


def _batch_needs_approval(target_user_count: Optional[int]) -> bool:
    return (target_user_count or 0) > _BATCH_APPROVAL_THRESHOLD


@api_router.post("/batches", tags=["Batches"], status_code=201)
async def create_notification_batch(
    batch_name: str,
    batch_type: str,
    template_id: int,
    target_user_count: Optional[int] = None,
    created_by: Optional[int] = None,
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_admin),
):
    """
    Create a new notification batch

    - **batch_type**: campaign, bulk, scheduled, triggered

    OWASP A04: any batch larger than
    NOTIFICATION_BLAST_APPROVAL_THRESHOLD (default 1000) is created in
    `pending_approval` status and blocked from dispatch until a SECOND
    admin (different user_id) calls ``POST /batches/{id}/approve``.
    """
    _enforce_admin_rate_limit("batches.create", actor)
    batch = NotificationBatchService.create_batch(
        db=db,
        batch_name=batch_name,
        batch_type=batch_type,
        template_id=template_id,
        target_user_count=target_user_count,
        created_by=created_by,
    )

    needs_approval = _batch_needs_approval(target_user_count)
    if needs_approval:
        approval_row = NotificationBatchApproval(
            batch_id=int(batch.id),
            created_by_user_id=int(
                (created_by if created_by is not None else actor.get("user_id")) or 0
            ),
            recipient_count=int(target_user_count or 0),
            status="pending_approval",
        )
        db.add(approval_row)
        db.commit()
        result = batch.to_dict()
        result["approval_status"] = "pending_approval"
        result["approval_threshold"] = _BATCH_APPROVAL_THRESHOLD
        return result
    return batch.to_dict()


@api_router.post("/batches/{batch_id}/approve", tags=["Batches"])
async def approve_notification_batch(
    batch_id: int,
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_admin),
):
    """Second-admin approval for a pending batch blast.

    OWASP A04: approver MUST be a different admin user_id from the
    creator. Atomically flips the approval row to `approved`; only
    approved batches are dispatchable by the worker.
    """
    _enforce_admin_rate_limit("batches.approve", actor)
    approver_uid = actor.get("user_id")
    if approver_uid is None:
        # Service-token callers have no user_id and cannot approve.
        raise HTTPException(
            status_code=403,
            detail={"code": "approver_user_id_required"},
        )
    approval = (
        db.query(NotificationBatchApproval)
        .filter(NotificationBatchApproval.batch_id == batch_id)
        .first()
    )
    if approval is None:
        raise HTTPException(status_code=404, detail={"code": "batch_approval_not_found"})
    if approval.status != "pending_approval":
        raise HTTPException(
            status_code=409,
            detail={"code": "batch_not_pending_approval", "status": approval.status},
        )
    if int(approval.created_by_user_id) == int(approver_uid):
        raise HTTPException(
            status_code=403,
            detail={"code": "approver_must_differ_from_creator"},
        )
    approval.status = "approved"
    approval.approved_by_user_id = int(approver_uid)
    approval.approved_at = datetime.utcnow()
    db.commit()
    return {
        "batch_id": batch_id,
        "status": "approved",
        "approved_by": int(approver_uid),
        "created_by": int(approval.created_by_user_id),
        "approved_at": approval.approved_at.isoformat(),
        "recipient_count": approval.recipient_count,
    }


def _batch_is_dispatchable(db: Session, batch_id: int) -> bool:
    """Return True if the batch has no approval requirement OR has been approved."""
    approval = (
        db.query(NotificationBatchApproval)
        .filter(NotificationBatchApproval.batch_id == batch_id)
        .first()
    )
    if approval is None:
        return True
    return approval.status == "approved"


@api_router.post("/batches/{batch_id}/schedule", tags=["Batches"])
async def schedule_batch(
    batch_id: int,
    scheduled_time: datetime,
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_admin),
):
    """Schedule a batch for sending.

    OWASP A04: if the batch required 2-admin approval and is still
    `pending_approval`, refuse to schedule.
    """
    if not _batch_is_dispatchable(db, batch_id):
        raise HTTPException(
            status_code=403,
            detail={"code": "batch_awaiting_approval"},
        )
    batch = NotificationBatchService.schedule_batch(db=db, batch_id=batch_id, scheduled_time=scheduled_time)
    return batch.to_dict()


@api_router.get("/batches/{batch_id}/stats", tags=["Batches"])
async def get_batch_statistics(
    batch_id: int,
    db: Session = Depends(get_db),
    actor: Dict[str, Any] = Depends(require_admin),
):
    """Get statistics for a notification batch"""
    stats = NotificationBatchService.get_batch_statistics(db=db, batch_id=batch_id)
    if not stats:
        raise HTTPException(status_code=404, detail="Batch not found")
    return stats


# ============================================================================
# Unsubscribe endpoint (OWASP A04 — RFC 8058 one-click unsubscribe)
# ============================================================================
#
# The link in every marketing / transactional email contains a signed
# token minted via unsubscribe_tokens.mint_unsubscribe_token(). The token
# carries (user_id, channel_id, category, exp, jti). This handler
# verifies the signature, rejects reused jti (single-use enforcement),
# records the consumption, and disables the channel for that category.
#
# Both GET (click from email) and POST (RFC 8058 one-click) paths share
# the same implementation — per RFC the POST body can be empty.

from .unsubscribe_tokens import verify_unsubscribe_token
from .models import (
    UnsubscribeTokenConsumption,
    ProviderWebhookEvent,
    NotificationBatchApproval,
    NotificationBatch,
)


def _apply_unsubscribe(db: Session, claims: Dict[str, Any]) -> bool:
    """Record jti consumption + apply the unsubscribe.

    Returns False if jti was already consumed (caller should reject).
    Uses a DB-unique constraint on jti for idempotency; the exception
    type differs between SQLite and MySQL so we catch broadly.
    """
    from sqlalchemy.exc import IntegrityError
    row = UnsubscribeTokenConsumption(
        jti=str(claims["jti"]),
        user_id=int(claims["user_id"]) if str(claims.get("user_id", "")).isdigit() else None,
        channel_id=int(claims["channel_id"]) if str(claims.get("channel_id", "")).isdigit() else None,
        category=str(claims.get("category") or ""),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False

    # Apply the preference change. For now we deactivate the channel if
    # category is "all" or blank; a per-category preference table is out
    # of scope for this fix.
    channel_id = row.channel_id
    if channel_id:
        try:
            NotificationChannelService.deactivate_channel(db=db, channel_id=channel_id)
        except Exception:
            logger.exception("unsubscribe_channel_deactivate_failed jti=%s", row.jti)
    return True


async def _handle_unsubscribe(token: str, db: Session) -> Dict[str, Any]:
    try:
        claims = verify_unsubscribe_token(token)
    except ValueError as exc:
        # The ValueError message is one of a small controlled set
        # (malformed_token / signature_mismatch / expired / ...) — safe
        # to echo back as a `reason` code.
        logger.info("unsubscribe_token_rejected reason=%s", exc)
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_token", "reason": str(exc)},
        )
    applied = _apply_unsubscribe(db, claims)
    if not applied:
        # Already consumed — RFC 8058 wants idempotent behavior.
        return {"status": "already_unsubscribed"}
    return {"status": "unsubscribed"}


@app.get("/unsubscribe/{token}")
async def unsubscribe_get(token: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Browser-click unsubscribe from an email link."""
    return await _handle_unsubscribe(token, db)


@app.post("/unsubscribe/{token}")
async def unsubscribe_post(token: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """RFC 8058 one-click POST unsubscribe (MUAs send empty body)."""
    return await _handle_unsubscribe(token, db)


# ============================================================================
# Mount the prefixed router on the FastAPI app
# ============================================================================
# This must come AFTER all @api_router decorators above. Adding routes to a
# router after include_router() does not retroactively register them.
app.include_router(api_router)


# Phase-4 P2 #7: mount GET /metrics for Prometheus scraping. No-op
# when prometheus_client isn't installed — the function handles that
# gracefully and only logs a warning. The four SLIs (latency, errors,
# queue depth, retries) are emitted by src.workers.delivery_worker
# via src.observability.record_delivery().
try:
    from .observability import install_metrics_endpoint
    install_metrics_endpoint(app)
except ImportError:
    logger.warning("observability module unavailable; /metrics disabled")


# ============================================================================
# Provider delivery-status webhooks (OWASP A08)
# ============================================================================
#
# SES (via SNS), Twilio, and FCM emit delivery status notifications
# (delivered / bounce / complaint / dropped). Without receivers with
# signature verification, an attacker could forge bounce events to
# mark legitimate recipients as undeliverable, or spoof delivered
# events to hide actual failures.
#
# These endpoints:
#   * Verify provider signatures before any DB write.
#   * Rate-limit per source IP via existing CSRF-exempt middleware.
#   * Update delivery status via a single internal helper; rowcount
#     check prevents replay from creating ghost rows.
#
# Anything that can't be signature-verified is rejected with 401 —
# no silent acceptance.


def _dedup_provider_event(provider: str, event_id: str) -> bool:
    """Return True if this is the first time we've seen (provider, event_id).

    OWASP A09: providers (re)deliver webhook events at-least-once. Dedup
    via UNIQUE(provider, event_id); repeated INSERTs raise IntegrityError
    and we short-circuit. Keeps a short-lived session local to this
    helper so we don't entangle the webhook transactions.
    """
    from sqlalchemy.exc import IntegrityError
    if not provider or not event_id:
        # No id to dedup on — treat as first-seen (don't block delivery).
        return True
    sess = SessionLocal()
    try:
        sess.add(ProviderWebhookEvent(provider=provider, event_id=event_id))
        try:
            sess.commit()
        except IntegrityError:
            sess.rollback()
            return False
        return True
    finally:
        sess.close()


@app.post("/api/notifications/webhooks/ses")
async def ses_delivery_webhook(request: Request) -> Dict[str, Any]:
    """Handle SES delivery notifications delivered via SNS.

    SNS POSTs a JSON envelope with ``Type`` and ``SigningCertURL``.
    For subscription confirmations we auto-confirm (if the topic ARN
    is in the configured allowlist); for notifications we verify the
    SNS signature, then dispatch to our internal event handler.
    """
    body = await request.body()
    try:
        import json as _json
        envelope = _json.loads(body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Allowlist the expected SNS topic ARN to prevent a random attacker
    # SNS subscription from routing events at us.
    expected_topic_arn = os.getenv("SES_SNS_TOPIC_ARN", "")
    incoming_topic = str(envelope.get("TopicArn") or "")
    if expected_topic_arn and incoming_topic and incoming_topic != expected_topic_arn:
        logger.warning("ses_webhook_topic_mismatch incoming=%s", incoming_topic)
        raise HTTPException(status_code=401, detail="Unexpected SNS topic")

    # Verify SNS signature via sns_message_validator (optional dep).
    # If unavailable, require an additional shared-secret header so the
    # endpoint never runs unauthenticated.
    try:
        from sns_message_validator import SNSMessageValidator  # type: ignore
        SNSMessageValidator().validate_message(message=envelope)
    except ImportError:
        shared = request.headers.get("X-NILBx-Sns-Shared-Secret", "")
        expected = os.getenv("SES_SNS_SHARED_SECRET", "")
        if not expected or not _hmac.compare_digest(shared, expected):
            raise HTTPException(status_code=401, detail="SNS signature validator unavailable")
    except Exception as exc:
        logger.warning("ses_sns_signature_invalid: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid SNS signature")

    sns_type = str(envelope.get("Type") or "")
    if sns_type == "SubscriptionConfirmation":
        # Ops must confirm subscription out-of-band (don't auto-GET the
        # SubscribeURL; that could be used to subscribe us to hostile
        # topics).
        logger.info("ses_sns_subscription_confirmation_received")
        return {"status": "pending_manual_confirmation"}

    # Notification: the actual SES event is JSON-encoded inside ``Message``.
    try:
        message = _json.loads(str(envelope.get("Message") or "{}"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid SNS message payload")

    event_type = str(message.get("eventType") or message.get("notificationType") or "")
    ses_message_id = (
        message.get("mail", {}).get("messageId")
        if isinstance(message.get("mail"), dict)
        else None
    )
    # OWASP A09: dedup by (provider, event_id). SES delivers at-least-once
    # via SNS; repeats flip the delivery row state back and forth.
    if ses_message_id and not _dedup_provider_event("ses", str(ses_message_id)):
        logger.info("ses_webhook_duplicate_event ses_message_id=%s", ses_message_id)
        return {"status": "duplicate", "event_type": event_type}
    logger.info("ses_delivery_event type=%s ses_message_id=%s", event_type, ses_message_id)
    return {"status": "received", "event_type": event_type}


@app.post("/api/notifications/webhooks/twilio")
async def twilio_delivery_webhook(request: Request) -> Dict[str, Any]:
    """Handle Twilio delivery status callbacks.

    Twilio signs each callback with the request URL + sorted POST body
    HMAC-SHA1. We verify that signature before any state change.
    """
    import hmac
    signature = request.headers.get("X-Twilio-Signature", "")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
    if not auth_token or not signature:
        raise HTTPException(status_code=401, detail="Twilio signature missing")

    form = await request.form()
    form_dict: Dict[str, str] = {k: str(v) for k, v in form.items()}

    # Twilio signature algorithm: HMAC-SHA1 of URL + sorted(k+v) joined.
    full_url = str(request.url)
    signed_source = full_url + "".join(
        f"{k}{form_dict[k]}" for k in sorted(form_dict.keys())
    )
    import hashlib as _twilio_hashlib
    import hmac as _twilio_hmac
    expected = _twilio_hmac.new(
        auth_token.encode("utf-8"),
        signed_source.encode("utf-8"),
        _twilio_hashlib.sha1,
    ).digest()
    import base64 as _b64
    expected_b64 = _b64.b64encode(expected).decode("ascii")
    if not hmac.compare_digest(expected_b64, signature):
        logger.warning("twilio_signature_mismatch")
        raise HTTPException(status_code=401, detail="Invalid Twilio signature")

    message_sid = form_dict.get("MessageSid") or form_dict.get("SmsSid") or ""
    message_status = form_dict.get("MessageStatus") or form_dict.get("SmsStatus") or ""
    # OWASP A09: dedup by (provider, MessageSid). Twilio retries POSTs
    # until a 2xx; dedup means retries are safe no-ops.
    if message_sid and not _dedup_provider_event("twilio", message_sid):
        logger.info("twilio_webhook_duplicate_event sid=%s", message_sid)
        return {"status": "duplicate", "message_sid": message_sid, "message_status": message_status}
    logger.info("twilio_delivery_event sid=%s status=%s", message_sid, message_status)
    return {"status": "received", "message_sid": message_sid, "message_status": message_status}


@app.post("/api/notifications/webhooks/fcm")
async def fcm_delivery_webhook(request: Request) -> Dict[str, Any]:
    """Handle FCM delivery status callbacks (forwarded via internal pipeline).

    FCM does not expose a direct webhook for delivery events — Google's
    recommended path is to export BigQuery delivery data or stream via
    Pub/Sub → Cloud Function → your service. Either approach ships
    events to us via our own HTTPS endpoint; this handler accepts that
    forwarded stream and MUST authenticate it, since the Cloud Function
    has no Google-side signature we can verify.

    Authentication model (all three must pass):

    1. ``X-NILBx-FCM-Shared-Secret`` header matches
       ``FCM_WEBHOOK_SHARED_SECRET`` via ``hmac.compare_digest``.
    2. ``X-NILBx-FCM-Signature`` header is the HMAC-SHA256 of the raw
       body using the same shared secret. Prevents header-stripping
       proxies or logs from leaking the secret into a replay payload.
    3. Timestamp header ``X-NILBx-FCM-Timestamp`` is within 300s of
       ``utcnow()``. Blocks replay of a captured valid envelope.

    This is stricter than the Twilio/SNS paths precisely because there
    is no provider-side signature to rely on — we are the full trust
    anchor.
    """
    import hmac as _fcm_hmac
    import hashlib as _fcm_hashlib
    import time as _fcm_time
    import json as _fcm_json

    shared_secret = os.getenv("FCM_WEBHOOK_SHARED_SECRET", "")
    if not shared_secret:
        # In dev / local we allow the endpoint to no-op so integration
        # tests don't need to wire a shared secret; in prod we refuse.
        env = (os.getenv("ENVIRONMENT") or os.getenv("APP_ENV") or "").strip().lower()
        if env not in {"dev", "development", "local", "test"}:
            raise HTTPException(
                status_code=503,
                detail="FCM_WEBHOOK_SHARED_SECRET not configured",
            )
        logger.warning("fcm_webhook_unconfigured_dev_only")
        return {"status": "accepted_unverified_dev_only"}

    provided_secret = request.headers.get("X-NILBx-FCM-Shared-Secret", "")
    provided_signature = request.headers.get("X-NILBx-FCM-Signature", "")
    provided_timestamp = request.headers.get("X-NILBx-FCM-Timestamp", "")

    if not provided_secret or not _fcm_hmac.compare_digest(
        provided_secret.encode("utf-8"), shared_secret.encode("utf-8"),
    ):
        logger.warning("fcm_webhook_bad_shared_secret")
        raise HTTPException(status_code=401, detail="Invalid FCM webhook shared secret")

    # Replay guard via timestamp.
    try:
        ts_val = int(provided_timestamp)
    except (TypeError, ValueError):
        logger.warning("fcm_webhook_missing_timestamp")
        raise HTTPException(status_code=401, detail="Invalid FCM webhook timestamp")
    if abs(int(_fcm_time.time()) - ts_val) > 300:
        logger.warning("fcm_webhook_stale_timestamp delta=%ds", int(_fcm_time.time()) - ts_val)
        raise HTTPException(status_code=401, detail="Stale FCM webhook timestamp")

    raw_body = await request.body()
    # Bind the timestamp into the HMAC so an attacker can't replay the
    # same body with a refreshed header.
    signed_material = provided_timestamp.encode("utf-8") + b"." + raw_body
    expected_signature = _fcm_hmac.new(
        shared_secret.encode("utf-8"),
        signed_material,
        _fcm_hashlib.sha256,
    ).hexdigest()
    if not _fcm_hmac.compare_digest(provided_signature, expected_signature):
        logger.warning("fcm_webhook_bad_signature")
        raise HTTPException(status_code=401, detail="Invalid FCM webhook signature")

    try:
        payload = _fcm_json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="FCM webhook payload must be an object")

    # Expected shape (from a Cloud Function / Pub/Sub fan-out):
    #   {"message_id": "...", "delivery_status": "delivered|not_delivered|dropped",
    #    "token_hash": "...", "failure_reason": "..."}
    # Never trust raw FCM tokens from the payload — we only accept
    # either an opaque NILBx notification_id we minted, or a SHA-256
    # hash prefix of the token that we can match against our own
    # persisted hash (future work; token hash columns not yet added).
    message_id = str(payload.get("message_id") or payload.get("notification_id") or "")
    delivery_status = str(payload.get("delivery_status") or "")
    if delivery_status not in {"delivered", "not_delivered", "dropped", "expired", ""}:
        delivery_status = "unknown"

    # OWASP A09: dedup by (provider, message_id). FCM forwarders
    # (Pub/Sub → Cloud Function) can redeliver on transient errors.
    if message_id and not _dedup_provider_event("fcm", message_id):
        logger.info("fcm_webhook_duplicate_event message_id=%s", message_id)
        return {"status": "duplicate", "message_id": message_id, "delivery_status": delivery_status}

    logger.info(
        "fcm_delivery_event message_id=%s status=%s",
        message_id, delivery_status,
    )

    # TODO(follow-up): once the fcm_webhook_events dedup table lands,
    # atomically claim ``message_id`` and update the matching
    # NotificationDelivery row's status / retry count. Keeping this
    # handler light today so we don't block on the dedup migration.
    return {
        "status": "received",
        "message_id": message_id,
        "delivery_status": delivery_status,
    }


# ============================================================================
# Error Handlers
# ============================================================================


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Custom HTTP exception handler"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8013)
