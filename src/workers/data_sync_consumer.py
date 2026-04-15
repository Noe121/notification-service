"""
Data Sync Consumer Worker
Polls SQS queue for data sync completion events from AWS Lambda pipelines.
Handles cache refresh for NCAA schools, transfer portal, and high school datasets.
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional
from datetime import datetime
import boto3
from botocore.exceptions import ClientError

# OWASP A08: inbound event signature verification. Every SNS sender
# (payment-service, deliverable-service, live-commerce-service) HMAC-signs
# its event payload; notification-service MUST verify before acting on the
# event. Unverified events are dropped + logged + deleted from SQS.
from src.event_verification import (
    EventSignatureError,
    require_event_hmac_key,
    signature_required,
    verify_signed_event,
)

logger = logging.getLogger("data-sync-consumer")

# SQS Configuration
SQS_QUEUE_URL = os.getenv("DATA_SYNC_EVENTS_QUEUE_URL", "")
POLL_INTERVAL_SECONDS = int(os.getenv("DATA_SYNC_POLL_SECONDS", "30"))
BATCH_SIZE = int(os.getenv("DATA_SYNC_BATCH_SIZE", "10"))
MAX_WAIT_TIME = 20  # Long polling

# Admin Dashboard URL for cache refresh triggers
ADMIN_DASHBOARD_URL = os.getenv("ADMIN_DASHBOARD_URL", "http://localhost:8000").rstrip("/")

# Initialize SQS client
sqs_client = boto3.client("sqs", region_name=os.getenv("AWS_REGION", "us-east-1"))

# Per-sender HMAC keys. require_event_hmac_key() boot-fails outside dev
# when any key is missing; in dev it logs a warning and returns "" so the
# verifier fails closed on the actual verify step.
_PAYMENT_KEY = require_event_hmac_key("PAYMENT_EVENT_HMAC_KEY")
_DELIVERABLE_KEY = require_event_hmac_key("DELIVERABLE_EVENT_HMAC_KEY")
_COMMERCE_KEY = require_event_hmac_key("COMMERCE_EVENT_HMAC_KEY")

# Prefix → key lookup. The data-sync queue is subscribed to events from
# multiple senders via SNS fan-out; route verification to the right key by
# event_type prefix. contract.obligation.* / contract.fulfillment.* are
# emitted by contract-service but use the deliverable HMAC key (shared
# deployment pipeline).
_KEY_BY_PREFIX = {
    "payment.": _PAYMENT_KEY,
    "deliverable.": _DELIVERABLE_KEY,
    "contract.obligation.": _DELIVERABLE_KEY,
    "contract.fulfillment.": _DELIVERABLE_KEY,
    "commerce.": _COMMERCE_KEY,
    # data_sync.completed events come from internal Lambda pipelines that
    # also sign with the deliverable/commerce keys depending on origin;
    # prefix match via "data_sync." routes to deliverable key by default.
    "data_sync.": _DELIVERABLE_KEY,
}


def _verify_inbound_event(payload: Dict[str, Any]) -> bool:
    """Return True iff payload carries a valid HMAC signature.

    OWASP A08: fails closed — missing/invalid/expired signatures are
    rejected. In dev, if ``NOTIFICATION_EVENT_SIGNATURE_REQUIRED=false``
    is set, verification is skipped with a single log line.
    """
    et = str(payload.get("event_type") or "")
    if not signature_required():
        logger.info("inbound_event_signature_skipped_dev event_type=%s", et)
        return True
    key = None
    for prefix, k in _KEY_BY_PREFIX.items():
        if et.startswith(prefix):
            key = k
            break
    if key is None:
        logger.warning("unknown_event_type dropped event_type=%s", et)
        return False
    try:
        verify_signed_event(payload, key, source=et)
        return True
    except EventSignatureError as exc:
        logger.warning(
            "notification_event_signature_rejected event_type=%s reason=%s",
            et,
            exc,
        )
        return False


def parse_sync_event(message_body: str) -> Optional[Dict[str, Any]]:
    """Parse SNS-wrapped SQS message to extract data_sync.completed event."""
    try:
        # SQS messages from SNS are wrapped in a Message field
        body = json.loads(message_body)

        # Check if this is an SNS message
        if "Message" in body:
            message = json.loads(body["Message"])
        else:
            message = body

        # Validate event type
        if message.get("event_type") != "data_sync.completed":
            logger.debug(f"Skipping non-sync event: {message.get('event_type')}")
            return None

        return message
    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Failed to parse sync event: {e}")
        return None


async def refresh_admin_cache(dataset_name: str, sync_data: Dict[str, Any]) -> bool:
    """Trigger admin dashboard cache refresh for updated dataset."""
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            # Call admin dashboard endpoint to refresh cache
            response = await client.post(
                f"{ADMIN_DASHBOARD_URL}/admin/cache/refresh",
                json={
                    "dataset": dataset_name,
                    "metadata": sync_data.get("metadata", {}),
                    "timestamp": datetime.utcnow().isoformat(),
                },
                timeout=10.0,
            )
            response.raise_for_status()
            logger.info(f"Cache refresh triggered for {dataset_name}")
            return True
    except Exception as e:
        logger.warning(f"Cache refresh failed for {dataset_name}: {e}")
        return False


async def handle_sync_event(event: Dict[str, Any]) -> bool:
    """Process data sync completion event and trigger notifications/cache refresh."""
    try:
        dataset = event.get("dataset", "")
        event_timestamp = event.get("timestamp", "")
        metadata = event.get("metadata", {})

        logger.info(f"Processing sync event for dataset: {dataset} at {event_timestamp}")

        # Route to appropriate handler based on dataset
        if dataset == "ncaa_schools":
            success = await handle_ncaa_sync(metadata)
        elif dataset == "transfer_portal":
            success = await handle_transfer_portal_sync(metadata)
        elif dataset == "high_school_nces":
            success = await handle_high_school_sync(metadata)
        else:
            logger.warning(f"Unknown dataset in sync event: {dataset}")
            return False

        # Always trigger cache refresh on successful processing
        if success:
            await refresh_admin_cache(dataset, event)

        return success
    except Exception as e:
        logger.error(f"Error processing sync event: {e}", exc_info=True)
        return False


async def handle_ncaa_sync(metadata: Dict[str, Any]) -> bool:
    """Handle NCAA schools sync completion."""
    try:
        schools_added = metadata.get("schools_added", 0)
        schools_updated = metadata.get("schools_updated", 0)
        schools_total = metadata.get("schools_total", 0)

        logger.info(
            f"NCAA sync completed: added={schools_added}, updated={schools_updated}, total={schools_total}"
        )

        # Queue notification about NCAA sync completion
        # This will be picked up by the notification delivery worker
        # For now, just log the event

        return True
    except Exception as e:
        logger.error(f"Error handling NCAA sync: {e}")
        return False


async def handle_transfer_portal_sync(metadata: Dict[str, Any]) -> bool:
    """Handle transfer portal sync completion."""
    try:
        athletes_added = metadata.get("athletes_added", 0)
        athletes_updated = metadata.get("athletes_updated", 0)
        athletes_total = metadata.get("athletes_total", 0)

        logger.info(
            f"Transfer portal sync completed: added={athletes_added}, updated={athletes_updated}, total={athletes_total}"
        )

        return True
    except Exception as e:
        logger.error(f"Error handling transfer portal sync: {e}")
        return False


async def handle_high_school_sync(metadata: Dict[str, Any]) -> bool:
    """Handle high school NCES data sync completion."""
    try:
        schools_added = metadata.get("schools_added", 0)
        schools_updated = metadata.get("schools_updated", 0)
        schools_total = metadata.get("schools_total", 0)

        logger.info(
            f"High school sync completed: added={schools_added}, updated={schools_updated}, total={schools_total}"
        )

        return True
    except Exception as e:
        logger.error(f"Error handling high school sync: {e}")
        return False


async def process_message(message: Dict[str, Any]) -> bool:
    """Process a single SQS message and delete if successful."""
    receipt_handle = message.get("ReceiptHandle")
    body = message.get("Body", "")

    try:
        # Parse the event
        event = parse_sync_event(body)
        if not event:
            # Invalid format but delete anyway to avoid reprocessing
            logger.debug(f"Deleting invalid message: {body[:100]}")
            sqs_client.delete_message(QueueUrl=SQS_QUEUE_URL, ReceiptHandle=receipt_handle)
            return True

        # OWASP A08: verify upstream HMAC signature BEFORE dispatching
        # any handler. Unverified events are dropped + deleted (no retry).
        if not _verify_inbound_event(event):
            sqs_client.delete_message(QueueUrl=SQS_QUEUE_URL, ReceiptHandle=receipt_handle)
            return True

        # Process the event
        success = await handle_sync_event(event)

        # Delete message from queue only on success
        if success:
            sqs_client.delete_message(QueueUrl=SQS_QUEUE_URL, ReceiptHandle=receipt_handle)
            logger.info(f"Processed and deleted message for {event.get('dataset')}")
        else:
            logger.warning("Message processing failed, keeping in queue for retry")

        return success
    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)
        return False


async def poll_queue() -> None:
    """Poll SQS queue for data sync events."""
    if not SQS_QUEUE_URL:
        logger.error("DATA_SYNC_EVENTS_QUEUE_URL not set, cannot start consumer")
        return

    logger.info(f"Starting data sync consumer, polling {SQS_QUEUE_URL}")

    while True:
        try:
            # Receive messages from queue
            response = sqs_client.receive_message(
                QueueUrl=SQS_QUEUE_URL,
                MaxNumberOfMessages=BATCH_SIZE,
                WaitTimeSeconds=MAX_WAIT_TIME,
                AttributeNames=["All"],
            )

            messages = response.get("Messages", [])
            if not messages:
                logger.debug("No messages in queue, waiting...")
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            logger.info(f"Received {len(messages)} messages from queue")

            # Process messages concurrently
            results = await asyncio.gather(
                *[process_message(msg) for msg in messages],
                return_exceptions=False,
            )

            success_count = sum(1 for r in results if r)
            logger.info(f"Processed {success_count}/{len(messages)} messages successfully")

        except ClientError as e:
            logger.error(f"SQS client error: {e}")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
        except Exception as e:
            logger.error(f"Unexpected error in poll loop: {e}", exc_info=True)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def start_consumer() -> None:
    """Start the data sync consumer as a background task."""
    try:
        await poll_queue()
    except Exception as e:
        logger.error(f"Consumer crashed: {e}", exc_info=True)
        # Restart after delay
        await asyncio.sleep(10)
        await start_consumer()


# For standalone execution
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    asyncio.run(start_consumer())
