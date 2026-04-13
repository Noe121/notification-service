from __future__ import annotations

"""
Notification Service Models

Data models matching the deployed notifications_db V001 schema.
Features soft delete pattern and strategic database indexing for high-performance queries.
"""

from datetime import datetime, time
from decimal import Decimal
from typing import Any, Optional

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
from sqlalchemy.orm import Mapped, declarative_base, mapped_column, relationship

Base = declarative_base()


class NotificationType(Base):
    """
    Notification Types
    Defines categories and default channels for notifications
    """

    __tablename__ = "notification_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type_code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[str] = mapped_column(
        Enum('deal', 'payment', 'compliance', 'system', 'marketing', 'social', name='notification_category'),
        nullable=False
    )
    default_channels: Mapped[Optional[Any]] = mapped_column(JSON)  # ["email", "push", "sms", "in_app"]
    is_active: Mapped[Optional[bool]] = mapped_column(Boolean, default=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)

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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    notification_type_id: Mapped[int] = mapped_column(Integer, ForeignKey("notification_types.id", ondelete="CASCADE"), nullable=False)
    channel: Mapped[str] = mapped_column(
        Enum('email', 'sms', 'push', 'in_app', 'webhook', name='notification_channel'),
        nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[Optional[str]] = mapped_column(String(500))  # For email
    body: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[Optional[Any]] = mapped_column(JSON)  # Available template variables
    is_active: Mapped[Optional[bool]] = mapped_column(Boolean, default=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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

    # Legacy attribute aliases expected by older tests/callers.
    @property
    def template_name(self):
        return self.name

    @template_name.setter
    def template_name(self, value):
        self.name = value

    @property
    def template_type(self):
        return self.channel

    @template_type.setter
    def template_type(self, value):
        self.channel = value

    @property
    def content(self):
        return self.body

    @content.setter
    def content(self, value):
        self.body = value

    @property
    def is_deleted(self):
        return False


class NotificationPreference(Base):
    """
    Notification Preferences
    Per-user settings for notification channels per type
    """

    __tablename__ = "notification_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)  # Reference to auth_db.users.id
    notification_type_id: Mapped[int] = mapped_column(Integer, ForeignKey("notification_types.id", ondelete="CASCADE"), nullable=False)
    email_enabled: Mapped[Optional[bool]] = mapped_column(Boolean, default=True)
    sms_enabled: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    push_enabled: Mapped[Optional[bool]] = mapped_column(Boolean, default=True)
    in_app_enabled: Mapped[Optional[bool]] = mapped_column(Boolean, default=True)
    quiet_hours_start: Mapped[Optional[time]] = mapped_column(Time)
    quiet_hours_end: Mapped[Optional[time]] = mapped_column(Time)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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

    @property
    def do_not_disturb_enabled(self):
        return bool(getattr(self, "_do_not_disturb_enabled", False))

    @do_not_disturb_enabled.setter
    def do_not_disturb_enabled(self, value):
        self._do_not_disturb_enabled = bool(value)

    @property
    def timezone(self):
        return getattr(self, "_timezone", None)

    @timezone.setter
    def timezone(self, value):
        self._timezone = value


class Notification(Base):
    """
    Notifications
    Individual notifications with multi-channel delivery tracking
    Uses Integer in the ORM test model for SQLite autoincrement compatibility.
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)  # Reference to auth_db.users.id
    notification_type_id: Mapped[int] = mapped_column(Integer, ForeignKey("notification_types.id", ondelete="CASCADE"), nullable=False)
    template_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("notification_templates.id", ondelete="SET NULL"))
    priority: Mapped[Optional[str]] = mapped_column(
        Enum('low', 'normal', 'high', 'critical', name='notification_priority'),
        default='normal'
    )

    # Content
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    data_payload: Mapped[Optional[Any]] = mapped_column(JSON)

    # Source
    source_system: Mapped[Optional[str]] = mapped_column(String(50))  # contract-service, payment-service, etc.
    source_reference_id: Mapped[Optional[str]] = mapped_column(String(100))

    # Status
    is_read: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    is_dismissed: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    dismissed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Scheduling
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(DateTime)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    deliveries = relationship("NotificationDelivery", back_populates="notification", cascade="all, delete-orphan")
    # New 2026-04-11: expose the parent NotificationType so to_dict() can emit
    # the human-readable `type_code` string ("deal_created", "payment_received",
    # ...) alongside the FK int. Both web (NotificationsPanel.jsx) and iOS
    # (NotificationsView.swift) switch on the string code for icon/color
    # mapping; without this enrichment they fell through to the default bell
    # for every server-backed row. Bug #20 in e2e_coverage_state.md.
    notification_type = relationship("NotificationType", lazy="joined")

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
            # `notification_type` is the string code resolved via the
            # joined NotificationType relationship. Web + iOS clients use
            # this for icon/color mapping. Falls back to None when the
            # related row is missing (shouldn't happen — FK is NOT NULL).
            "notification_type": self.notification_type.type_code if self.notification_type else None,
            "template_id": self.template_id,
            "priority": self.priority,
            "title": self.title,
            "body": self.body,
            # Backward-compat alias so legacy clients reading `message`
            # still see the body text. New clients should read `body`
            # directly.
            "message": self.body,
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

    @property
    def message(self):
        return self.body

    @message.setter
    def message(self, value):
        self.body = value

    @property
    def is_deleted(self):
        return bool(self.is_dismissed)

    @is_deleted.setter
    def is_deleted(self, value):
        self.is_dismissed = bool(value)

    @property
    def deleted_at(self):
        return self.dismissed_at

    @deleted_at.setter
    def deleted_at(self, value):
        self.dismissed_at = value


class NotificationDelivery(Base):
    """
    Notification Deliveries
    Multi-channel delivery tracking (replaces old DeliveryLog)
    Uses Integer in the ORM test model for SQLite autoincrement compatibility.
    """

    __tablename__ = "notification_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    notification_id: Mapped[int] = mapped_column(Integer, ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False)
    channel: Mapped[str] = mapped_column(
        Enum('email', 'sms', 'push', 'in_app', 'webhook', name='delivery_channel'),
        nullable=False
    )
    status: Mapped[Optional[str]] = mapped_column(
        Enum('pending', 'sent', 'delivered', 'failed', 'bounced', name='delivery_status'),
        default='pending'
    )

    # Delivery Details
    recipient_address: Mapped[Optional[str]] = mapped_column(String(255))  # Email, phone, device token
    provider: Mapped[Optional[str]] = mapped_column(String(50))  # sendgrid, twilio, firebase, etc.
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(255))

    # Retry Logic
    attempt_count: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Error Handling
    error_code: Mapped[Optional[str]] = mapped_column(String(50))
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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

    @property
    def delivery_status(self):
        return self.status

    @delivery_status.setter
    def delivery_status(self, value):
        self.status = value

    @property
    def external_message_id(self):
        return self.provider_message_id

    @external_message_id.setter
    def external_message_id(self, value):
        self.provider_message_id = value

    @property
    def retry_count(self):
        return self.attempt_count

    @retry_count.setter
    def retry_count(self, value):
        self.attempt_count = value


class NotificationBatch(Base):
    """
    Notification Batches
    Group-level notifications for campaigns and bulk messaging
    """

    __tablename__ = "notification_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_name: Mapped[str] = mapped_column(String(255), nullable=False)
    notification_type_id: Mapped[int] = mapped_column(Integer, ForeignKey("notification_types.id", ondelete="CASCADE"), nullable=False)
    template_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("notification_templates.id", ondelete="SET NULL"))
    total_recipients: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    sent_count: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    failed_count: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    status: Mapped[Optional[str]] = mapped_column(
        Enum('pending', 'processing', 'completed', 'failed', 'cancelled', name='batch_status'),
        default='pending'
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_by: Mapped[Optional[int]] = mapped_column(Integer)  # Reference to auth_db.users.id
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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

    @property
    def batch_status(self):
        return getattr(self, "_legacy_batch_status", "draft" if self.status == "pending" else self.status)

    @batch_status.setter
    def batch_status(self, value):
        self._legacy_batch_status = value

    @property
    def scheduled_send_time(self):
        return getattr(self, "_scheduled_send_time", None)

    @scheduled_send_time.setter
    def scheduled_send_time(self, value):
        self._scheduled_send_time = value


class VerifiedEmail(Base):
    """
    Verified Emails
    Verified email addresses for users
    """

    __tablename__ = "verified_emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)  # Reference to auth_db.users.id
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    is_primary: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)

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

    @property
    def channel_type(self):
        return "email"

    @property
    def channel_value(self):
        return self.email

    @channel_value.setter
    def channel_value(self, value):
        self.email = value

    @property
    def is_verified(self):
        return bool(self.verified_at)

    @is_verified.setter
    def is_verified(self, value):
        self.verified_at = datetime.utcnow() if value else None

    @property
    def is_active(self):
        return bool(getattr(self, "_is_active", True))

    @is_active.setter
    def is_active(self, value):
        self._is_active = bool(value)


class VerifiedPhone(Base):
    """
    Verified Phones
    Verified phone numbers for users
    """

    __tablename__ = "verified_phones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)  # Reference to auth_db.users.id
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    country_code: Mapped[Optional[str]] = mapped_column(String(5), default='+1')
    is_primary: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)

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

    @property
    def channel_type(self):
        return "sms"

    @property
    def channel_value(self):
        return f"{self.country_code}{self.phone}"

    @channel_value.setter
    def channel_value(self, value):
        self.phone = value

    @property
    def is_verified(self):
        return bool(self.verified_at)

    @is_verified.setter
    def is_verified(self, value):
        self.verified_at = datetime.utcnow() if value else None

    @property
    def is_active(self):
        return bool(getattr(self, "_is_active", True))

    @is_active.setter
    def is_active(self, value):
        self._is_active = bool(value)


# Legacy aliases for backwards compatibility
# These can be removed once all code is updated to use new class names
UserNotificationPreference = NotificationPreference
DeliveryLog = NotificationDelivery
NotificationChannel = VerifiedEmail  # Partial mapping - actual schema is different
