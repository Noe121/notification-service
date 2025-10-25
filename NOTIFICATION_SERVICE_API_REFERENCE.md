# Notification Service API Reference

## Base URL

```
http://localhost:8000/api/v1
http://production.example.com/notification-service
```

## Authentication

All endpoints support optional bearer token authentication:

```
Authorization: Bearer <token>
```

## Response Format

All responses are JSON:

```json
{
  "data": {},
  "message": "Success",
  "status": 200
}
```

## Error Handling

Error responses include status code and detail:

```json
{
  "detail": "Resource not found",
  "status": 404
}
```

---

## Endpoints

### Health Check

#### GET /health

Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "service": "notification-service"
}
```

---

## Templates

### Create Notification Template

**POST /templates**

Create a new notification template.

**Parameters:**
- `template_name` (string, required): Unique template identifier
- `template_type` (string, required): Type of notification (email, sms, push, in_app)
- `content` (string, required): Template content with {{variable}} placeholders
- `subject` (string, optional): Email subject line
- `variables` (array, optional): List of variable names
- `priority` (string, optional): Priority level (low, normal, high, urgent)

**Example Request:**
```bash
curl -X POST http://localhost:8000/templates \
  -H "Content-Type: application/json" \
  -d '{
    "template_name": "welcome_email",
    "template_type": "email",
    "subject": "Welcome!",
    "content": "Welcome {{user_name}}!",
    "variables": ["user_name"],
    "priority": "normal"
  }'
