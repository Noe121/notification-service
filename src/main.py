"""
Notification Service API Endpoints - Phase 3

Comprehensive notification management including templates, preferences,
delivery tracking, and batch processing.
"""

from fastapi import FastAPI, HTTPException, Depends, status, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import create_engine
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
import os

# Import models and services
from models import Base, NotificationTemplate
from notification_service import (
    NotificationService,
    UserPreferenceService,
    NotificationChannelService,
    DeliveryService,
    NotificationBatchService,
)

# ============================================================================
# Setup
# ============================================================================

app = FastAPI(
    title="Notification Service API",
    description="Comprehensive notification management service with multi-channel delivery",
    version="3.0.0",
)

logger = logging.getLogger(__name__)

# Database setup
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///../notification_service.db/notifications.db",
)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create tables
try:
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created successfully")
except Exception as e:
    logger.error(f"Failed to create database tables: {e}")
    raise


# ============================================================================
# Dependency Injection
# ============================================================================


def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============================================================================
# Health Check
# ============================================================================


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "notification-service"}


# ============================================================================
# Notification Template Endpoints
# ============================================================================


@app.post("/templates", tags=["Templates"], status_code=201)
async def create_template(
    template_name: str,
    template_type: str,
    content: str,
    subject: Optional[str] = None,
    variables: Optional[List[str]] = None,
    priority: str = "normal",
    db: Session = Depends(get_db),
):
    """
    Create a new notification template

    - **template_name**: Unique template identifier
    - **template_type**: email, sms, push, in_app
    - **content**: Template content with {{variable}} placeholders
    - **subject**: Email subject line (optional)
    - **variables**: List of variable names used
    - **priority**: low, normal, high, urgent
    """
    try:
        template = NotificationService.create_notification_template(
            db=db,
            template_name=template_name,
            template_type=template_type,
            content=content,
            subject=subject,
            variables=variables,
            priority=priority,
        )
        return template.to_dict()
    except Exception as e:
        logger.error(f"Error creating template: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/templates", tags=["Templates"])
