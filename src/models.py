"""
Notification Service Models

Comprehensive data models for user notifications, alert management, and delivery tracking.
Features soft delete pattern and strategic database indexing for high-performance queries.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    VARCHAR,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class NotificationTemplate(Base):
    """
    Notification Templates
    Reusable templates for different notification types with variable substitution
    """

    __tablename__ = "notification_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    template_name = Column(String(100), nullable=False, unique=True)
    template_type = Column(String(50), nullable=False)  # email, sms, push, in_app
    subject = Column(String(255))  # For emails
    content = Column(Text, nullable=False)
    variables = Column(Text)  # JSON list of variable names {{name}}, {{email}}, etc.
    priority = Column(String(20), default="normal")  # low, normal, high, urgent
    retry_policy = Column(String(50))  # JSON config for retry behavior
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    is_deleted = Column(Integer, default=0)
    deleted_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_notification_templates_type_active", "template_type", "is_active"),
        Index("idx_notification_templates_name", "template_name"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "template_name": self.template_name,
            "template_type": self.template_type,
            "subject": self.subject,
            "content": self.content,
            "priority": self.priority,
            "is_active": bool(self.is_active),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class UserNotificationPreference(Base):
    """
    User Notification Preferences
    Per-user settings for notification channels and frequency
    """

    __tablename__ = "user_notification_preferences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, unique=True, index=True)
    email_enabled = Column(Integer, default=1)
    sms_enabled = Column(Integer, default=1)
    push_enabled = Column(Integer, default=1)
    in_app_enabled = Column(Integer, default=1)
    email_frequency = Column(String(50), default="immediate")  # immediate, daily, weekly, never
    sms_frequency = Column(String(50), default="immediate")
    push_frequency = Column(String(50), default="immediate")
    quiet_hours_start = Column(String(5))  # HH:MM format
    quiet_hours_end = Column(String(5))
    timezone = Column(String(50), default="UTC")
    notification_categories = Column(Text)  # JSON array of category preferences
    do_not_disturb_enabled = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    is_deleted = Column(Integer, default=0)
    deleted_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_user_notification_prefs_user_dnd", "user_id", "do_not_disturb_enabled"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "email_enabled": bool(self.email_enabled),
            "sms_enabled": bool(self.sms_enabled),
            "push_enabled": bool(self.push_enabled),
            "in_app_enabled": bool(self.in_app_enabled),
            "email_frequency": self.email_frequency,
            "sms_frequency": self.sms_frequency,
            "push_frequency": self.push_frequency,
            "timezone": self.timezone,
            "do_not_disturb_enabled": bool(self.do_not_disturb_enabled),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Notification(Base):
    """
    Notifications
    Individual notifications with multi-channel delivery tracking
    """

    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    template_id = Column(Integer, ForeignKey("notification_templates.id"), nullable=False)
    notification_type = Column(String(50), nullable=False)  # order_update, payment_alert, system_alert, etc.
    title = Column(String(255))
    message = Column(Text, nullable=False)
    data_payload = Column(Text)  # JSON additional data for rich notifications
    priority = Column(String(20), default="normal")
    is_read = Column(Integer, default=0)
    read_at = Column(DateTime)
    expires_at = Column(DateTime)
    source_system = Column(String(100), nullable=False)  # which service triggered this
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    is_deleted = Column(Integer, default=0)
    deleted_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_notifications_user_read", "user_id", "is_read"),
        Index("idx_notifications_user_created", "user_id", "created_at"),
        Index("idx_notifications_type_created", "notification_type", "created_at"),
        Index("idx_notifications_user_priority", "user_id", "priority"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "template_id": self.template_id,
            "notification_type": self.notification_type,
            "title": self.title,
            "message": self.message,
            "priority": self.priority,
            "is_read": bool(self.is_read),
            "read_at": self.read_at.isoformat() if self.read_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "source_system": self.source_system,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class NotificationChannel(Base):
    """
    Notification Channels
    User contact information for different notification types
    """

    __tablename__ = "notification_channels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    channel_type = Column(String(50), nullable=False)  # email, sms, push, in_app, webhook
    channel_value = Column(String(500), nullable=False)  # email address, phone number, device token, webhook URL
    is_verified = Column(Integer, default=0)
    is_primary = Column(Integer, default=0)
    verification_token = Column(String(500))
    verification_attempts = Column(Integer, default=0)
    verified_at = Column(DateTime)
    is_active = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    is_deleted = Column(Integer, default=0)
    deleted_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_notification_channels_user_type", "user_id", "channel_type"),
        Index("idx_notification_channels_verified", "is_verified", "is_active"),
        Index("idx_notification_channels_value", "channel_value"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "channel_type": self.channel_type,
            "is_verified": bool(self.is_verified),
            "is_primary": bool(self.is_primary),
            "is_active": bool(self.is_active),
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DeliveryLog(Base):
    """
    Delivery Logs
    Comprehensive logging of notification delivery attempts and results
    """

    __tablename__ = "delivery_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    notification_id = Column(Integer, ForeignKey("notifications.id"), nullable=False, index=True)
    channel_id = Column(Integer, ForeignKey("notification_channels.id"), nullable=False)
    delivery_channel = Column(String(50), nullable=False)  # email, sms, push, webhook
    delivery_status = Column(String(50), nullable=False)  # pending, sent, failed, bounced, complained, delivered
    status_code = Column(Integer)
    error_message = Column(Text)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    next_retry_at = Column(DateTime)
    delivered_at = Column(DateTime)
    external_message_id = Column(String(500))  # ID from email/SMS provider
    response_metadata = Column(Text)  # JSON metadata from provider
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    is_deleted = Column(Integer, default=0)
    deleted_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_delivery_logs_notification_status", "notification_id", "delivery_status"),
        Index("idx_delivery_logs_channel_status", "delivery_channel", "delivery_status"),
        Index("idx_delivery_logs_retry_time", "next_retry_at", "retry_count"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "notification_id": self.notification_id,
            "channel_id": self.channel_id,
            "delivery_channel": self.delivery_channel,
            "delivery_status": self.delivery_status,
            "status_code": self.status_code,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "delivered_at": self.delivered_at.isoformat() if self.delivered_at else None,
            "external_message_id": self.external_message_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class NotificationBatch(Base):
    """
    Notification Batches
    Group-level notifications for campaigns and bulk messaging
    """

    __tablename__ = "notification_batches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_name = Column(String(255), nullable=False)
    batch_type = Column(String(50), nullable=False)  # campaign, bulk, scheduled, triggered
    template_id = Column(Integer, ForeignKey("notification_templates.id"), nullable=False)
    target_user_count = Column(Integer)
    scheduled_send_time = Column(DateTime)
    batch_status = Column(String(50), nullable=False)  # draft, scheduled, sending, completed, failed, paused
    sent_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    bounce_count = Column(Integer, default=0)
    success_rate = Column(Float)  # percentage
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_by = Column(Integer)  # admin/system user ID
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    is_deleted = Column(Integer, default=0)
    deleted_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_notification_batches_status", "batch_status", "created_at"),
        Index("idx_notification_batches_scheduled", "scheduled_send_time"),
    )

    def to_dict(self):
        success_rate_value = float(self.success_rate) if self.success_rate is not None else 0.0  # type: ignore[reportArgumentType]
        return {
            "id": self.id,
            "batch_name": self.batch_name,
            "batch_type": self.batch_type,
            "template_id": self.template_id,
            "target_user_count": self.target_user_count,
            "batch_status": self.batch_status,
            "sent_count": self.sent_count,
            "failed_count": self.failed_count,
            "bounce_count": self.bounce_count,
            "success_rate": success_rate_value,
            "scheduled_send_time": self.scheduled_send_time.isoformat() if self.scheduled_send_time else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
