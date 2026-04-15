"""
Delivery Worker
Polls pending delivery logs and routes them through external providers.
"""

import asyncio
import json
import logging
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("notification-delivery-worker")

BASE_URL = os.getenv("NOTIFICATION_SERVICE_URL", "http://localhost:8013").rstrip("/")
POLL_INTERVAL_SECONDS = int(os.getenv("DELIVERY_WORKER_POLL_SECONDS", "15"))
BATCH_SIZE = int(os.getenv("DELIVERY_WORKER_BATCH_SIZE", "50"))
HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# OWASP A10: SSRF hardening for webhook delivery.
WEBHOOK_URL_MAX_LENGTH = int(os.getenv("WEBHOOK_URL_MAX_LENGTH", "2048"))
WEBHOOK_POST_TIMEOUT = httpx.Timeout(3.0, connect=3.0)


async def fetch_pending_deliveries(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    resp = await client.get(f"{BASE_URL}/delivery/pending", params={"limit": BATCH_SIZE})
    resp.raise_for_status()
    payload = resp.json()
    return payload.get("deliveries", [])


async def fetch_notification(client: httpx.AsyncClient, notification_id: int) -> Optional[Dict[str, Any]]:
    resp = await client.get(f"{BASE_URL}/notifications/{notification_id}")
    if resp.status_code == 404:
        logger.warning("Notification %s not found, skipping delivery", notification_id)
        return None
    resp.raise_for_status()
    return resp.json()


async def fetch_channel(client: httpx.AsyncClient, channel_id: int) -> Optional[Dict[str, Any]]:
    resp = await client.get(f"{BASE_URL}/channels/{channel_id}")
    if resp.status_code == 404:
        logger.warning("Channel %s not found, skipping delivery", channel_id)
        return None
    resp.raise_for_status()
    return resp.json()


async def report_success(client: httpx.AsyncClient, delivery_id: int, metadata: Dict[str, Any]) -> None:
    payload: Dict[str, Any] = {}
    if metadata.get("external_message_id"):
        payload["external_message_id"] = metadata["external_message_id"]
    await client.post(f"{BASE_URL}/delivery/{delivery_id}/success", json=payload)


async def report_failure(
    client: httpx.AsyncClient,
    delivery_id: int,
    error_message: str,
    status_code: Optional[int] = None,
    should_retry: bool = True,
) -> None:
    payload: Dict[str, Any] = {
        "error_message": error_message,
        "should_retry": should_retry,
    }
    if status_code is not None:
        payload["status_code"] = status_code
    await client.post(f"{BASE_URL}/delivery/{delivery_id}/failure", json=payload)


async def send_email(channel: Dict[str, Any], notification: Dict[str, Any]) -> Dict[str, Any]:
    """Send email via AWS SES (real provider).

    Falls back to mock in dev/test environments if SES is not configured.
    PII: channel_value (email) is passed to SES but never logged directly.
    """
    env = os.getenv("ENVIRONMENT", "dev").strip().lower()

    # Use real provider in non-dev environments
    if env not in ("local", "test", "dev", "development"):
        try:
            from .provider_clients import send_email_ses
            return await send_email_ses(channel, notification)
        except ImportError:
            pass  # Fall through to mock
        except Exception:
            logger.exception("SES email delivery failed, notification=%s", notification["id"])
            raise

    # Dev/test mock fallback
    logger.info("Mock sending email for notification=%s", notification["id"])
    await asyncio.sleep(0.1)
    return {
        "external_message_id": f"email-{notification['id']}-{channel['id']}",
        "response_metadata": {"provider": "mock-email"},
    }


async def send_sms(channel: Dict[str, Any], notification: Dict[str, Any]) -> Dict[str, Any]:
    """Send SMS via Twilio (real provider).

    Falls back to mock in dev/test environments if Twilio is not configured.
    PII: channel_value (phone) is passed to Twilio but never logged directly.
    """
    env = os.getenv("ENVIRONMENT", "dev").strip().lower()

    if env not in ("local", "test", "dev", "development"):
        try:
            from .provider_clients import send_sms_twilio
            return await send_sms_twilio(channel, notification)
        except ImportError:
            pass
        except Exception:
            logger.exception("Twilio SMS delivery failed, notification=%s", notification["id"])
            raise

    logger.info("Mock sending SMS for notification=%s", notification["id"])
    await asyncio.sleep(0.1)
    return {
        "external_message_id": f"sms-{notification['id']}-{channel['id']}",
        "response_metadata": {"provider": "mock-sms"},
    }


async def send_push(channel: Dict[str, Any], notification: Dict[str, Any]) -> Dict[str, Any]:
    """Send push notification via Firebase Cloud Messaging (real provider).

    Falls back to mock in dev/test environments if FCM is not configured.
    PII: channel_value (FCM token) is passed to Firebase but never logged directly.
    """
    env = os.getenv("ENVIRONMENT", "dev").strip().lower()

    if env not in ("local", "test", "dev", "development"):
        try:
            from .provider_clients import send_push_fcm
            return await send_push_fcm(channel, notification)
        except ImportError:
            pass
        except Exception:
            logger.exception("FCM push delivery failed, notification=%s", notification["id"])
            raise

    logger.info("Mock sending push for notification=%s", notification["id"])
    await asyncio.sleep(0.1)
    return {
        "external_message_id": f"push-{notification['id']}-{channel['id']}",
        "response_metadata": {"provider": "mock-push"},
    }


def _validate_webhook_url(url: str) -> None:
    """Validate a webhook URL against SSRF risks.

    Blocks: private IPs, AWS metadata, localhost, non-HTTPS schemes,
    and hostnames that resolve (via DNS) to ANY address in a private /
    loopback / reserved / link-local / multicast range — which closes
    the DNS-rebind + CNAME-to-private attacks the prior check missed.

    This is called at both validation time and immediately before the
    actual POST — there is no caching between the two checks, so a DNS
    rebind between validate and send is detected on the second call.
    """
    from urllib.parse import urlparse
    import ipaddress
    import socket

    if not url or not isinstance(url, str):
        raise ValueError("Webhook URL missing")
    if len(url) > WEBHOOK_URL_MAX_LENGTH:
        raise ValueError(
            f"Webhook URL exceeds max length of {WEBHOOK_URL_MAX_LENGTH} bytes"
        )

    parsed = urlparse(url)

    # Require HTTPS
    if parsed.scheme not in ("https",):
        raise ValueError(f"Webhook URL must use HTTPS, got: {parsed.scheme}")

    hostname = parsed.hostname or ""
    if not hostname:
        raise ValueError("Webhook URL missing hostname")

    # Block literal localhost / loopback hostnames BEFORE any DNS work —
    # saves a resolver round-trip and avoids depending on resolv.conf
    # being sane.
    if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        raise ValueError("Webhook URL cannot target localhost")

    # Block AWS metadata endpoint (literal match)
    if hostname in ("169.254.169.254", "fd00:ec2::254"):
        raise ValueError("Webhook URL cannot target cloud metadata endpoint")

    # Block internal service hostnames by suffix
    blocked_suffixes = (".local", ".internal", ".svc.cluster.local")
    if any(hostname.endswith(suffix) for suffix in blocked_suffixes):
        raise ValueError(f"Webhook URL cannot target internal hostname: {hostname}")

    # Resolve EVERY A/AAAA record the hostname maps to. Reject if ANY
    # of them falls into a forbidden range (DNS rebind defense: attacker
    # can't hide a private IP behind a single CNAME if we check all
    # answers).
    try:
        addrinfos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValueError(f"Webhook URL hostname does not resolve: {hostname}") from exc

    if not addrinfos:
        raise ValueError(f"Webhook URL hostname returned no addresses: {hostname}")

    for info in addrinfos:
        sockaddr = info[4]
        addr_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(addr_str)
        except ValueError:
            raise ValueError(f"Webhook URL DNS answer is not a valid IP: {addr_str}")
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(
                f"Webhook URL resolves to forbidden address range: {addr_str}"
            )


async def send_webhook(channel: Dict[str, Any], notification: Dict[str, Any]) -> Dict[str, Any]:
    """Send a webhook notification to an external URL.

    Security (OWASP A10):
      * URL validated against SSRF risks (scheme, hostname suffixes,
        DNS resolution for EVERY returned IP) before dispatch.
      * URL re-validated immediately before the POST (TOCTOU defense —
        DNS rebind between validate and send is caught).
      * Redirects are NOT followed; the validator can't anticipate
        redirect targets, so any 30x is a failure.
      * Short timeout (3s) bounds the request.
    """
    target_url = channel["channel_value"]
    _validate_webhook_url(target_url)

    # TOCTOU defense: re-resolve + re-validate right before the send so a
    # DNS rebind that flipped the answer between the two calls is caught.
    _validate_webhook_url(target_url)

    async with httpx.AsyncClient(
        timeout=WEBHOOK_POST_TIMEOUT, follow_redirects=False
    ) as client:
        resp = await client.post(
            target_url,
            headers={"Content-Type": "application/json"},
            content=json.dumps(
                {
                    "notification_id": notification["id"],
                    "title": notification.get("title"),
                    "message": notification.get("message"),
                    "data_payload": notification.get("data_payload"),
                }
            ),
        )
        resp.raise_for_status()
        return {
            "external_message_id": resp.headers.get("X-Request-Id") or f"webhook-{notification['id']}",
            "response_metadata": {"status_code": resp.status_code},
        }


CHANNEL_HANDLERS = {
    "email": send_email,
    "sms": send_sms,
    "push": send_push,
    "webhook": send_webhook,
}


async def process_delivery(
    delivery: Dict[str, Any],
    client: httpx.AsyncClient,
    channel_cache: Dict[int, Dict[str, Any]],
) -> None:
    delivery_id = delivery["id"]
    channel_id = delivery["channel_id"]

    channel = channel_cache.get(channel_id)
    if channel is None:
        channel = await fetch_channel(client, channel_id)
        if channel is None:
            await report_failure(client, delivery_id, "Channel metadata missing")
            return
        channel_cache[channel_id] = channel

    notification = await fetch_notification(client, delivery["notification_id"])
    if notification is None:
        await report_failure(client, delivery_id, "Notification metadata missing", should_retry=False)
        return

    handler = CHANNEL_HANDLERS.get(channel["channel_type"])
    if handler is None:
        error = f"Unsupported channel type {channel['channel_type']}"
        logger.error(error)
        await report_failure(client, delivery_id, error, should_retry=False)
        return

    # Phase-4 P2 #7: wrap the delivery call in the SLI recorder. The
    # context manager times the call, emits the latency histogram, and
    # — on exception — increments the error counter with a bounded
    # reason code. Instrumentation failures never propagate (observability
    # must not break delivery).
    try:
        from ..observability import record_delivery
    except ImportError:  # module not on path in some test setups
        record_delivery = None

    try:
        if record_delivery is None:
            result = await handler(channel, notification)
        else:
            with record_delivery(channel["channel_type"]) as _m:
                try:
                    result = await handler(channel, notification)
                    _m.ok()
                except Exception:
                    _m.fail("handler_exception")
                    raise
        await report_success(client, delivery_id, result)
    except Exception as exc:
        logger.exception("Delivery %s failed for channel %s", delivery_id, channel["channel_type"])
        await report_failure(client, delivery_id, str(exc))


async def worker_loop() -> None:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        while True:
            try:
                deliveries = await fetch_pending_deliveries(client)
            except Exception as exc:
                logger.error("Failed fetching pending deliveries: %s", exc)
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            # Phase-4 P2 #7: publish queue depth gauge every poll so the
            # alerting rule ("pending > 1000 for 15m") can fire even
            # when the worker is healthy but throughput-constrained.
            try:
                from ..observability import set_queue_depth
                set_queue_depth(len(deliveries))
            except Exception:
                pass

            if not deliveries:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for entry in deliveries:
                grouped[entry["delivery_channel"]].append(entry)

            channel_cache: Dict[int, Dict[str, Any]] = {}

            for channel_type, items in grouped.items():
                for delivery in items:
                    await process_delivery(delivery, client, channel_cache)

            await asyncio.sleep(POLL_INTERVAL_SECONDS)


def configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("DELIVERY_WORKER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main() -> None:
    configure_logging()
    logger.info("Starting delivery worker (polling %s every %d seconds)", BASE_URL, POLL_INTERVAL_SECONDS)
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()
