"""
Notification Service Tests

Comprehensive test suite covering templates, notifications, preferences,
channels, delivery tracking, and batch processing.
"""

import pytest
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

# Add src directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from models import (
    NotificationTemplate,
    UserNotificationPreference,
    Notification,
    NotificationChannel,
    DeliveryLog,
    NotificationBatch,
)
from notification_service import (
    NotificationService,
    UserPreferenceService,
    NotificationChannelService,
    DeliveryService,
    NotificationBatchService,
)


# ============================================================================
# Notification Template Tests
# ============================================================================


class TestNotificationTemplates:
    """Test notification template creation and retrieval"""

    def test_create_notification_template(self, db_session: Session):
        """Test creating a notification template"""
        template = NotificationService.create_notification_template(
            db=db_session,
            template_name="welcome_email",
            template_type="email",
            content="Welcome {{user_name}} to our platform!",
            subject="Welcome!",
            variables=["user_name"],
            priority="normal",
        )

        assert template.id is not None
        assert template.template_name == "welcome_email"
        assert template.template_type == "email"
        assert template.is_active == 1
        assert template.is_deleted == 0

    def test_get_template_by_name(self, db_session: Session):
        """Test retrieving template by name"""
        NotificationService.create_notification_template(
            db=db_session,
            template_name="order_confirmation",
            template_type="email",
            content="Order {{order_id}} confirmed",
            subject="Order Confirmed",
        )

        template = NotificationService.get_template_by_name(db=db_session, template_name="order_confirmation")
        assert template is not None
        assert template.template_name == "order_confirmation"

    def test_get_active_templates(self, db_session: Session):
        """Test retrieving active templates"""
        NotificationService.create_notification_template(
            db=db_session,
            template_name="template1",
            template_type="email",
            content="Content 1",
        )
        NotificationService.create_notification_template(
            db=db_session,
            template_name="template2",
            template_type="sms",
            content="Content 2",
        )

        templates, total = NotificationService.get_active_templates(db=db_session)
        assert total == 2
        assert len(templates) == 2

    def test_get_templates_by_type(self, db_session: Session):
        """Test filtering templates by type"""
        NotificationService.create_notification_template(
            db=db_session,
            template_name="email_template",
            template_type="email",
            content="Email content",
        )
        NotificationService.create_notification_template(
            db=db_session,
            template_name="sms_template",
            template_type="sms",
            content="SMS content",
        )

        templates, total = NotificationService.get_active_templates(db=db_session, template_type="email")
        assert total == 1
        assert templates[0].template_type == "email"


# ============================================================================
# Notification Tests
# ============================================================================


class TestNotifications:
    """Test notification creation and management"""

    def test_send_notification(self, db_session: Session):
        """Test sending a notification"""
        template = NotificationService.create_notification_template(
            db=db_session,
            template_name="test_template",
            template_type="email",
            content="Test notification",
        )

        notification = NotificationService.send_notification(
            db=db_session,
            user_id=1,
            template_id=template.id,
            notification_type="order_update",
            title="Order Update",
            message="Your order has been processed",
        )

        assert notification.id is not None
        assert notification.user_id == 1
        assert notification.is_read == 0
        assert notification.is_deleted == 0

    def test_get_user_notifications(self, db_session: Session):
        """Test retrieving user notifications"""
        template = NotificationService.create_notification_template(
            db=db_session,
            template_name="template",
            template_type="email",
            content="Content",
        )

        NotificationService.send_notification(
            db=db_session,
            user_id=1,
            template_id=template.id,
            notification_type="type1",
            message="Message 1",
        )
        NotificationService.send_notification(
            db=db_session,
            user_id=1,
            template_id=template.id,
            notification_type="type2",
            message="Message 2",
        )

        notifications, total = NotificationService.get_user_notifications(db=db_session, user_id=1)
        assert total == 2
        assert len(notifications) == 2

    def test_mark_notification_as_read(self, db_session: Session):
        """Test marking notification as read"""
        template = NotificationService.create_notification_template(
            db=db_session,
            template_name="template",
            template_type="email",
            content="Content",
        )

        notification = NotificationService.send_notification(
            db=db_session,
            user_id=1,
            template_id=template.id,
            notification_type="test",
            message="Test",
        )

        updated = NotificationService.mark_as_read(
            db=db_session,
            notification_id=notification.id,
            user_id=1,
        )

        assert updated.is_read == 1
        assert updated.read_at is not None

    def test_unread_notifications_filter(self, db_session: Session):
        """Test filtering unread notifications"""
        template = NotificationService.create_notification_template(
            db=db_session,
            template_name="template",
            template_type="email",
            content="Content",
        )

        n1 = NotificationService.send_notification(
            db=db_session,
            user_id=1,
            template_id=template.id,
            notification_type="type1",
            message="Message 1",
        )
        n2 = NotificationService.send_notification(
            db=db_session,
            user_id=1,
            template_id=template.id,
            notification_type="type2",
            message="Message 2",
        )

        NotificationService.mark_as_read(db=db_session, notification_id=n1.id, user_id=1)

        unread, total = NotificationService.get_user_notifications(
            db=db_session,
            user_id=1,
            unread_only=True,
        )
        assert total == 1
        assert unread[0].id == n2.id

    def test_delete_notification(self, db_session: Session):
        """Test soft deleting a notification"""
        template = NotificationService.create_notification_template(
            db=db_session,
            template_name="template",
            template_type="email",
            content="Content",
        )

        notification = NotificationService.send_notification(
            db=db_session,
            user_id=1,
            template_id=template.id,
            notification_type="test",
            message="Test",
        )

        success = NotificationService.delete_notification(
            db=db_session,
            notification_id=notification.id,
            user_id=1,
        )

        assert success is True
        db_session.refresh(notification)
        assert notification.is_deleted == 1
        assert notification.deleted_at is not None


