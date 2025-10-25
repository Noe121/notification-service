"""
Notification Service - Business Logic Layer

Comprehensive notification management including template handling, delivery tracking,
user preferences, and batch processing.
"""

from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
import json
import logging

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, desc, func

from models import (
    NotificationTemplate,
    UserNotificationPreference,
    Notification,
    NotificationChannel,
    DeliveryLog,
    NotificationBatch,
    Base,
)

logger = logging.getLogger(__name__)


class NotificationService:
    """Core notification service with template and delivery management"""

    @staticmethod
    def create_notification_template(
        db: Session,
        template_name: str,
        template_type: str,
        content: str,
        subject: Optional[str] = None,
        variables: Optional[List[str]] = None,
        priority: str = "normal",
        retry_policy: Optional[Dict[str, Any]] = None,
    ) -> NotificationTemplate:
        """
        Create a new notification template

        Args:
            db: Database session
            template_name: Unique template identifier
            template_type: Type of notification (email, sms, push, in_app)
            content: Template content with {{variable}} placeholders
            subject: Email subject line
            variables: List of variable names used in template
            priority: Notification priority level
            retry_policy: JSON retry configuration

        Returns:
            Created NotificationTemplate object
        """
        template = NotificationTemplate(
            template_name=template_name,
            template_type=template_type,
            subject=subject,
            content=content,
            variables=json.dumps(variables) if variables else None,
            priority=priority,
            retry_policy=json.dumps(retry_policy) if retry_policy else None,
            is_active=1,
        )
        db.add(template)
        db.commit()
        db.refresh(template)
        return template

    @staticmethod
    def get_template_by_name(db: Session, template_name: str) -> Optional[NotificationTemplate]:
        """Retrieve template by name"""
        return (
            db.query(NotificationTemplate)
            .filter(NotificationTemplate.template_name == template_name, NotificationTemplate.is_deleted == 0)
            .first()
        )

    @staticmethod
    def get_active_templates(
        db: Session, template_type: Optional[str] = None, limit: int = 100, offset: int = 0
    ) -> tuple[List[NotificationTemplate], int]:
        """
        Get active notification templates with optional filtering

        Returns:
            Tuple of (templates list, total count)
        """
        query = db.query(NotificationTemplate).filter(
            NotificationTemplate.is_deleted == 0, NotificationTemplate.is_active == 1
        )

        if template_type:
            query = query.filter(NotificationTemplate.template_type == template_type)

        total = query.count()
        templates = query.order_by(NotificationTemplate.created_at.desc()).offset(offset).limit(limit).all()

        return templates, total

    @staticmethod
    def send_notification(
        db: Session,
        user_id: int,
        template_id: int,
        notification_type: str,
        title: Optional[str] = None,
        message: Optional[str] = None,
        data_payload: Optional[Dict[str, Any]] = None,
        priority: str = "normal",
        source_system: str = "system",
        expires_at: Optional[datetime] = None,
    ) -> Notification:
        """
        Create and send a notification to a user

        Args:
            db: Database session
            user_id: Target user ID
            template_id: Notification template to use
            notification_type: Type of notification
            title: Notification title
            message: Notification message content
            data_payload: Additional data for rich notifications
            priority: Priority level
            source_system: System that triggered notification
            expires_at: When notification expires

        Returns:
            Created Notification object
        """
        notification = Notification(
            user_id=user_id,
            template_id=template_id,
            notification_type=notification_type,
            title=title,
            message=message,
            data_payload=json.dumps(data_payload) if data_payload else None,
            priority=priority,
            is_read=0,
            source_system=source_system,
            expires_at=expires_at,
        )
        db.add(notification)
        db.commit()
        db.refresh(notification)

        # Queue for delivery to active channels
        NotificationService._queue_delivery(db, notification)

        return notification

    @staticmethod
    def _queue_delivery(db: Session, notification: Notification) -> None:
        """Queue notification for delivery to active channels"""
        channels = (
            db.query(NotificationChannel)
            .filter(
                NotificationChannel.user_id == notification.user_id,
                NotificationChannel.is_active == 1,
                NotificationChannel.is_verified == 1,
                NotificationChannel.is_deleted == 0,
            )
            .all()
        )

        for channel in channels:
            delivery_log = DeliveryLog(
                notification_id=notification.id,
                channel_id=channel.id,
                delivery_channel=channel.channel_type,
                delivery_status="pending",
                retry_count=0,
                max_retries=3,
            )
            db.add(delivery_log)

        db.commit()

    @staticmethod
    def mark_as_read(db: Session, notification_id: int, user_id: int) -> Optional[Notification]:
        """Mark notification as read"""
        notification = (
            db.query(Notification)
            .filter(
                Notification.id == notification_id,
                Notification.user_id == user_id,
                Notification.is_deleted == 0,
            )
            .first()
        )

        if notification:
            notification.is_read = 1  # type: ignore[reportAttributeAccessIssue]
            notification.read_at = datetime.utcnow()  # type: ignore[reportAttributeAccessIssue]
            db.commit()
            db.refresh(notification)

        return notification

    @staticmethod
    def get_user_notifications(
        db: Session,
        user_id: int,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[List[Notification], int]:
        """
        Get notifications for a user with optional filtering

        Returns:
            Tuple of (notifications list, total count)
        """
        query = db.query(Notification).filter(
            Notification.user_id == user_id,
            Notification.is_deleted == 0,
        )

        if unread_only:
            query = query.filter(Notification.is_read == 0)

        total = query.count()
        notifications = query.order_by(Notification.created_at.desc()).offset(offset).limit(limit).all()

        return notifications, total

    @staticmethod
    def delete_notification(db: Session, notification_id: int, user_id: int) -> bool:
        """Soft delete a notification"""
        notification = (
            db.query(Notification)
            .filter(
                Notification.id == notification_id,
                Notification.user_id == user_id,
                Notification.is_deleted == 0,
            )
            .first()
        )

        if notification:
            notification.is_deleted = 1  # type: ignore[reportAttributeAccessIssue]
            notification.deleted_at = datetime.utcnow()  # type: ignore[reportAttributeAccessIssue]
            db.commit()
            return True

        return False


class UserPreferenceService:
    """Manage user notification preferences and opt-ins"""

    @staticmethod
    def get_or_create_preferences(db: Session, user_id: int) -> UserNotificationPreference:
        """Get existing preferences or create defaults"""
        preferences = (
            db.query(UserNotificationPreference)
            .filter(
                UserNotificationPreference.user_id == user_id,
                UserNotificationPreference.is_deleted == 0,
            )
            .first()
        )

        if not preferences:
            preferences = UserNotificationPreference(user_id=user_id)
            db.add(preferences)
            db.commit()
            db.refresh(preferences)

        return preferences

    @staticmethod
    def update_preferences(
        db: Session,
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
    ) -> UserNotificationPreference:
        """Update user notification preferences"""
        preferences = UserPreferenceService.get_or_create_preferences(db, user_id)

        if email_enabled is not None:
            preferences.email_enabled = 1 if email_enabled else 0  # type: ignore[reportAttributeAccessIssue]
        if sms_enabled is not None:
            preferences.sms_enabled = 1 if sms_enabled else 0  # type: ignore[reportAttributeAccessIssue]
        if push_enabled is not None:
            preferences.push_enabled = 1 if push_enabled else 0  # type: ignore[reportAttributeAccessIssue]
        if in_app_enabled is not None:
            preferences.in_app_enabled = 1 if in_app_enabled else 0  # type: ignore[reportAttributeAccessIssue]
        if email_frequency is not None:
            preferences.email_frequency = email_frequency  # type: ignore[reportAttributeAccessIssue]
        if timezone is not None:
            preferences.timezone = timezone  # type: ignore[reportAttributeAccessIssue]
        if do_not_disturb is not None:
            preferences.do_not_disturb_enabled = 1 if do_not_disturb else 0  # type: ignore[reportAttributeAccessIssue]
        if quiet_hours_start is not None:
            preferences.quiet_hours_start = quiet_hours_start  # type: ignore[reportAttributeAccessIssue]
        if quiet_hours_end is not None:
            preferences.quiet_hours_end = quiet_hours_end  # type: ignore[reportAttributeAccessIssue]

        db.commit()
        db.refresh(preferences)
        return preferences

    @staticmethod
    def is_notification_allowed(db: Session, user_id: int, channel_type: str) -> bool:
        """Check if user allows notifications for specific channel"""
        preferences = UserPreferenceService.get_or_create_preferences(db, user_id)

        if preferences.do_not_disturb_enabled:
            return False

        channel_enabled_map = {
            "email": preferences.email_enabled,
            "sms": preferences.sms_enabled,
            "push": preferences.push_enabled,
            "in_app": preferences.in_app_enabled,
        }

        return bool(channel_enabled_map.get(channel_type, False))


class NotificationChannelService:
    """Manage user notification channels (email, SMS, push tokens, etc.)"""

    @staticmethod
    def add_channel(
        db: Session,
        user_id: int,
        channel_type: str,
        channel_value: str,
        is_primary: bool = False,
    ) -> NotificationChannel:
        """
        Add a new notification channel for user

        Args:
            db: Database session
            user_id: User ID
            channel_type: Type of channel (email, sms, push, webhook)
            channel_value: Channel identifier (email, phone, token, URL)
            is_primary: Whether this is the primary channel

        Returns:
            Created NotificationChannel
        """
        # If this is primary, unset other primary channels
        if is_primary:
            db.query(NotificationChannel).filter(
                NotificationChannel.user_id == user_id,
                NotificationChannel.channel_type == channel_type,
                NotificationChannel.is_primary == 1,
            ).update({NotificationChannel.is_primary: 0})

        channel = NotificationChannel(
            user_id=user_id,
            channel_type=channel_type,
            channel_value=channel_value,
            is_primary=1 if is_primary else 0,
            is_active=1,
            is_verified=0,
        )
        db.add(channel)
        db.commit()
        db.refresh(channel)
        return channel

    @staticmethod
    def get_user_channels(
        db: Session,
        user_id: int,
        channel_type: Optional[str] = None,
        verified_only: bool = False,
    ) -> List[NotificationChannel]:
        """Get notification channels for user"""
        query = db.query(NotificationChannel).filter(
            NotificationChannel.user_id == user_id,
            NotificationChannel.is_deleted == 0,
        )

        if channel_type:
            query = query.filter(NotificationChannel.channel_type == channel_type)

        if verified_only:
            query = query.filter(NotificationChannel.is_verified == 1)

        return query.order_by(NotificationChannel.is_primary.desc()).all()

    @staticmethod
    def verify_channel(db: Session, channel_id: int, verification_token: str) -> bool:
        """Mark channel as verified"""
        channel = (
            db.query(NotificationChannel)
            .filter(NotificationChannel.id == channel_id, NotificationChannel.is_deleted == 0)
            .first()
        )

        if channel and channel.verification_token == verification_token:
            channel.is_verified = 1  # type: ignore[reportAttributeAccessIssue]
            channel.verified_at = datetime.utcnow()  # type: ignore[reportAttributeAccessIssue]
            db.commit()
            db.refresh(channel)
            return True

        return False

    @staticmethod
    def deactivate_channel(db: Session, channel_id: int) -> bool:
        """Deactivate a notification channel"""
        channel = (
            db.query(NotificationChannel)
            .filter(NotificationChannel.id == channel_id, NotificationChannel.is_deleted == 0)
            .first()
        )

        if channel:
            channel.is_active = 0  # type: ignore[reportAttributeAccessIssue]
            db.commit()
            return True

        return False


class DeliveryService:
    """Manage notification delivery tracking and retry logic"""

    @staticmethod
    def get_pending_deliveries(db: Session, limit: int = 100) -> List[DeliveryLog]:
        """Get pending deliveries ready to send"""
        now = datetime.utcnow()
        return (
            db.query(DeliveryLog)
            .filter(
                DeliveryLog.delivery_status == "pending",
                or_(DeliveryLog.next_retry_at.is_(None), DeliveryLog.next_retry_at <= now),
                DeliveryLog.is_deleted == 0,
            )
            .order_by(DeliveryLog.created_at)
            .limit(limit)
            .all()
        )

    @staticmethod
    def mark_delivered(
        db: Session,
        delivery_log_id: int,
        external_message_id: Optional[str] = None,
        response_metadata: Optional[Dict[str, Any]] = None,
    ) -> DeliveryLog:
        """Mark delivery as successful"""
        delivery = (
            db.query(DeliveryLog)
            .filter(DeliveryLog.id == delivery_log_id, DeliveryLog.is_deleted == 0)
            .first()
        )

        if delivery:
            delivery.delivery_status = "delivered"  # type: ignore[reportAttributeAccessIssue]
            delivery.delivered_at = datetime.utcnow()  # type: ignore[reportAttributeAccessIssue]
            if external_message_id:
                delivery.external_message_id = external_message_id  # type: ignore[reportAttributeAccessIssue]
            if response_metadata:
                delivery.response_metadata = json.dumps(response_metadata)  # type: ignore[reportAttributeAccessIssue]
            db.commit()
            db.refresh(delivery)

        return delivery

    @staticmethod
    def mark_failed(
        db: Session,
        delivery_log_id: int,
        error_message: str,
        status_code: Optional[int] = None,
        should_retry: bool = True,
    ) -> DeliveryLog:
        """Mark delivery as failed with retry logic"""
        delivery = (
            db.query(DeliveryLog)
            .filter(DeliveryLog.id == delivery_log_id, DeliveryLog.is_deleted == 0)
            .first()
        )

        if delivery:
            delivery.retry_count += 1  # type: ignore[reportAttributeAccessIssue]
            delivery.error_message = error_message  # type: ignore[reportAttributeAccessIssue]
            if status_code:
                delivery.status_code = status_code  # type: ignore[reportAttributeAccessIssue]

            if should_retry and delivery.retry_count < delivery.max_retries:  # type: ignore[reportAttributeAccessIssue]
                # Exponential backoff: 1 min, 5 min, 15 min
                backoff_minutes = [1, 5, 15][delivery.retry_count - 1]  # type: ignore[reportAttributeAccessIssue]
                delivery.next_retry_at = datetime.utcnow() + timedelta(minutes=backoff_minutes)  # type: ignore[reportAttributeAccessIssue]
                delivery.delivery_status = "pending"  # type: ignore[reportAttributeAccessIssue]
            else:
                delivery.delivery_status = "failed"  # type: ignore[reportAttributeAccessIssue]

            db.commit()
            db.refresh(delivery)

        return delivery

    @staticmethod
    def get_delivery_statistics(db: Session, notification_id: int) -> Dict[str, Any]:
        """Get delivery statistics for a notification"""
        logs = (
            db.query(DeliveryLog)
            .filter(DeliveryLog.notification_id == notification_id, DeliveryLog.is_deleted == 0)
            .all()
        )

        total = len(logs)
        delivered = len([l for l in logs if l.delivery_status == "delivered"])
        failed = len([l for l in logs if l.delivery_status == "failed"])
        pending = len([l for l in logs if l.delivery_status == "pending"])

        return {
            "total": total,
            "delivered": delivered,
            "failed": failed,
            "pending": pending,
            "success_rate": (delivered / total * 100) if total > 0 else 0,
        }


class NotificationBatchService:
    """Manage batch and campaign notifications"""

    @staticmethod
    def create_batch(
        db: Session,
        batch_name: str,
        batch_type: str,
        template_id: int,
        target_user_count: Optional[int] = None,
        scheduled_send_time: Optional[datetime] = None,
        created_by: Optional[int] = None,
    ) -> NotificationBatch:
        """Create a new notification batch"""
        batch = NotificationBatch(
            batch_name=batch_name,
            batch_type=batch_type,
            template_id=template_id,
            target_user_count=target_user_count,
            scheduled_send_time=scheduled_send_time,
            batch_status="draft",
            created_by=created_by,
        )
        db.add(batch)
        db.commit()
        db.refresh(batch)
        return batch

    @staticmethod
    def schedule_batch(db: Session, batch_id: int, scheduled_time: datetime) -> NotificationBatch:
        """Schedule a batch for sending"""
        batch = db.query(NotificationBatch).filter(NotificationBatch.id == batch_id).first()
        if batch:
            batch.scheduled_send_time = scheduled_time  # type: ignore[reportAttributeAccessIssue]
            batch.batch_status = "scheduled"  # type: ignore[reportAttributeAccessIssue]
            db.commit()
            db.refresh(batch)
        return batch

    @staticmethod
    def get_batch_statistics(db: Session, batch_id: int) -> Dict[str, Any]:
        """Get statistics for a batch"""
        batch = db.query(NotificationBatch).filter(NotificationBatch.id == batch_id).first()

        if not batch:
            return {}

        success_rate = batch.success_rate or 0
        return {
            "batch_id": batch.id,
            "batch_name": batch.batch_name,
            "batch_status": batch.batch_status,
            "target_count": batch.target_user_count,
            "sent_count": batch.sent_count,
            "failed_count": batch.failed_count,
            "bounce_count": batch.bounce_count,
            "success_rate": float(success_rate),  # type: ignore[reportArgumentType]
        }
