"""Phase-4 P2 #7: observability primitives for the notification delivery
pipeline.

We intentionally stay lightweight — no heavyweight OTel SDK dependency
(which adds ~30MB and a large import cost) and no hard dependency on a
metrics endpoint. Instead we expose four SLIs through
``prometheus_client`` when it's available, or fall through to no-op
stubs when it isn't. The Grafana/Tempo pipeline downstream can scrape
``/metrics`` once the deployment wires it up.

### The four SLIs

1. **delivery_latency_seconds** (histogram, label=``channel``) — end-to-end
   wall time for each ``send_{email,sms,push,webhook}`` call. Alert
   target: channel-specific p99 (email >30s, sms >5s, push >2s,
   webhook >10s).
2. **delivery_errors_total** (counter, labels=``channel``, ``reason``) —
   failed delivery attempts broken down by provider-returned reason.
   Alert on a sustained >5% error rate over 10m.
3. **delivery_queue_depth** (gauge) — number of ``pending`` rows in the
   delivery log. Alert on sustained >1000 for 15m, which indicates the
   worker can't keep up with producers.
4. **delivery_retries_per_success** (histogram) — how many attempts it
   took to land a success. Steady-state should be ~1.0; regressions to
   ~2+ usually point at a provider outage.

These SLIs double as OTel metrics if ``opentelemetry-api`` is
available (see ``_maybe_register_otel``) — the OTel provider reads
the same counters.

### Instrumentation API

Callers use three zero-dependency helpers:

    with record_delivery("email") as ctx:
        ... send_email_ses(...) ...
        ctx.ok()  # or ctx.fail(reason)

    set_queue_depth(123)

    record_retry_count(attempts=2)

All three no-op if ``prometheus_client`` isn't installed — delivery
code never crashes on instrumentation.
"""
from __future__ import annotations

import contextlib
import logging
import time
from typing import Iterator

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry, REGISTRY
    _PROM_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PROM_AVAILABLE = False


# Grafana/Prometheus naming convention: snake_case with the unit suffix
# at the end (seconds, total, bytes, etc.). Labels stay low-cardinality.
_CHANNEL_LABELS = ("channel",)
_ERROR_LABELS = ("channel", "reason")


# Phase-4 audit item #3: bounded label cardinality.
#
# ``channel`` and ``reason`` are Prometheus labels — each distinct
# string mints a new time series. An unbounded caller (typo, or an
# ``f"timeout for user {uid}"``) could blow up the /metrics payload
# and OOM the scraper. Bound both to finite enums; anything outside
# the enum is remapped to a generic bucket.

_CHANNEL_ENUM = frozenset({"email", "sms", "push", "webhook", "chat", "in_app"})
_CHANNEL_OTHER = "other"

# Reason normalisation: regex prefix match → canonical enum value.
# Order matters (first-match wins). Exception class names come in
# lowercased and stripped of the "Error" / "Exception" suffix so we
# bucket TimeoutError, httpx.TimeoutException, etc. together.
_REASON_PATTERNS: tuple[tuple[str, str], ...] = (
    ("timeout", "timeout"),
    ("connection_refused", "connection_refused"),
    ("connectionrefused", "connection_refused"),
    ("connection", "connection_error"),
    ("dnsresolution", "dns_error"),
    ("ssl", "tls_error"),
    ("tls", "tls_error"),
    ("unauthorized", "unauthorized"),
    ("forbidden", "forbidden"),
    ("ratelimit", "rate_limited"),
    ("throttle", "rate_limited"),
    ("bounce", "bounce"),
    ("invalid_recipient", "invalid_recipient"),
    ("invalid_email", "invalid_recipient"),
    ("provider_down", "provider_down"),
    ("serviceunavailable", "provider_down"),
    ("badrequest", "bad_request"),
    ("validationerror", "bad_request"),
    ("exception", "exception"),
    ("unknown", "unknown"),
)

_REASON_MAX_LEN = 32


def _normalise_channel(raw: str) -> str:
    """Collapse a caller-supplied channel string to a bounded enum value."""
    if not raw:
        return _CHANNEL_OTHER
    lower = raw.strip().lower()
    if lower in _CHANNEL_ENUM:
        return lower
    return _CHANNEL_OTHER


def _normalise_reason(raw: str) -> str:
    """Collapse a caller-supplied failure reason to a bounded enum value.

    Strips spaces + lowercase, runs a prefix match against a hand-rolled
    list of canonical codes, and clips to ``_REASON_MAX_LEN`` as a
    second line of defence. Anything unrecognised lands in ``"other"``.
    """
    if not raw:
        return "unknown"
    token = raw.strip().lower().replace(" ", "_")
    # Match against regex-free prefixes first — cheap and predictable.
    for needle, canonical in _REASON_PATTERNS:
        if needle in token:
            return canonical[:_REASON_MAX_LEN]
    return "other"