# ============================================================================
# User Preference Tests
# ============================================================================


class TestUserPreferences:
    """Test user notification preferences"""

    def test_get_or_create_preferences(self, db_session: Session):
        """Test getting or creating default preferences"""
        prefs = UserPreferenceService.get_or_create_preferences(db=db_session, user_id=1)
        assert prefs.user_id == 1
        assert prefs.email_enabled == 1
        assert prefs.sms_enabled == 1
        assert prefs.do_not_disturb_enabled == 0

    def test_update_preferences(self, db_session: Session):
        """Test updating notification preferences"""
        updated = UserPreferenceService.update_preferences(
            db=db_session,
            user_id=1,
            email_enabled=False,
            sms_enabled=True,
            timezone="America/New_York",
        )

        assert updated.email_enabled == 0
        assert updated.sms_enabled == 1
        assert updated.timezone == "America/New_York"

    def test_dnd_mode(self, db_session: Session):
        """Test do-not-disturb mode"""
        UserPreferenceService.update_preferences(
            db=db_session,
            user_id=1,
            do_not_disturb=True,
        )

        allowed = UserPreferenceService.is_notification_allowed(db=db_session, user_id=1, channel_type="email")
        assert allowed is False

    def test_channel_specific_preferences(self, db_session: Session):
        """Test channel-specific notification preferences"""
        UserPreferenceService.update_preferences(
            db=db_session,
            user_id=1,
            email_enabled=True,
            sms_enabled=False,
        )

        email_allowed = UserPreferenceService.is_notification_allowed(db=db_session, user_id=1, channel_type="email")
        sms_allowed = UserPreferenceService.is_notification_allowed(db=db_session, user_id=1, channel_type="sms")

        assert email_allowed is True
        assert sms_allowed is False


# ============================================================================
# Notification Channel Tests
# ============================================================================


class TestNotificationChannels:
    """Test notification channel management"""

    def test_add_notification_channel(self, db_session: Session):
        """Test adding a notification channel"""
        channel = NotificationChannelService.add_channel(
            db=db_session,
            user_id=1,
            channel_type="email",
            channel_value="user@example.com",
            is_primary=True,
        )

        assert channel.id is not None
        assert channel.user_id == 1
        assert channel.channel_type == "email"
        assert channel.is_primary == 1
        assert channel.is_verified == 0

    def test_get_user_channels(self, db_session: Session):
        """Test retrieving user channels"""
        NotificationChannelService.add_channel(
            db=db_session,
            user_id=1,
            channel_type="email",
            channel_value="user@example.com",
        )
        NotificationChannelService.add_channel(
            db=db_session,
            user_id=1,
            channel_type="sms",
            channel_value="+1234567890",
        )

        channels = NotificationChannelService.get_user_channels(db=db_session, user_id=1)
        assert len(channels) == 2

    def test_verify_channel(self, db_session: Session):
        """Test verifying a notification channel"""
        channel = NotificationChannelService.add_channel(
            db=db_session,
            user_id=1,
            channel_type="email",
            channel_value="user@example.com",
        )

        # Simulate setting verification token
        channel.verification_token = "token123"
        db_session.commit()

        success = NotificationChannelService.verify_channel(
            db=db_session,
            channel_id=channel.id,
            verification_token="token123",
        )

        assert success is True
        db_session.refresh(channel)
        assert channel.is_verified == 1

    def test_deactivate_channel(self, db_session: Session):
        """Test deactivating a notification channel"""
        channel = NotificationChannelService.add_channel(
            db=db_session,
            user_id=1,
            channel_type="email",
            channel_value="user@example.com",
        )

        success = NotificationChannelService.deactivate_channel(db=db_session, channel_id=channel.id)

        assert success is True
        db_session.refresh(channel)
        assert channel.is_active == 0

    def test_channel_type_filtering(self, db_session: Session):
        """Test filtering channels by type"""
        NotificationChannelService.add_channel(
            db=db_session,
            user_id=1,
            channel_type="email",
            channel_value="user@example.com",
        )
        NotificationChannelService.add_channel(
            db=db_session,
            user_id=1,
            channel_type="email",
            channel_value="user2@example.com",
        )
        NotificationChannelService.add_channel(
            db=db_session,
            user_id=1,
            channel_type="sms",
            channel_value="+1234567890",
        )

        email_channels = NotificationChannelService.get_user_channels(
            db=db_session,
            user_id=1,
            channel_type="email",
        )
        assert len(email_channels) == 2


