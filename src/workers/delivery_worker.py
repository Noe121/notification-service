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
    logger.info("Mock sending email to %s for notification %s", channel["channel_value"], notification["id"])
    await asyncio.sleep(0.1)
    return {
        "external_message_id": f"email-{notification['id']}-{channel['id']}",
        "response_metadata": {"provider": "mock-email", "channel": channel["channel_value"]},
    }


async def send_sms(channel: Dict[str, Any], notification: Dict[str, Any]) -> Dict[str, Any]:
    logger.info("Mock sending SMS to %s for notification %s", channel["channel_value"], notification["id"])
    await asyncio.sleep(0.1)
    return {
        "external_message_id": f"sms-{notification['id']}-{channel['id']}",
        "response_metadata": {"provider": "mock-sms", "channel": channel["channel_value"]},
    }


async def send_push(channel: Dict[str, Any], notification: Dict[str, Any]) -> Dict[str, Any]:
    logger.info("Mock sending push to %s for notification %s", channel["channel_value"], notification["id"])
    await asyncio.sleep(0.1)
    return {
        "external_message_id": f"push-{notification['id']}-{channel['id']}",
        "response_metadata": {"provider": "mock-push", "channel": channel["channel_value"]},
    }


async def send_webhook(channel: Dict[str, Any], notification: Dict[str, Any]) -> Dict[str, Any]:
    target_url = channel["channel_value"]
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
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

    try:
        result = await handler(channel, notification)
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
