"""
Notification Service - Business Logic Layer

Comprehensive notification management including template handling, delivery tracking,
user preferences, and batch processing. Updated to match V001 deployed schema.
"""

from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
import json
import logging

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, desc, func

from .models import (
    NotificationType,
    NotificationTemplate,
    NotificationPreference,
    Notification,
    NotificationDelivery,
    NotificationBatch,
    VerifiedEmail,
    VerifiedPhone,
    Base,
)

logger = logging.getLogger(__name__)


class NotificationService:
    """Core notification service with template and delivery management"""

    @staticmethod
    def get_or_create_notification_type(
        db: Session,
        type_code: str,
        name: str,
        category: str = 'system',
        description: Optional[str] = None,
        default_channels: Optional[List[str]] = None,
    ) -> NotificationType:
        """Get existing notification type or create new one"""
        notification_type = (
            db.query(NotificationType)
            .filter(NotificationType.type_code == type_code)
            .first()
        )

        if not notification_type:
            notification_type = NotificationType(
                type_code=type_code,
                name=name,
                description=description,
                category=category,
                default_channels=default_channels or ["email", "in_app"],
                is_active=True,
            )
            db.add(notification_type)
            db.commit()
            db.refresh(notification_type)

        return notification_type

    @staticmethod
    def create_notification_template(
        db: Session,
        template_name: str,
        template_type: str,  # This maps to channel in new schema
        content: str,  # This maps to body in new schema
        subject: Optional[str] = None,
        variables: Optional[List[str]] = None,
        priority: str = "normal",
        retry_policy: Optional[Dict[str, Any]] = None,
        notification_type_code: str = "system_alert",
    ) -> NotificationTemplate:
        """
        Create a new notification template

        Args:
            db: Database session
            template_name: Unique template identifier (maps to name)
            template_type: Type of notification (email, sms, push, in_app) - maps to channel
            content: Template content with {{variable}} placeholders - maps to body
            subject: Email subject line
            variables: List of variable names used in template
            priority: Notification priority level (not stored in template)
            retry_policy: JSON retry configuration (not stored in template)
            notification_type_code: Code for the notification type

        Returns:
            Created NotificationTemplate object
        """
        # Get or create the notification type
        notification_type = NotificationService.get_or_create_notification_type(
            db=db,
            type_code=notification_type_code,
            name=notification_type_code.replace('_', ' ').title(),
            category='system',
        )

        template = NotificationTemplate(
            notification_type_id=notification_type.id,
            channel=template_type,
            name=template_name,
            subject=subject,
            body=content,
            variables=variables,
            is_active=True,
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
            .filter(NotificationTemplate.name == template_name, NotificationTemplate.is_active == True)
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
        query = db.query(NotificationTemplate).filter(NotificationTemplate.is_active == True)

        if template_type:
            query = query.filter(NotificationTemplate.channel == template_type)

        total = query.count()
        templates = query.order_by(NotificationTemplate.created_at.desc()).offset(offset).limit(limit).all()

        return templates, total

    @staticmethod
    def send_notification(
        db: Session,
        user_id: int,
        template_id: int,
        notification_type: str,  # This is now the type code
        title: Optional[str] = None,
        message: Optional[str] = None,  # Maps to body
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
            notification_type: Type code for notification
            title: Notification title
            message: Notification message content (maps to body)
            data_payload: Additional data for rich notifications
            priority: Priority level
            source_system: System that triggered notification
            expires_at: When notification expires

        Returns:
            Created Notification object
        """
        # Get or create the notification type
        notification_type_obj = NotificationService.get_or_create_notification_type(
            db=db,
            type_code=notification_type,
            name=notification_type.replace('_', ' ').title(),
        )

        notification = Notification(
            user_id=user_id,
            notification_type_id=notification_type_obj.id,
            template_id=template_id,
            title=title or "Notification",
            body=message or "",
            data_payload=data_payload,
            priority=priority,
            is_read=False,
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
        """Queue notification for delivery to verified channels"""
        # Get verified email addresses
        emails = (
            db.query(VerifiedEmail)
            .filter(
                VerifiedEmail.user_id == notification.user_id,
                VerifiedEmail.verified_at.isnot(None),
            )
            .all()
        )

        for email in emails:
            delivery = NotificationDelivery(
                notification_id=notification.id,
                channel='email',
                status='pending',
                recipient_address=email.email,
                attempt_count=0,
            )
            db.add(delivery)

        # Get verified phone numbers for SMS
        phones = (
            db.query(VerifiedPhone)
            .filter(
                VerifiedPhone.user_id == notification.user_id,
                VerifiedPhone.verified_at.isnot(None),
            )
            .all()
        )

        for phone in phones:
            delivery = NotificationDelivery(
                notification_id=notification.id,
                channel='sms',
                status='pending',
                recipient_address=f"{phone.country_code}{phone.phone}",
                attempt_count=0,
            )
            db.add(delivery)

        # Always add in-app delivery
        in_app_delivery = NotificationDelivery(
            notification_id=notification.id,
            channel='in_app',
            status='delivered',  # In-app is immediate
            delivered_at=datetime.utcnow(),
            attempt_count=1,
        )
        db.add(in_app_delivery)

        db.commit()

    @staticmethod
    def mark_as_read(db: Session, notification_id: int, user_id: int) -> Optional[Notification]:
        """Mark notification as read"""
        notification = (
            db.query(Notification)
            .filter(
                Notification.id == notification_id,
                Notification.user_id == user_id,
            )
            .first()
        )

        if notification:
            notification.is_read = True
            notification.read_at = datetime.utcnow()
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
        query = db.query(Notification).filter(Notification.user_id == user_id)

        if unread_only:
            query = query.filter(Notification.is_read == False)

        total = query.count()
        notifications = query.order_by(Notification.created_at.desc()).offset(offset).limit(limit).all()

        return notifications, total

    @staticmethod
    def get_notification_by_id(db: Session, notification_id: int) -> Optional[Notification]:
        """Retrieve a single notification by ID"""
        return db.query(Notification).filter(Notification.id == notification_id).first()

    @staticmethod
    def delete_notification(db: Session, notification_id: int, user_id: int) -> bool:
        """Dismiss a notification (soft delete via is_dismissed)"""
        notification = (
            db.query(Notification)
            .filter(
                Notification.id == notification_id,
                Notification.user_id == user_id,
            )
            .first()
        )

        if notification:
            notification.is_dismissed = True
            notification.dismissed_at = datetime.utcnow()
            db.commit()
            return True

        return False


class UserPreferenceService:
    """Manage user notification preferences and opt-ins"""

    @staticmethod
    def get_or_create_preferences(db: Session, user_id: int, notification_type_id: int = 1) -> NotificationPreference:
        """Get existing preferences or create defaults"""
        preferences = (
            db.query(NotificationPreference)
            .filter(
                NotificationPreference.user_id == user_id,
                NotificationPreference.notification_type_id == notification_type_id,
            )
            .first()
        )

        if not preferences:
            preferences = NotificationPreference(
                user_id=user_id,
                notification_type_id=notification_type_id,
                email_enabled=True,
                sms_enabled=False,
                push_enabled=True,
                in_app_enabled=True,
            )
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
        notification_type_id: int = 1,
    ) -> NotificationPreference:
        """Update user notification preferences"""
        preferences = UserPreferenceService.get_or_create_preferences(db, user_id, notification_type_id)

        if email_enabled is not None:
            preferences.email_enabled = email_enabled
        if sms_enabled is not None:
            preferences.sms_enabled = sms_enabled
        if push_enabled is not None:
            preferences.push_enabled = push_enabled
        if in_app_enabled is not None:
            preferences.in_app_enabled = in_app_enabled
        if quiet_hours_start is not None:
            from datetime import time as dt_time
            try:
                h, m = map(int, quiet_hours_start.split(':'))
                preferences.quiet_hours_start = dt_time(h, m)
            except (ValueError, AttributeError):
                pass
        if quiet_hours_end is not None:
            from datetime import time as dt_time
            try:
                h, m = map(int, quiet_hours_end.split(':'))
                preferences.quiet_hours_end = dt_time(h, m)
            except (ValueError, AttributeError):
                pass

        db.commit()
        db.refresh(preferences)
        return preferences

    @staticmethod
    def is_notification_allowed(db: Session, user_id: int, channel_type: str, notification_type_id: int = 1) -> bool:
        """Check if user allows notifications for specific channel"""
        preferences = UserPreferenceService.get_or_create_preferences(db, user_id, notification_type_id)

        channel_enabled_map = {
            "email": preferences.email_enabled,
            "sms": preferences.sms_enabled,
            "push": preferences.push_enabled,
            "in_app": preferences.in_app_enabled,
        }

        return bool(channel_enabled_map.get(channel_type, False))


class NotificationChannelService:
    """Manage user notification channels (verified emails, phones, etc.)"""

    @staticmethod
    def add_channel(
        db: Session,
        user_id: int,
        channel_type: str,
        channel_value: str,
        is_primary: bool = False,
    ) -> VerifiedEmail | VerifiedPhone:
        """
        Add a new notification channel for user

        Args:
            db: Database session
            user_id: User ID
            channel_type: Type of channel (email, sms)
            channel_value: Channel identifier (email, phone)
            is_primary: Whether this is the primary channel

        Returns:
            Created VerifiedEmail or VerifiedPhone
        """
        if channel_type == 'email':
            # If this is primary, unset other primary emails
            if is_primary:
                db.query(VerifiedEmail).filter(
                    VerifiedEmail.user_id == user_id,
                    VerifiedEmail.is_primary == True,
                ).update({VerifiedEmail.is_primary: False})

            channel = VerifiedEmail(
                user_id=user_id,
                email=channel_value,
                is_primary=is_primary,
            )
        else:  # sms/phone
            # If this is primary, unset other primary phones
            if is_primary:
                db.query(VerifiedPhone).filter(
                    VerifiedPhone.user_id == user_id,
                    VerifiedPhone.is_primary == True,
                ).update({VerifiedPhone.is_primary: False})

            channel = VerifiedPhone(
                user_id=user_id,
                phone=channel_value,
                is_primary=is_primary,
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
    ) -> List[VerifiedEmail | VerifiedPhone]:
        """Get notification channels for user"""
        channels = []

        if channel_type in (None, 'email'):
            query = db.query(VerifiedEmail).filter(VerifiedEmail.user_id == user_id)
            if verified_only:
                query = query.filter(VerifiedEmail.verified_at.isnot(None))
            channels.extend(query.order_by(VerifiedEmail.is_primary.desc()).all())

        if channel_type in (None, 'sms', 'phone'):
            query = db.query(VerifiedPhone).filter(VerifiedPhone.user_id == user_id)
            if verified_only:
                query = query.filter(VerifiedPhone.verified_at.isnot(None))
            channels.extend(query.order_by(VerifiedPhone.is_primary.desc()).all())

        return channels

    @staticmethod
    def verify_channel(db: Session, channel_id: int, verification_token: str) -> bool:
        """Mark channel as verified (simplified - actual impl would check token)"""
        # Try email first
        email = db.query(VerifiedEmail).filter(VerifiedEmail.id == channel_id).first()
        if email:
            email.verified_at = datetime.utcnow()
            db.commit()
            return True

        # Try phone
        phone = db.query(VerifiedPhone).filter(VerifiedPhone.id == channel_id).first()
        if phone:
            phone.verified_at = datetime.utcnow()
            db.commit()
            return True

        return False

    @staticmethod
    def deactivate_channel(db: Session, channel_id: int) -> bool:
        """Remove a notification channel (delete from DB)"""
        # Try email first
        email = db.query(VerifiedEmail).filter(VerifiedEmail.id == channel_id).first()
        if email:
            db.delete(email)
            db.commit()
            return True

        # Try phone
        phone = db.query(VerifiedPhone).filter(VerifiedPhone.id == channel_id).first()
        if phone:
            db.delete(phone)
            db.commit()
            return True

        return False

    @staticmethod
    def get_channel_by_id(db: Session, channel_id: int) -> Optional[VerifiedEmail | VerifiedPhone]:
        """Retrieve a notification channel by ID"""
        email = db.query(VerifiedEmail).filter(VerifiedEmail.id == channel_id).first()
        if email:
            return email
        return db.query(VerifiedPhone).filter(VerifiedPhone.id == channel_id).first()


class DeliveryService:
    """Manage notification delivery tracking and retry logic"""

    @staticmethod
    def get_pending_deliveries(db: Session, limit: int = 100) -> List[NotificationDelivery]:
        """Get pending deliveries ready to send"""
        now = datetime.utcnow()
        return (
            db.query(NotificationDelivery)
            .filter(
                NotificationDelivery.status == "pending",
                or_(NotificationDelivery.next_retry_at.is_(None), NotificationDelivery.next_retry_at <= now),
            )
            .order_by(NotificationDelivery.created_at)
            .limit(limit)
            .all()
        )

    @staticmethod
    def mark_delivered(
        db: Session,
        delivery_log_id: int,
        external_message_id: Optional[str] = None,
        response_metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[NotificationDelivery]:
        """Mark delivery as successful"""
        delivery = db.query(NotificationDelivery).filter(NotificationDelivery.id == delivery_log_id).first()
        if delivery is not None:
            delivery.status = "delivered"
            delivery.delivered_at = datetime.utcnow()
            if external_message_id:
                delivery.provider_message_id = external_message_id
            db.commit()
            db.refresh(delivery)
            return delivery
        return None

    @staticmethod
    def mark_failed(
        db: Session,
        delivery_log_id: int,
        error_message: str,
        status_code: Optional[int] = None,
        should_retry: bool = True,
    ) -> Optional[NotificationDelivery]:
        """Mark delivery as failed with retry logic"""
        delivery = db.query(NotificationDelivery).filter(NotificationDelivery.id == delivery_log_id).first()
        if delivery is not None:
            delivery.attempt_count += 1
            delivery.error_message = error_message
            delivery.last_attempt_at = datetime.utcnow()
            if status_code is not None:
                delivery.error_code = str(status_code)

            max_retries = 3
            if should_retry and delivery.attempt_count < max_retries:
                # Exponential backoff: 1 min, 5 min, 15 min
                backoff_minutes = [1, 5, 15][delivery.attempt_count - 1]
                delivery.next_retry_at = datetime.utcnow() + timedelta(minutes=backoff_minutes)
                delivery.status = "pending"
            else:
                delivery.status = "failed"

            db.commit()
            db.refresh(delivery)
            return delivery
        return None

    @staticmethod
    def get_delivery_statistics(db: Session, notification_id: int) -> Dict[str, Any]:
        """Get delivery statistics for a notification"""
        logs = (
            db.query(NotificationDelivery)
            .filter(NotificationDelivery.notification_id == notification_id)
            .all()
        )
        total = len(logs)
        delivered = len([l for l in logs if l.status == "delivered"])
        failed = len([l for l in logs if l.status == "failed"])
        pending = len([l for l in logs if l.status == "pending"])
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
        # Get notification type from template
        template = db.query(NotificationTemplate).filter(NotificationTemplate.id == template_id).first()
        notification_type_id = template.notification_type_id if template else 1

        batch = NotificationBatch(
            batch_name=batch_name,
            notification_type_id=notification_type_id,
            template_id=template_id,
            total_recipients=target_user_count or 0,
            status="pending",
            created_by=created_by,
        )
        db.add(batch)
        db.commit()
        db.refresh(batch)
        return batch

    @staticmethod
    def schedule_batch(db: Session, batch_id: int, scheduled_time: datetime) -> NotificationBatch:
        """Schedule a batch for sending (batch doesn't have scheduled_send_time in V001 schema)"""
        batch = db.query(NotificationBatch).filter(NotificationBatch.id == batch_id).first()
        if batch:
            batch.status = "pending"
            # Note: V001 schema doesn't have scheduled_send_time, would need migration
            db.commit()
            db.refresh(batch)
        return batch

    @staticmethod
    def get_batch_statistics(db: Session, batch_id: int) -> Dict[str, Any]:
        """Get statistics for a batch"""
        batch = db.query(NotificationBatch).filter(NotificationBatch.id == batch_id).first()

        if not batch:
            return {}

        total = batch.total_recipients or 0
        success_rate = (batch.sent_count / total * 100) if total > 0 else 0

        return {
            "batch_id": batch.id,
            "batch_name": batch.batch_name,
            "batch_status": batch.status,
            "target_count": total,
            "sent_count": batch.sent_count,
            "failed_count": batch.failed_count,
            "success_rate": success_rate,
        }


# Legacy aliases for backwards compatibility (deprecated - use new names)
UserNotificationPreference = NotificationPreference
DeliveryLog = NotificationDelivery
NotificationChannel = VerifiedEmail