# ============================================================================
# Delivery & Retry Tests
# ============================================================================


class TestDeliveryTracking:
    """Test delivery tracking and retry logic"""

    def test_get_pending_deliveries(self, db_session: Session):
        """Test retrieving pending deliveries"""
        template = NotificationService.create_notification_template(
            db=db_session,
            template_name="template",
            template_type="email",
            content="Content",
        )
        channel = NotificationChannelService.add_channel(
            db=db_session,
            user_id=1,
            channel_type="email",
            channel_value="user@example.com",
            is_primary=True,
        )
        channel.is_verified = 1
        db_session.commit()

        NotificationService.send_notification(
            db=db_session,
            user_id=1,
            template_id=template.id,
            notification_type="test",
            message="Test",
        )

        pending = DeliveryService.get_pending_deliveries(db=db_session)
        assert len(pending) > 0

    def test_mark_delivery_success(self, db_session: Session):
        """Test marking delivery as successful"""
        template = NotificationService.create_notification_template(
            db=db_session,
            template_name="template",
            template_type="email",
            content="Content",
        )
        channel = NotificationChannelService.add_channel(
            db=db_session,
            user_id=1,
            channel_type="email",
            channel_value="user@example.com",
            is_primary=True,
        )
        channel.is_verified = 1
        db_session.commit()

        notification = NotificationService.send_notification(
            db=db_session,
            user_id=1,
            template_id=template.id,
            notification_type="test",
            message="Test",
        )

        delivery = (
            db_session.query(DeliveryLog)
            .filter(DeliveryLog.notification_id == notification.id)
            .first()
        )

        updated = DeliveryService.mark_delivered(
            db=db_session,
            delivery_log_id=delivery.id,
            external_message_id="ext123",
        )

        assert updated.delivery_status == "delivered"
        assert updated.external_message_id == "ext123"
        assert updated.delivered_at is not None

    def test_mark_delivery_failed_with_retry(self, db_session: Session):
        """Test marking delivery as failed with retry"""
        template = NotificationService.create_notification_template(
            db=db_session,
            template_name="template",
            template_type="email",
            content="Content",
        )
        channel = NotificationChannelService.add_channel(
            db=db_session,
            user_id=1,
            channel_type="email",
            channel_value="user@example.com",
            is_primary=True,
        )
        channel.is_verified = 1
        db_session.commit()

        notification = NotificationService.send_notification(
            db=db_session,
            user_id=1,
            template_id=template.id,
            notification_type="test",
            message="Test",
        )

        delivery = (
            db_session.query(DeliveryLog)
            .filter(DeliveryLog.notification_id == notification.id)
            .first()
        )

        updated = DeliveryService.mark_failed(
            db=db_session,
            delivery_log_id=delivery.id,
            error_message="Connection timeout",
            should_retry=True,
        )

        assert updated.delivery_status == "pending"
        assert updated.retry_count == 1
        assert updated.next_retry_at is not None

    def test_delivery_statistics(self, db_session: Session):
        """Test getting delivery statistics"""
        template = NotificationService.create_notification_template(
            db=db_session,
            template_name="template",
            template_type="email",
            content="Content",
        )
        channel = NotificationChannelService.add_channel(
            db=db_session,
            user_id=1,
            channel_type="email",
            channel_value="user@example.com",
            is_primary=True,
        )
        channel.is_verified = 1
        db_session.commit()

        notification = NotificationService.send_notification(
            db=db_session,
            user_id=1,
            template_id=template.id,
            notification_type="test",
            message="Test",
        )

        delivery = (
            db_session.query(DeliveryLog)
            .filter(DeliveryLog.notification_id == notification.id)
            .first()
        )
        DeliveryService.mark_delivered(db=db_session, delivery_log_id=delivery.id)

        stats = DeliveryService.get_delivery_statistics(db=db_session, notification_id=notification.id)
        assert stats["total"] == 1
        assert stats["delivered"] == 1
        assert stats["success_rate"] == 100.0


