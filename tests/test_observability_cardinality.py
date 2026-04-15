"""Prometheus label-cardinality contract tests (Phase-4 audit item #3).

Pins the bounded-cardinality guarantee for the four delivery SLIs:
- ``channel`` label must be one of a finite enum
  (email, sms, push, webhook). Unknown values are remapped to
  ``"other"`` so a caller typo can't explode the metric namespace.
- ``reason`` label must be one of a finite enum of normalised
  failure codes. Caller-supplied free-form reasons are mapped to the
  closest enum bucket or ``"other"`` — the 48-char truncation we had
  before is insufficient because the tail of a long reason can still
  vary per-request.

Without these bounds, a single buggy caller passing
``f"timeout for user {uid}"`` to ``ctx.fail(...)`` would mint one
Prometheus time series per uid and OOM the scraper.
"""
from __future__ import annotations

import importlib

import pytest

pytest.importorskip("prometheus_client")


@pytest.fixture
def obs():
    """Return a freshly-reloaded observability module with a clean registry.

    prometheus_client's default registry is global; unregister any
    existing collectors from this module before reload so each test
    starts with zeroed counters.
    """
    import importlib
    from prometheus_client import REGISTRY
    try:
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
    except ImportError:
        pass
    import src.observability as o
    importlib.reload(o)
    return o


class TestChannelEnum:
    def test_record_delivery_rejects_unknown_channel_value(self, obs):
        # Unknown channel strings must not mint a brand-new time
        # series. The enforced behaviour: normalise to "other".
        with obs.record_delivery("weird-channel-42") as ctx:
            ctx.ok()
        # Pull the children label sets from the histogram directly.
        channels = {
            tuple(sorted(m.labels.items()))
            for m in obs.delivery_latency_seconds.collect()[0].samples
            if m.labels
        }
        seen_channels = {dict(m).get("channel") for m in channels}
        assert "weird-channel-42" not in seen_channels
        assert "other" in seen_channels

    def test_record_delivery_accepts_known_channel_enum(self, obs):
        for ch in ("email", "sms", "push", "webhook"):
            with obs.record_delivery(ch) as ctx:
                ctx.ok()
        samples = obs.delivery_latency_seconds.collect()[0].samples
        seen = {s.labels.get("channel") for s in samples if s.labels}
        for ch in ("email", "sms", "push", "webhook"):
            assert ch in seen, f"channel {ch!r} should be recorded"

    def test_channel_case_normalised_to_lowercase(self, obs):
        with obs.record_delivery("EMAIL") as ctx:
            ctx.ok()
        samples = obs.delivery_latency_seconds.collect()[0].samples
        seen = {s.labels.get("channel") for s in samples if s.labels}
        assert "email" in seen
        assert "EMAIL" not in seen


class TestReasonEnum:
    def test_ctx_fail_reason_normalised_to_finite_set(self, obs):
        # A free-form reason must map to a bounded enum; the tail of
        # the string (e.g. a uid) must not leak into the label.
        with obs.record_delivery("email") as ctx:
            ctx.fail("timeout for user 12345 attempting send")
        samples = obs.delivery_errors_total.collect()[0].samples
        seen_reasons = {s.labels.get("reason") for s in samples if s.labels}
        # Must NOT contain the raw caller string or its truncation.
        assert not any("12345" in r for r in seen_reasons if r)
        # The finite enum contract: at least one of the normalised codes.
        assert seen_reasons & {"timeout", "other"}, (
            f"reason must map to finite enum; got {seen_reasons}"
        )

    def test_caller_supplied_reason_with_per_request_substring_does_not_create_new_label(self, obs):
        # Three different uids in the reason must collapse to one series.
        for uid in (1, 2, 3):
            with obs.record_delivery("email") as ctx:
                ctx.fail(f"connection_refused for user {uid}")
        samples = obs.delivery_errors_total.collect()[0].samples
        seen = {s.labels.get("reason") for s in samples if s.labels}
        # At most one unique reason label for this family.
        offending = {r for r in seen if r and any(str(u) in r for u in (1, 2, 3))}
        assert not offending, f"per-uid reason leaked into label: {offending}"

    def test_exception_class_name_normalised(self, obs):
        # If the context manager catches a live exception, the class
        # name becomes the reason — it IS finite (at most ~dozens of
        # exception classes), so keep as-is but lowercased.
        try:
            with obs.record_delivery("email"):
                raise ConnectionRefusedError("nope")
        except ConnectionRefusedError:
            pass
        samples = obs.delivery_errors_total.collect()[0].samples
        seen = {s.labels.get("reason") for s in samples if s.labels}
        assert any(r and "connection" in r.lower() for r in seen), seen


class TestReasonLengthCap:
    def test_reason_never_exceeds_32_chars(self, obs):
        # Double layer: even if normalisation misses something, the
        # final label value must be bounded.
        with obs.record_delivery("email") as ctx:
            ctx.fail("x" * 200)
        samples = obs.delivery_errors_total.collect()[0].samples
        for s in samples:
            r = s.labels.get("reason")
            if r is not None:
                assert len(r) <= 32, (
                    f"reason label must be <=32 chars; got {len(r)}: {r!r}"
                )