async def list_templates(
    template_type: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Get active notification templates"""
    templates, total = NotificationService.get_active_templates(
        db=db,
        template_type=template_type,
        limit=limit,
        offset=offset,
    )
    return {
        "templates": [t.to_dict() for t in templates],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/templates/{template_id}", tags=["Templates"])
async def get_template(template_id: int, db: Session = Depends(get_db)):
    """Get a specific notification template"""
    template = db.query(NotificationTemplate).filter(NotificationTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template.to_dict()


# ============================================================================
# Notification Endpoints
# ============================================================================


@app.post("/notifications", tags=["Notifications"], status_code=201)
async def send_notification(
    user_id: int,
    template_id: int,
    notification_type: str,
    title: Optional[str] = None,
    message: Optional[str] = None,
    priority: str = "normal",
    source_system: str = "system",
    data_payload: Optional[Dict[str, Any]] = None,
    db: Session = Depends(get_db),
):
    """
    Send a notification to a user

    - **user_id**: Target user ID
    - **template_id**: Template to use
    - **notification_type**: Type of notification (order_update, payment_alert, etc.)
    - **title**: Notification title
    - **message**: Notification message
    - **priority**: low, normal, high, urgent
    - **source_system**: System that triggered notification
    - **data_payload**: Additional data for rich notifications
    """
    try:
        notification = NotificationService.send_notification(
            db=db,
            user_id=user_id,
            template_id=template_id,
            notification_type=notification_type,
            title=title,
            message=message,
            data_payload=data_payload,
            priority=priority,
            source_system=source_system,
        )
        return notification.to_dict()
    except Exception as e:
        logger.error(f"Error sending notification: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/users/{user_id}/notifications", tags=["Notifications"])
async def get_user_notifications(
    user_id: int,
    unread_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Get notifications for a user"""
    notifications, total = NotificationService.get_user_notifications(
        db=db,
        user_id=user_id,
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )
    return {
        "notifications": [n.to_dict() for n in notifications],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.put("/notifications/{notification_id}/read", tags=["Notifications"])
async def mark_notification_read(
    notification_id: int,
    user_id: int,
    db: Session = Depends(get_db),
):
    """Mark a notification as read"""
    notification = NotificationService.mark_as_read(db=db, notification_id=notification_id, user_id=user_id)
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    return notification.to_dict()


@app.delete("/notifications/{notification_id}", tags=["Notifications"])
async def delete_notification(
    notification_id: int,
    user_id: int,
    db: Session = Depends(get_db),
):
    """Soft delete a notification"""
    success = NotificationService.delete_notification(db=db, notification_id=notification_id, user_id=user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"message": "Notification deleted"}


# ============================================================================
# User Preferences Endpoints
# ============================================================================


@app.get("/users/{user_id}/preferences", tags=["Preferences"])
async def get_user_preferences(user_id: int, db: Session = Depends(get_db)):
    """Get notification preferences for a user"""
    preferences = UserPreferenceService.get_or_create_preferences(db=db, user_id=user_id)
    return preferences.to_dict()


@app.put("/users/{user_id}/preferences", tags=["Preferences"])
async def update_user_preferences(
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
    db: Session = Depends(get_db),
):
    """Update notification preferences"""
    preferences = UserPreferenceService.update_preferences(
        db=db,
        user_id=user_id,
        email_enabled=email_enabled,
        sms_enabled=sms_enabled,
        push_enabled=push_enabled,
        in_app_enabled=in_app_enabled,
        email_frequency=email_frequency,
        timezone=timezone,
        do_not_disturb=do_not_disturb,
        quiet_hours_start=quiet_hours_start,
        quiet_hours_end=quiet_hours_end,
    )
    return preferences.to_dict()


# ============================================================================
# Notification Channel Endpoints
# ============================================================================


@app.post("/users/{user_id}/channels", tags=["Channels"], status_code=201)
async def add_notification_channel(
    user_id: int,
    channel_type: str,
    channel_value: str,
    is_primary: bool = False,
    db: Session = Depends(get_db),
):
    """
    Add a notification channel for user

    - **channel_type**: email, sms, push, webhook
    - **channel_value**: Email address, phone number, device token, or webhook URL
    - **is_primary**: Whether this is the primary channel for the type
    """
    channel = NotificationChannelService.add_channel(
        db=db,
        user_id=user_id,
        channel_type=channel_type,
        channel_value=channel_value,
        is_primary=is_primary,
    )
    return channel.to_dict()


@app.get("/users/{user_id}/channels", tags=["Channels"])
async def get_user_channels(
    user_id: int,
    channel_type: Optional[str] = None,
    verified_only: bool = False,
    db: Session = Depends(get_db),
):
    """Get notification channels for a user"""
    channels = NotificationChannelService.get_user_channels(
        db=db,
        user_id=user_id,
        channel_type=channel_type,
        verified_only=verified_only,
    )
    return {"channels": [c.to_dict() for c in channels]}


@app.post("/channels/{channel_id}/verify", tags=["Channels"])
async def verify_channel(
    channel_id: int,
    verification_token: str,
    db: Session = Depends(get_db),
):
    """Verify a notification channel"""
    success = NotificationChannelService.verify_channel(db=db, channel_id=channel_id, verification_token=verification_token)
    if not success:
        raise HTTPException(status_code=400, detail="Verification failed")
    return {"message": "Channel verified"}


@app.delete("/channels/{channel_id}", tags=["Channels"])
async def deactivate_channel(channel_id: int, db: Session = Depends(get_db)):
    """Deactivate a notification channel"""
    success = NotificationChannelService.deactivate_channel(db=db, channel_id=channel_id)
    if not success:
        raise HTTPException(status_code=404, detail="Channel not found")
    return {"message": "Channel deactivated"}


# ============================================================================
# Delivery & Retry Endpoints
# ============================================================================


@app.get("/delivery/pending", tags=["Delivery"])
async def get_pending_deliveries(
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """Get pending deliveries for retry processing"""
    deliveries = DeliveryService.get_pending_deliveries(db=db, limit=limit)
    return {
        "deliveries": [d.to_dict() for d in deliveries],
        "count": len(deliveries),
    }


@app.post("/delivery/{delivery_log_id}/success", tags=["Delivery"])
async def mark_delivery_success(
    delivery_log_id: int,
    external_message_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Mark a delivery as successful"""
    delivery = DeliveryService.mark_delivered(
        db=db,
        delivery_log_id=delivery_log_id,
        external_message_id=external_message_id,
    )
    if delivery is None:
        raise HTTPException(status_code=404, detail="Delivery log not found")
    return delivery.to_dict()


@app.post("/delivery/{delivery_log_id}/failure", tags=["Delivery"])
async def mark_delivery_failure(
    delivery_log_id: int,
    error_message: str,
    status_code: Optional[int] = None,
    should_retry: bool = True,
    db: Session = Depends(get_db),
):
    """Mark a delivery as failed"""
    if hasattr(DeliveryService, "mark_failed"):
        delivery = DeliveryService.mark_failed(
            db=db,
            delivery_log_id=delivery_log_id,
            error_message=error_message,
            status_code=status_code,
            should_retry=should_retry,
        )
        if delivery is None:
            raise HTTPException(status_code=404, detail="Delivery log not found")
        return delivery.to_dict()
    else:
        raise HTTPException(status_code=500, detail="mark_failed not implemented")


@app.get("/notifications/{notification_id}/delivery-stats", tags=["Delivery"])
async def get_delivery_statistics(
    notification_id: int,
    db: Session = Depends(get_db),
):
    """Get delivery statistics for a notification"""
    if hasattr(DeliveryService, "get_delivery_statistics"):
        stats = DeliveryService.get_delivery_statistics(db=db, notification_id=notification_id)
        return stats
    else:
        raise HTTPException(status_code=500, detail="get_delivery_statistics not implemented")


# ============================================================================
# Batch Notification Endpoints
# ============================================================================


@app.post("/batches", tags=["Batches"], status_code=201)
async def create_notification_batch(
    batch_name: str,
    batch_type: str,
    template_id: int,
    target_user_count: Optional[int] = None,
    created_by: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """
    Create a new notification batch

    - **batch_type**: campaign, bulk, scheduled, triggered
    """
    batch = NotificationBatchService.create_batch(
        db=db,
        batch_name=batch_name,
        batch_type=batch_type,
        template_id=template_id,
        target_user_count=target_user_count,
        created_by=created_by,
    )
    return batch.to_dict()


@app.post("/batches/{batch_id}/schedule", tags=["Batches"])
async def schedule_batch(
    batch_id: int,
    scheduled_time: datetime,
    db: Session = Depends(get_db),
):
    """Schedule a batch for sending"""
    batch = NotificationBatchService.schedule_batch(db=db, batch_id=batch_id, scheduled_time=scheduled_time)
    return batch.to_dict()


@app.get("/batches/{batch_id}/stats", tags=["Batches"])
async def get_batch_statistics(batch_id: int, db: Session = Depends(get_db)):
    """Get statistics for a notification batch"""
    stats = NotificationBatchService.get_batch_statistics(db=db, batch_id=batch_id)
    if not stats:
        raise HTTPException(status_code=404, detail="Batch not found")
    return stats


# ============================================================================
# Error Handlers
# ============================================================================


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Custom HTTP exception handler"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8013)