# ============================================================================
# Batch Notification Tests
# ============================================================================


class TestNotificationBatches:
    """Test batch notification management"""

    def test_create_notification_batch(self, db_session: Session):
        """Test creating a notification batch"""
        template = NotificationService.create_notification_template(
            db=db_session,
            template_name="template",
            template_type="email",
            content="Content",
        )

        batch = NotificationBatchService.create_batch(
            db=db_session,
            batch_name="welcome_campaign",
            batch_type="campaign",
            template_id=template.id,
            target_user_count=1000,
        )

        assert batch.id is not None
        assert batch.batch_name == "welcome_campaign"
        assert batch.batch_status == "draft"

    def test_schedule_batch(self, db_session: Session):
        """Test scheduling a batch"""
        template = NotificationService.create_notification_template(
            db=db_session,
            template_name="template",
            template_type="email",
            content="Content",
        )

        batch = NotificationBatchService.create_batch(
            db=db_session,
            batch_name="scheduled_campaign",
            batch_type="scheduled",
            template_id=template.id,
        )

        scheduled_time = datetime.utcnow() + timedelta(hours=1)
        updated = NotificationBatchService.schedule_batch(
            db=db_session,
            batch_id=batch.id,
            scheduled_time=scheduled_time,
        )

        assert updated.batch_status == "scheduled"
        assert updated.scheduled_send_time == scheduled_time

    def test_batch_statistics(self, db_session: Session):
        """Test getting batch statistics"""
        template = NotificationService.create_notification_template(
            db=db_session,
            template_name="template",
            template_type="email",
            content="Content",
        )

        batch = NotificationBatchService.create_batch(
            db=db_session,
            batch_name="stats_test",
            batch_type="campaign",
            template_id=template.id,
            target_user_count=1000,
        )
        batch.sent_count = 950
        batch.failed_count = 50
        batch.success_rate = 95.0
        db_session.commit()

        stats = NotificationBatchService.get_batch_statistics(db=db_session, batch_id=batch.id)
        assert stats["batch_name"] == "stats_test"
        assert stats["sent_count"] == 950
        assert stats["success_rate"] == 95.0


# ============================================================================
# Integration Tests
# ============================================================================


class TestNotificationIntegration:
    """Integration tests for complete workflows"""

    def test_full_notification_workflow(self, db_session: Session):
        """Test complete notification workflow from creation to delivery"""
        # Create template
        template = NotificationService.create_notification_template(
            db=db_session,
            template_name="integration_test",
            template_type="email",
            content="Integration test notification",
        )

        # Set up user preferences
        UserPreferenceService.update_preferences(
            db=db_session,
            user_id=1,
            email_enabled=True,
        )

        # Add notification channel
        channel = NotificationChannelService.add_channel(
            db=db_session,
            user_id=1,
            channel_type="email",
            channel_value="user@example.com",
            is_primary=True,
        )
        channel.is_verified = 1
        db_session.commit()

        # Send notification
        notification = NotificationService.send_notification(
            db=db_session,
            user_id=1,
            template_id=template.id,
            notification_type="integration_test",
            message="Test workflow",
        )

        # Check delivery queued
        deliveries = DeliveryService.get_pending_deliveries(db=db_session)
        assert len(deliveries) == 1

        # Mark as delivered
        delivery = deliveries[0]
        DeliveryService.mark_delivered(
            db=db_session,
            delivery_log_id=delivery.id,
            external_message_id="ext_001",
        )

        # Verify delivery statistics
        stats = DeliveryService.get_delivery_statistics(db=db_session, notification_id=notification.id)
        assert stats["delivered"] == 1
        assert stats["success_rate"] == 100.0

    def test_soft_delete_pattern(self, db_session: Session):
        """Test soft delete pattern across models"""
        template = NotificationService.create_notification_template(
            db=db_session,
            template_name="soft_delete_test",
            template_type="email",
            content="Test",
        )

        notification = NotificationService.send_notification(
            db=db_session,
            user_id=1,
            template_id=template.id,
            notification_type="test",
            message="Test",
        )

        # Get before deletion
        before, _ = NotificationService.get_user_notifications(db=db_session, user_id=1)
        assert len(before) == 1

        # Soft delete
        NotificationService.delete_notification(db=db_session, notification_id=notification.id, user_id=1)

        # Should not appear in normal queries
        after, _ = NotificationService.get_user_notifications(db=db_session, user_id=1)
        assert len(after) == 0

        # But is still in database with is_deleted flag
        db_session.refresh(notification)
        assert notification.is_deleted == 1