```

**Example Response (201 Created):**
```json
{
  "id": 1,
  "template_name": "welcome_email",
  "template_type": "email",
  "subject": "Welcome!",
  "content": "Welcome {{user_name}}!",
  "priority": "normal",
  "is_active": true,
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

### List Templates

**GET /templates**

Get active notification templates.

**Query Parameters:**
- `template_type` (string, optional): Filter by template type
- `limit` (integer, optional): Results per page (default: 100, max: 500)
- `offset` (integer, optional): Pagination offset (default: 0)

**Example Request:**
```bash
curl "http://localhost:8000/templates?template_type=email&limit=20&offset=0"
```

**Example Response (200 OK):**
```json
{
  "templates": [
    {
      "id": 1,
      "template_name": "welcome_email",
      "template_type": "email",
      "priority": "normal",
      "is_active": true,
      "created_at": "2024-01-15T10:30:00Z"
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0
}
```

---

## Notifications

### Send Notification

**POST /notifications**

Send a notification to a user.

**Parameters:**
- `user_id` (integer, required): Target user ID
- `template_id` (integer, required): Template to use
- `notification_type` (string, required): Type of notification
- `title` (string, optional): Notification title
- `message` (string, optional): Message content
- `priority` (string, optional): Priority level
- `source_system` (string, optional): Originating system
- `data_payload` (object, optional): Additional data

**Example Request:**
```bash
curl -X POST http://localhost:8000/notifications \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": 123,
    "template_id": 1,
    "notification_type": "order_update",
    "title": "Order Confirmed",
    "message": "Your order #456 has been confirmed",
    "priority": "high",
    "source_system": "order-service"
  }'
```

**Example Response (201 Created):**
```json
{
  "id": 1,
  "user_id": 123,
  "template_id": 1,
  "notification_type": "order_update",
  "title": "Order Confirmed",
  "message": "Your order #456 has been confirmed",
  "priority": "high",
  "is_read": false,
  "source_system": "order-service",
  "created_at": "2024-01-15T10:30:00Z"
}
```

### Get User Notifications

**GET /users/{user_id}/notifications**

Get notifications for a user.

**Query Parameters:**
- `unread_only` (boolean, optional): Show only unread notifications
- `limit` (integer, optional): Results per page (default: 50)
- `offset` (integer, optional): Pagination offset (default: 0)

**Example Request:**
```bash
curl "http://localhost:8000/users/123/notifications?unread_only=true&limit=10"
```

**Example Response (200 OK):**
```json
{
  "notifications": [
    {
      "id": 1,
      "user_id": 123,
      "notification_type": "order_update",
      "title": "Order Confirmed",
      "message": "Your order has been confirmed",
      "is_read": false,
      "created_at": "2024-01-15T10:30:00Z"
    }
  ],
  "total": 5,
  "limit": 10,
  "offset": 0
}
```

### Mark Notification Read

**PUT /notifications/{notification_id}/read**

Mark a notification as read.

**Parameters:**
- `user_id` (integer, required): User ID (for authorization)

**Example Request:**
```bash
curl -X PUT http://localhost:8000/notifications/1/read \
  -H "Content-Type: application/json" \
  -d '{"user_id": 123}'
```

**Example Response (200 OK):**
```json
{
  "id": 1,
  "user_id": 123,
  "is_read": true,
  "read_at": "2024-01-15T10:35:00Z"
}
```

### Delete Notification

**DELETE /notifications/{notification_id}**

Soft delete a notification.

**Parameters:**
- `user_id` (integer, required): User ID (for authorization)

**Example Request:**
```bash
curl -X DELETE "http://localhost:8000/notifications/1?user_id=123"
```

**Example Response (200 OK):**
```json
{
  "message": "Notification deleted"
}
```

---

## User Preferences

### Get User Preferences

**GET /users/{user_id}/preferences**

Get notification preferences for a user.

**Example Request:**
```bash
curl "http://localhost:8000/users/123/preferences"
```

**Example Response (200 OK):**
```json
{
  "id": 1,
  "user_id": 123,
  "email_enabled": true,
  "sms_enabled": true,
  "push_enabled": false,
  "in_app_enabled": true,
  "email_frequency": "immediate",
  "timezone": "America/New_York",
  "do_not_disturb_enabled": false,
  "created_at": "2024-01-15T10:30:00Z"
}
```

### Update User Preferences

**PUT /users/{user_id}/preferences**

Update notification preferences.

**Parameters (all optional):**
- `email_enabled` (boolean)
- `sms_enabled` (boolean)
- `push_enabled` (boolean)
- `in_app_enabled` (boolean)
- `email_frequency` (string): immediate, daily, weekly, never
- `timezone` (string)
- `do_not_disturb` (boolean)
- `quiet_hours_start` (string): HH:MM format
- `quiet_hours_end` (string): HH:MM format

**Example Request:**
```bash
curl -X PUT http://localhost:8000/users/123/preferences \
  -H "Content-Type: application/json" \
  -d '{
    "email_enabled": true,
    "sms_enabled": false,
    "timezone": "Europe/London",
    "do_not_disturb": false,
    "quiet_hours_start": "22:00",
    "quiet_hours_end": "08:00"
  }'
```

**Example Response (200 OK):**
```json
{
  "id": 1,
  "user_id": 123,
  "email_enabled": true,
  "sms_enabled": false,
  "timezone": "Europe/London",
  "quiet_hours_start": "22:00",
  "quiet_hours_end": "08:00",
  "updated_at": "2024-01-15T10:35:00Z"
}
```

---

## Notification Channels

### Add Notification Channel

**POST /users/{user_id}/channels**

Add a notification channel for a user.

**Parameters:**
- `channel_type` (string, required): email, sms, push, webhook
- `channel_value` (string, required): Email/phone/token/URL
- `is_primary` (boolean, optional): Set as primary channel

**Example Request:**
```bash
curl -X POST http://localhost:8000/users/123/channels \
  -H "Content-Type: application/json" \
  -d '{
    "channel_type": "email",
    "channel_value": "user@example.com",
    "is_primary": true
  }'
```

**Example Response (201 Created):**
```json
{
  "id": 1,
  "user_id": 123,
  "channel_type": "email",
  "is_verified": false,
  "is_primary": true,
  "is_active": true,
  "created_at": "2024-01-15T10:30:00Z"
}
```

### Get User Channels

**GET /users/{user_id}/channels**

Get notification channels for a user.

**Query Parameters:**
- `channel_type` (string, optional): Filter by type
- `verified_only` (boolean, optional): Show only verified channels

**Example Request:**
```bash
curl "http://localhost:8000/users/123/channels?verified_only=true"
```

**Example Response (200 OK):**
```json
{
  "channels": [
    {
      "id": 1,
      "user_id": 123,
      "channel_type": "email",
      "is_verified": true,
      "is_primary": true,
      "is_active": true,
      "verified_at": "2024-01-15T10:32:00Z",
      "created_at": "2024-01-15T10:30:00Z"
    }
  ]
}
```

### Verify Channel

**POST /channels/{channel_id}/verify**

Verify a notification channel.

**Parameters:**
- `verification_token` (string, required): Verification token sent to channel

**Example Request:**
```bash
curl -X POST http://localhost:8000/channels/1/verify \
  -H "Content-Type: application/json" \
  -d '{"verification_token": "abc123def456"}'
```

**Example Response (200 OK):**
```json
{
  "message": "Channel verified"
}
```

### Deactivate Channel

**DELETE /channels/{channel_id}**

Deactivate a notification channel.

**Example Request:**
```bash
curl -X DELETE http://localhost:8000/channels/1
```

**Example Response (200 OK):**
```json
{
  "message": "Channel deactivated"
}
```

---

## Delivery Tracking

### Get Pending Deliveries

**GET /delivery/pending**

Get pending deliveries ready for retry.

**Query Parameters:**
- `limit` (integer, optional): Results per page (default: 100, max: 1000)

**Example Request:**
```bash
curl "http://localhost:8000/delivery/pending?limit=50"
```

**Example Response (200 OK):**
```json
{
  "deliveries": [
    {
      "id": 1,
      "notification_id": 1,
      "channel_id": 1,
      "delivery_channel": "email",
      "delivery_status": "pending",
      "retry_count": 0,
      "created_at": "2024-01-15T10:30:00Z"
    }
  ],
  "count": 5
}
```

### Mark Delivery Successful

**POST /delivery/{delivery_log_id}/success**

Mark a delivery as successful.

**Parameters:**
- `external_message_id` (string, optional): Provider message ID

**Example Request:**
```bash
curl -X POST http://localhost:8000/delivery/1/success \
  -H "Content-Type: application/json" \
  -d '{"external_message_id": "msg_12345"}'
```

**Example Response (200 OK):**
```json
{
  "id": 1,
  "delivery_status": "delivered",
  "external_message_id": "msg_12345",
  "delivered_at": "2024-01-15T10:31:00Z"
}
```

### Mark Delivery Failed

**POST /delivery/{delivery_log_id}/failure**

Mark a delivery as failed.

**Parameters:**
- `error_message` (string, required): Error description
- `status_code` (integer, optional): HTTP status code
- `should_retry` (boolean, optional): Whether to retry (default: true)

**Example Request:**
```bash
curl -X POST http://localhost:8000/delivery/1/failure \
  -H "Content-Type: application/json" \
  -d '{
    "error_message": "Invalid email address",
    "status_code": 400,
    "should_retry": false
  }'
```

**Example Response (200 OK):**
```json
{
  "id": 1,
  "delivery_status": "failed",
  "error_message": "Invalid email address",
  "retry_count": 0
}
```

### Get Delivery Statistics

**GET /notifications/{notification_id}/delivery-stats**

Get delivery statistics for a notification.

**Example Request:**
```bash
curl "http://localhost:8000/notifications/1/delivery-stats"
```

**Example Response (200 OK):**
```json
{
  "total": 3,
  "delivered": 2,
  "failed": 1,
  "pending": 0,
  "success_rate": 66.67
}
```

---

## Batch Notifications

### Create Notification Batch

**POST /batches**

Create a new notification batch.

**Parameters:**
- `batch_name` (string, required): Batch name
- `batch_type` (string, required): campaign, bulk, scheduled, triggered
- `template_id` (integer, required): Template to use
- `target_user_count` (integer, optional): Expected user count
- `created_by` (integer, optional): Creating admin user ID

**Example Request:**
```bash
curl -X POST http://localhost:8000/batches \
  -H "Content-Type: application/json" \
  -d '{
    "batch_name": "spring_promo_2024",
    "batch_type": "campaign",
    "template_id": 1,
    "target_user_count": 50000,
    "created_by": 1
  }'
```

**Example Response (201 Created):**
```json
{
  "id": 1,
  "batch_name": "spring_promo_2024",
  "batch_type": "campaign",
  "template_id": 1,
  "batch_status": "draft",
  "target_user_count": 50000,
  "sent_count": 0,
  "created_at": "2024-01-15T10:30:00Z"
}
```

### Schedule Batch

**POST /batches/{batch_id}/schedule**

Schedule a batch for sending.

**Parameters:**
- `scheduled_time` (datetime, required): When to send (ISO 8601 format)

**Example Request:**
```bash
curl -X POST http://localhost:8000/batches/1/schedule \
  -H "Content-Type: application/json" \
  -d '{"scheduled_time": "2024-01-20T09:00:00Z"}'
```

**Example Response (200 OK):**
```json
{
  "id": 1,
  "batch_status": "scheduled",
  "scheduled_send_time": "2024-01-20T09:00:00Z"
}
```

### Get Batch Statistics

**GET /batches/{batch_id}/stats**

Get statistics for a batch.

**Example Request:**
```bash
curl "http://localhost:8000/batches/1/stats"
```

**Example Response (200 OK):**
```json
{
  "batch_id": 1,
  "batch_name": "spring_promo_2024",
  "batch_status": "completed",
  "target_count": 50000,
  "sent_count": 49875,
  "failed_count": 125,
  "bounce_count": 50,
  "success_rate": 99.75
}
```

---

## Status Codes

| Code | Meaning |
|------|---------|
| 200 | OK - Successful request |
| 201 | Created - Resource created successfully |
| 400 | Bad Request - Invalid parameters |
| 404 | Not Found - Resource not found |
| 500 | Server Error - Internal server error |

## Rate Limiting

Current implementation: No rate limiting (production should add)

Recommended: 1000 requests/minute per API key

## Pagination

All list endpoints support pagination:

```json
{
  "data": [...],
  "total": 100,
  "limit": 20,
  "offset": 0
}
```

Maximum limit: 500 (varies by endpoint)

---

**Last Updated:** 2024-01-15
**API Version:** 3.0.0