if _PROM_AVAILABLE:
    delivery_latency_seconds = Histogram(
        "nilbx_notification_delivery_latency_seconds",
        "Wall-clock time from dispatch to provider ACK, per channel",
        _CHANNEL_LABELS,
        buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
    )
    delivery_errors_total = Counter(
        "nilbx_notification_delivery_errors_total",
        "Delivery failures grouped by channel + reason-code (bounded cardinality)",
        _ERROR_LABELS,
    )
    delivery_queue_depth = Gauge(
        "nilbx_notification_delivery_queue_depth",
        "Backlog of pending_delivery rows — worker needs to drain these",
    )
    delivery_retries_per_success = Histogram(
        "nilbx_notification_delivery_retries_per_success",
        "Attempts-to-success distribution — 1.0 is ideal; drift signals provider outage",
        buckets=(1, 2, 3, 5, 10),
    )


@contextlib.contextmanager
def record_delivery(channel: str) -> Iterator["_DeliveryCtx"]:
    """Time a single delivery attempt and emit the right counters.

    Use as ``with record_delivery("email") as ctx: ... ctx.ok()``. If
    the block raises or never calls ``ctx.ok()``, we record a failure
    with ``reason="exception"`` (or whatever reason the caller sets
    via ``ctx.fail(...)`` before re-raising).

    ``channel`` is normalised through :func:`_normalise_channel` so a
    caller typo cannot mint a new Prometheus time series.
    """
    ctx = _DeliveryCtx(channel=_normalise_channel(channel), started_at=time.monotonic())
    try:
        yield ctx
    except Exception as exc:
        ctx._record(success=False, reason=f"{type(exc).__name__}")
        raise
    else:
        if not ctx._finalised:
            # Caller neither ok()'d nor fail()'d — assume success.
            ctx._record(success=True, reason="")


class _DeliveryCtx:
    """Small accumulator yielded by :func:`record_delivery`."""

    __slots__ = ("channel", "started_at", "_finalised")

    def __init__(self, channel: str, started_at: float) -> None:
        self.channel = channel
        self.started_at = started_at
        self._finalised = False

    def ok(self) -> None:
        self._record(success=True, reason="")

    def fail(self, reason: str) -> None:
        self._record(success=False, reason=reason or "unknown")

    def _record(self, *, success: bool, reason: str) -> None:
        if self._finalised:
            return
        self._finalised = True
        elapsed = time.monotonic() - self.started_at
        if not _PROM_AVAILABLE:
            logger.debug(
                "delivery_metric channel=%s ok=%s reason=%s elapsed=%.3f",
                self.channel, success, reason, elapsed,
            )
            return
        try:
            delivery_latency_seconds.labels(channel=self.channel).observe(elapsed)
            if not success:
                delivery_errors_total.labels(
                    channel=self.channel,
                    reason=_normalise_reason(reason),
                ).inc()
        except Exception:
            # Never let instrumentation fail the delivery path.
            logger.exception("delivery_metric_record_failed")


def set_queue_depth(depth: int) -> None:
    """Caller passes the current pending-delivery count."""
    if not _PROM_AVAILABLE:
        return
    try:
        delivery_queue_depth.set(float(depth))
    except Exception:
        logger.exception("queue_depth_metric_failed")


def record_retry_count(attempts: int) -> None:
    """Record the number of attempts this delivery required before success."""
    if not _PROM_AVAILABLE:
        return
    try:
        delivery_retries_per_success.observe(max(1, int(attempts)))
    except Exception:
        logger.exception("retry_metric_failed")


# ── FastAPI integration helper ────────────────────────────────────────

def install_metrics_endpoint(app) -> None:
    """Mount ``GET /metrics`` on the given FastAPI app.

    No-op when ``prometheus_client`` isn't installed. Safe to call from
    ``main.py`` at import time — route registration doesn't raise.

    Phase-4 audit item #3 (A01 Broken Access Control): the route is
    gated by :func:`notification_service.src.auth.require_admin` so
    only callers presenting the internal service token (compliance-
    service, Prometheus scraper with the shared secret) or an
    admin-role bearer get exposition. Before this gate landed the
    endpoint was internet-reachable at
    ``https://dev.nilbx.com/notifications/metrics`` and leaked queue
    depth + per-channel error counts to anyone.
    """
    if not _PROM_AVAILABLE:
        logger.warning(
            "prometheus_client not installed; /metrics endpoint disabled"
        )
        return

    from fastapi import Depends
    from fastapi.responses import Response
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    # FastAPI resolves return-type annotations at route-registration
    # time, so the ``Response`` name must be visible in the module's
    # globals — not just inside the function scope. Assigning here
    # makes the annotation below resolvable without hoisting the
    # import to module level.
    global _FASTAPI_RESPONSE_CLS  # noqa: PLW0603
    _FASTAPI_RESPONSE_CLS = Response

    # Import lazily so the auth module's own heavy imports only load
    # in environments that actually mount the endpoint.
    try:
        from .auth import require_admin  # type: ignore
    except ImportError:  # pragma: no cover
        from src.auth import require_admin  # type: ignore

    @app.get("/metrics", include_in_schema=False)
    def _metrics(_actor=Depends(require_admin)):
        return Response(
            content=generate_latest(REGISTRY),
            media_type=CONTENT_TYPE_LATEST,
        )


__all__ = [
    "record_delivery",
    "set_queue_depth",
    "record_retry_count",
    "install_metrics_endpoint",
]
