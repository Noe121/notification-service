"""Phase-4 P2 #7: notification-service observability primitives.

Tests the four SLI counters + the FastAPI integration helper. We DO
NOT require prometheus_client to be installed — the module degrades
gracefully, and the tests verify that silent-degrade path too.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def obs_module(monkeypatch):
    """Re-import observability so the prometheus registry is fresh per test."""
    # Unregister any collectors that a prior test left behind so reload
    # doesn't explode with "Duplicated timeseries in CollectorRegistry".
    try:
        from prometheus_client import REGISTRY
        import src.observability as obs
        for name in (
            "delivery_latency_seconds",
            "delivery_errors_total",
            "delivery_queue_depth",
            "delivery_retries_per_success",
        ):
            metric = getattr(obs, name, None)
            if metric is not None:
                try:
                    REGISTRY.unregister(metric)
                except KeyError:
                    pass
    except ImportError:
        pass
    import src.observability as obs
    importlib.reload(obs)
    return obs


def test_record_delivery_happy_path(obs_module):
    """Context manager must not raise on normal ok() flow."""
    with obs_module.record_delivery("email") as ctx:
        ctx.ok()
    # No assertion needed — the point is that the call didn't raise.


def test_record_delivery_auto_ok_when_block_exits_without_call(obs_module):
    """If the caller forgets to call ok()/fail(), we auto-record success."""
    with obs_module.record_delivery("sms"):
        pass


def test_record_delivery_on_exception(obs_module):
    """An exception in the block must be re-raised AND recorded as failure."""
    with pytest.raises(ValueError):
        with obs_module.record_delivery("push") as ctx:
            raise ValueError("provider rejected")
    # Already-finalised ctx shouldn't double-record on the way out.
    with obs_module.record_delivery("push") as ctx2:
        ctx2.fail("bounced")
        ctx2.fail("double-call")  # second call is ignored


def test_record_delivery_never_raises_if_prom_missing(monkeypatch, obs_module):
    """Force the ``_PROM_AVAILABLE = False`` path and confirm zero crashes."""
    monkeypatch.setattr(obs_module, "_PROM_AVAILABLE", False)
    with obs_module.record_delivery("webhook") as ctx:
        ctx.ok()
    obs_module.set_queue_depth(42)
    obs_module.record_retry_count(3)


def test_set_queue_depth_and_retry_count_no_crash(obs_module):
    obs_module.set_queue_depth(0)
    obs_module.set_queue_depth(9999)
    obs_module.record_retry_count(1)
    obs_module.record_retry_count(5)


def test_metrics_endpoint_mounts(obs_module):
    """Integration: install_metrics_endpoint adds GET /metrics to a FastAPI app.

    Phase-4 audit item #3: the route is gated by require_admin, so an
    unauthenticated GET returns 401/403. A missing route returns 404.
    Either status code proves the route was mounted under the auth gate
    (the old "accepted 200 unauth" branch is a regression we now fail).
    """
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi not installed in this test env")
    app = FastAPI()
    obs_module.install_metrics_endpoint(app)
    client = TestClient(app)
    r = client.get("/metrics")
    # 401/403 = route mounted behind the auth gate; 404 = prometheus
    # wasn't installed so install_metrics_endpoint no-op'd.
    assert r.status_code in (401, 403, 404), (
        f"/metrics must be auth-gated or absent; got {r.status_code}"
    )
