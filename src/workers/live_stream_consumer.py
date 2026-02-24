"""
SQS consumer for notification-service – live-stream lifecycle events.

Subscribes to the nilbx-live-stream-notifications queue (filtered from the
nilbx-live-stream-events SNS topic) and dispatches in-app / push / email
notifications for user-facing stream milestones.

Configured via LIVE_STREAM_NOTIFICATIONS_QUEUE_URL environment variable.
"""

import json
import logging
import os
from typing import Any, Callable, Dict, Optional

import boto3
from sqlalchemy.orm import Session

from ..notification_service import NotificationService

logger = logging.getLogger(__name__)

# Map live-stream event types to NILBx notification_type slugs.
# These slugs are looked up against notification templates in the DB.
_NOTIFICATION_TYPE_MAP: Dict[str, str] = {
    "live_stream.scheduled": "live_stream_scheduled",
    "live_stream.started": "live_stream_started",
    "live_stream.ended": "live_stream_ended",
    "live_stream.recording.ready": "live_stream_recording_ready",
    "live_stream.live_now": "live_stream_live_now",
}


class LiveStreamNotificationConsumer:
    """
    Consumes live-stream events from SQS and dispatches notifications via
    the notification-service's internal delivery pipeline.
    """

    def __init__(self, sqs_client, queue_url: str, db_session_factory: Callable[[], Session]):
        self.sqs_client = sqs_client
        self.queue_url = queue_url
        self.db_session_factory = db_session_factory

    @classmethod
    def from_env(cls, db_session_factory: Callable[[], Session]) -> Optional["LiveStreamNotificationConsumer"]:
        queue_url = os.getenv("LIVE_STREAM_NOTIFICATIONS_QUEUE_URL")
        if not queue_url:
            logger.warning("LIVE_STREAM_NOTIFICATIONS_QUEUE_URL not set – consumer disabled")
            return None
        region = os.getenv("AWS_REGION", "us-east-1")
        sqs_client = boto3.client("sqs", region_name=region)
        return cls(sqs_client=sqs_client, queue_url=queue_url, db_session_factory=db_session_factory)

    def poll_and_process(self, max_messages: int = 10, wait_time_seconds: int = 0) -> None:
        try:
            response = self.sqs_client.receive_message(
                QueueUrl=self.queue_url,
                MaxNumberOfMessages=max_messages,
                WaitTimeSeconds=wait_time_seconds,
                MessageAttributeNames=["All"],
            )
            for message in response.get("Messages", []):
                try:
                    self._process_message(message)
                    self.sqs_client.delete_message(
                        QueueUrl=self.queue_url,
                        ReceiptHandle=message["ReceiptHandle"],
                    )
                except Exception as exc:
                    logger.error("Error processing live-stream notification message: %s", exc, exc_info=True)
        except Exception as exc:
            logger.error("Error polling live-stream notifications queue: %s", exc, exc_info=True)

    def _process_message(self, message: Dict[str, Any]) -> None:
        body = json.loads(message["Body"])
        event_data = json.loads(body["Message"]) if "Message" in body else body
        event_type = event_data.get("event_type", "")

        notification_type = _NOTIFICATION_TYPE_MAP.get(event_type)
        if not notification_type:
            logger.debug("No notification mapping for event type: %s", event_type)
            return

        logger.info("Dispatching notification for live-stream event: %s", event_type)
        self._dispatch(event_type, notification_type, event_data)

    def _dispatch(self, event_type: str, notification_type: str, event: Dict[str, Any]) -> None:
        """
        Find the active template for notification_type and enqueue a delivery.
        For live_stream.live_now, fans are listed in rsvp_user_ids[] in the payload
        rather than derived from the actor. Fails gracefully if no template exists.
        """
        db = self.db_session_factory()
        try:
            stream_id = event.get("stream_id", "")
            payload_data = event.get("payload") or event  # SNS wraps payload in "payload" field

            # live_stream.live_now: notify each RSVP'd fan individually
            if event_type == "live_stream.live_now":
                rsvp_user_ids = payload_data.get("rsvp_user_ids") or event.get("rsvp_user_ids") or []
                if not rsvp_user_ids:
                    logger.debug("live_stream.live_now with empty rsvp_user_ids for stream %s", stream_id)
                    return
                templates, _ = NotificationService.get_active_templates(db=db, template_type="in_app")
                template = next(
                    (t for t in templates if notification_type in (t.template_name or "")), None
                )
                if not template:
                    logger.info("No template for %s – skipping", notification_type)
                    return
                template_id = int(getattr(template, "id"))
                fan_payload = {
                    "stream_id": stream_id,
                    "stream_title": payload_data.get("stream_title") or event.get("stream_title", ""),
                    "hls_url": payload_data.get("hls_url") or event.get("hls_url", ""),
                    "influencer_name": payload_data.get("influencer_name") or event.get("influencer_name", ""),
                    "event_type": event_type,
                }
                dispatched = 0
                for fan_id in rsvp_user_ids:
                    try:
                        NotificationService.send_notification(
                            db=db,
                            user_id=int(fan_id),
                            template_id=template_id,
                            notification_type=notification_type,
                            data_payload=fan_payload,
                        )
                        dispatched += 1
                    except Exception as fan_exc:
                        logger.warning("Failed to notify fan %s for stream %s: %s", fan_id, stream_id, fan_exc)
                logger.info("live_stream.live_now: notified %d/%d fans for stream %s", dispatched, len(rsvp_user_ids), stream_id)
                return

            # Default path: single notification to stream owner
            actor = event.get("actor") or {}
            owner_user_id = actor.get("user_id")
            if not owner_user_id:
                logger.debug("No user_id in actor for event %s – skipping notification", event_type)
                return

            templates, _ = NotificationService.get_active_templates(
                db=db,
                template_type="in_app",
            )
            template = next(
                (t for t in templates if notification_type in (t.template_name or "")),
                None,
            )
            if not template:
                logger.info(
                    "No active template found for notification_type=%s – skipping",
                    notification_type,
                )
                return
            template_id = int(getattr(template, "id"))

            NotificationService.send_notification(
                db=db,
                user_id=int(owner_user_id),
                template_id=template_id,
                notification_type=notification_type,
                data_payload={
                    "stream_id": stream_id,
                    "event_type": event_type,
                    "occurred_at": event.get("occurred_at"),
                    "hls_url": event.get("hls_url"),
                    "replay_url": event.get("replay_url"),
                },
            )
            logger.info(
                "Notification dispatched for user_id=%s event=%s stream=%s",
                owner_user_id,
                event_type,
                stream_id,
            )
        except Exception as exc:
            logger.error("Failed to dispatch notification for %s: %s", event_type, exc, exc_info=True)
            raise
        finally:
            db.close()

    def run_forever(self) -> None:
        logger.info("Starting live-stream notification consumer, polling %s", self.queue_url)
        while True:
            try:
                self.poll_and_process()
            except KeyboardInterrupt:
                logger.info("Live-stream notification consumer shutting down")
                break
            except Exception as exc:
                logger.error("Unexpected error in live-stream notification consumer: %s", exc, exc_info=True)
