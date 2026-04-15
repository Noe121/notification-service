"""Authorisation contract for GET /metrics (Phase-4 audit item #3).

Before this audit the Prometheus ``/metrics`` endpoint was mounted on
the public FastAPI app with no auth dep — reachable at
``https://dev.nilbx.com/notifications/metrics``. Exposition leaked
queue depth, error counts, channel usage shape, and gave SSRF/DOS
fingerprinting to anyone on the internet.

Contract:
- GET /metrics without auth → 401.
- GET /metrics with a non-admin bearer → 403.
- GET /metrics with the X-Service-Token that require_admin trusts → 200 + text.
- GET /metrics with an admin-role bearer → 200 + text.
"""
from __future__ import annotations

import importlib

import pytest

pytest.importorskip("prometheus_client")

from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client_and_token(monkeypatch):
    # Same unregister dance as the other obs tests so the /metrics
    # mount doesn't duplicate-register on a fresh FastAPI instance.
    from prometheus_client import REGISTRY
    import src.observability as o
    for name in (
        "delivery_latency_seconds",
        "delivery_errors_total",
        "delivery_queue_depth",
        "delivery_retries_per_success",
    ):
        metric = getattr(o, name, None)
        if metric is not None:
            try:
                REGISTRY.unregister(metric)
            except KeyError:
                pass
    importlib.reload(o)

    # Deterministic service token so tests can present it.
    monkeypatch.setenv("NILBX_INTERNAL_SERVICE_TOKEN", "test-service-token-12345")
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "test-service-token-12345")
    # Reload auth so the module-level ``_INTERNAL_SERVICE_TOKEN`` picks
    # up this test's env var. Without the reload, an earlier test that
    # reloaded auth with a different token leaves its value cached,
    # and our service-token presentation here fails 401.
    import src.auth as _auth
    importlib.reload(_auth)

    app = FastAPI()
    o.install_metrics_endpoint(app)
    return TestClient(app), "test-service-token-12345"


class TestMetricsEndpointAuth:

    def test_metrics_returns_401_without_token(self, client_and_token):
        client, _ = client_and_token
        resp = client.get("/metrics")
        assert resp.status_code in (401, 403), (
            f"/metrics without auth must be 401/403, got {resp.status_code}: {resp.text[:200]}"
        )

    def test_metrics_with_service_token_returns_200(self, client_and_token):
        client, tok = client_and_token
        resp = client.get("/metrics", headers={"X-Service-Token": tok})
        assert resp.status_code == 200
        assert "nilbx_notification_delivery" in resp.text

    def test_metrics_rejects_wrong_service_token(self, client_and_token):
        client, _ = client_and_token
        resp = client.get("/metrics", headers={"X-Service-Token": "wrong-token"})
        assert resp.status_code in (401, 403)

    def test_metrics_rejects_random_bearer_token(self, client_and_token):
        """A random bearer that isn't the service token and doesn't validate
        with auth-service must be rejected — not silently open."""
        client, _ = client_and_token
        resp = client.get(
            "/metrics",
            headers={"Authorization": "Bearer random-unrelated-token"},
        )
        # Whichever rejection path fires (401/403 from require_admin, or
        # 503 when the auth-service validation call can't reach network)
        # is acceptable — the invariant is "never 200 without the real
        # service token".
        assert resp.status_code != 200, (
            f"/metrics must not accept an unrelated bearer; got {resp.status_code}"
        )

    def test_metrics_exposition_format_is_prometheus_text(self, client_and_token):
        client, tok = client_and_token
        resp = client.get("/metrics", headers={"X-Service-Token": tok})
        assert resp.status_code == 200
        ctype = resp.headers.get("content-type", "")
        assert ctype.startswith("text/plain") or "openmetrics" in ctype
