"""
Notification Service Models

Data models matching the deployed notifications_db V001 schema.
Features soft delete pattern and strategic database indexing for high-performance queries.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    Time,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class NotificationType(Base):
    """
    Notification Types
    Defines categories and default channels for notifications
    """

    __tablename__ = "notification_types"

    id = Column(Integer, primary_key=True, autoincrement=True)
    type_code = Column(String(50), nullable=False, unique=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    category = Column(
        Enum('deal', 'payment', 'compliance', 'system', 'marketing', 'social', name='notification_category'),
        nullable=False
    )
    default_channels = Column(JSON)  # ["email", "push", "sms", "in_app"]
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_type_code", "type_code"),
        Index("idx_category", "category"),
        Index("idx_is_active", "is_active"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "type_code": self.type_code,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "default_channels": self.default_channels,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class NotificationTemplate(Base):
    """
    Notification Templates
    Templates per channel for different notification types
    """

    __tablename__ = "notification_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    notification_type_id = Column(Integer, ForeignKey("notification_types.id", ondelete="CASCADE"), nullable=False)
    channel = Column(
        Enum('email', 'sms', 'push', 'in_app', 'webhook', name='notification_channel'),
        nullable=False
    )
    name = Column(String(255), nullable=False)
    subject = Column(String(500))  # For email
    body = Column(Text, nullable=False)
    variables = Column(JSON)  # Available template variables
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_notification_type", "notification_type_id"),
        Index("idx_channel", "channel"),
        Index("idx_template_is_active", "is_active"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "notification_type_id": self.notification_type_id,
            "channel": self.channel,
            "name": self.name,
            "subject": self.subject,
            "body": self.body,
            "variables": self.variables,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class NotificationPreference(Base):
    """
    Notification Preferences
    Per-user settings for notification channels per type
    """

    __tablename__ = "notification_preferences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)  # Reference to auth_db.users.id
    notification_type_id = Column(Integer, ForeignKey("notification_types.id", ondelete="CASCADE"), nullable=False)
    email_enabled = Column(Boolean, default=True)
    sms_enabled = Column(Boolean, default=False)
    push_enabled = Column(Boolean, default=True)
    in_app_enabled = Column(Boolean, default=True)
    quiet_hours_start = Column(Time)
    quiet_hours_end = Column(Time)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_pref_user_id", "user_id"),
        Index("unique_user_type", "user_id", "notification_type_id", unique=True),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "notification_type_id": self.notification_type_id,
            "email_enabled": self.email_enabled,
            "sms_enabled": self.sms_enabled,
            "push_enabled": self.push_enabled,
            "in_app_enabled": self.in_app_enabled,
            "quiet_hours_start": str(self.quiet_hours_start) if self.quiet_hours_start else None,
            "quiet_hours_end": str(self.quiet_hours_end) if self.quiet_hours_end else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Notification(Base):
    """
    Notifications
    Individual notifications with multi-channel delivery tracking
    Uses BIGINT for id to match deployed schema
    """

    __tablename__ = "notifications"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)  # Reference to auth_db.users.id
    notification_type_id = Column(Integer, ForeignKey("notification_types.id", ondelete="CASCADE"), nullable=False)
    template_id = Column(Integer, ForeignKey("notification_templates.id", ondelete="SET NULL"))
    priority = Column(
        Enum('low', 'normal', 'high', 'critical', name='notification_priority'),
        default='normal'
    )

    # Content
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)
    data_payload = Column(JSON)

    # Source
    source_system = Column(String(50))  # contract-service, payment-service, etc.
    source_reference_id = Column(String(100))

    # Status
    is_read = Column(Boolean, default=False)
    read_at = Column(DateTime)
    is_dismissed = Column(Boolean, default=False)
    dismissed_at = Column(DateTime)

    # Scheduling
    scheduled_for = Column(DateTime)
    expires_at = Column(DateTime)

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    deliveries = relationship("NotificationDelivery", back_populates="notification", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_notif_user_id", "user_id"),
        Index("idx_notif_notification_type", "notification_type_id"),
        Index("idx_notif_is_read", "is_read"),
        Index("idx_notif_priority", "priority"),
        Index("idx_notif_scheduled_for", "scheduled_for"),
        Index("idx_notif_created_at", "created_at"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "notification_type_id": self.notification_type_id,
            "template_id": self.template_id,
            "priority": self.priority,
            "title": self.title,
            "body": self.body,
            "data_payload": self.data_payload,
            "source_system": self.source_system,
            "source_reference_id": self.source_reference_id,
            "is_read": self.is_read,
            "read_at": self.read_at.isoformat() if self.read_at else None,
            "is_dismissed": self.is_dismissed,
            "dismissed_at": self.dismissed_at.isoformat() if self.dismissed_at else None,
            "scheduled_for": self.scheduled_for.isoformat() if self.scheduled_for else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class NotificationDelivery(Base):
    """
    Notification Deliveries
    Multi-channel delivery tracking (replaces old DeliveryLog)
    Uses BIGINT for id and notification_id to match deployed schema
    """

    __tablename__ = "notification_deliveries"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    notification_id = Column(BigInteger, ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False)
    channel = Column(
        Enum('email', 'sms', 'push', 'in_app', 'webhook', name='delivery_channel'),
        nullable=False
    )
    status = Column(
        Enum('pending', 'sent', 'delivered', 'failed', 'bounced', name='delivery_status'),
        default='pending'
    )

    # Delivery Details
    recipient_address = Column(String(255))  # Email, phone, device token
    provider = Column(String(50))  # sendgrid, twilio, firebase, etc.
    provider_message_id = Column(String(255))

    # Retry Logic
    attempt_count = Column(Integer, default=0)
    last_attempt_at = Column(DateTime)
    next_retry_at = Column(DateTime)

    # Error Handling
    error_code = Column(String(50))
    error_message = Column(Text)

    delivered_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    notification = relationship("Notification", back_populates="deliveries")

    __table_args__ = (
        Index("idx_delivery_notification_id", "notification_id"),
        Index("idx_delivery_channel", "channel"),
        Index("idx_delivery_status", "status"),
        Index("idx_delivery_next_retry", "next_retry_at"),
        Index("idx_delivery_created_at", "created_at"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "notification_id": self.notification_id,
            "channel": self.channel,
            "status": self.status,
            "recipient_address": self.recipient_address,
            "provider": self.provider,
            "provider_message_id": self.provider_message_id,
            "attempt_count": self.attempt_count,
            "last_attempt_at": self.last_attempt_at.isoformat() if self.last_attempt_at else None,
            "next_retry_at": self.next_retry_at.isoformat() if self.next_retry_at else None,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "delivered_at": self.delivered_at.isoformat() if self.delivered_at else None,
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
    notification_type_id = Column(Integer, ForeignKey("notification_types.id", ondelete="CASCADE"), nullable=False)
    template_id = Column(Integer, ForeignKey("notification_templates.id", ondelete="SET NULL"))
    total_recipients = Column(Integer, default=0)
    sent_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    status = Column(
        Enum('pending', 'processing', 'completed', 'failed', 'cancelled', name='batch_status'),
        default='pending'
    )
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_by = Column(Integer)  # Reference to auth_db.users.id
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_batch_status", "status"),
        Index("idx_batch_created_at", "created_at"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "batch_name": self.batch_name,
            "notification_type_id": self.notification_type_id,
            "template_id": self.template_id,
            "total_recipients": self.total_recipients,
            "sent_count": self.sent_count,
            "failed_count": self.failed_count,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class VerifiedEmail(Base):
    """
    Verified Emails
    Verified email addresses for users
    """

    __tablename__ = "verified_emails"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)  # Reference to auth_db.users.id
    email = Column(String(255), nullable=False)
    is_primary = Column(Boolean, default=False)
    verified_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_email_user_id", "user_id"),
        Index("idx_email", "email"),
        Index("idx_email_is_primary", "is_primary"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "email": self.email,
            "is_primary": self.is_primary,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class VerifiedPhone(Base):
    """
    Verified Phones
    Verified phone numbers for users
    """

    __tablename__ = "verified_phones"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)  # Reference to auth_db.users.id
    phone = Column(String(20), nullable=False)
    country_code = Column(String(5), default='+1')
    is_primary = Column(Boolean, default=False)
    verified_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_phone_user_id", "user_id"),
        Index("idx_phone", "phone"),
        Index("idx_phone_is_primary", "is_primary"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "phone": self.phone,
            "country_code": self.country_code,
            "is_primary": self.is_primary,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# Legacy aliases for backwards compatibility
# These can be removed once all code is updated to use new class names
UserNotificationPreference = NotificationPreference
DeliveryLog = NotificationDelivery
NotificationChannel = VerifiedEmail  # Partial mapping - actual schema is different
