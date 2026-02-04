# Notification Service

Multi-channel notification delivery and template management service for the NILbx platform.

## Overview

The Notification Service provides comprehensive notification management including:
- **Template Management**: Reusable notification templates with variable substitution
- **User Preferences**: Per-user channel settings (email, SMS, push, in-app)
- **Multi-Channel Delivery**: Support for email, SMS, push notifications, and in-app alerts
- **Delivery Tracking**: Real-time status monitoring and delivery confirmation
- **Batch Processing**: Efficient bulk notification dispatch
- **Quiet Hours**: Respect user do-not-disturb preferences

## Database

**Consolidated Database**: `notifications_db`
Shared with other notification-related services using `notificationuser` credentials.

### Schema Location
Active migrations: `NILbx-env/modules/db/mysql/migrations/notifications_db/`

## Technology Stack

- **Framework**: FastAPI 0.104.1
- **Database**: MySQL 8.4 via SQLAlchemy 2.0.23
- **Python**: 3.11
- **Deployment**: AWS ECS Fargate (Docker)
- **Database Driver**: PyMySQL 1.1.0

## Local Development

### Prerequisites
- Python 3.11+
- MySQL 8.4 (or use AWS RDS dev instance)
- AWS credentials configured (for Secrets Manager)

### Setup

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env

# Edit .env with your configuration
# DB_SECRET_NAME=dev-notifications-db-credentials
# DATABASE_URL=mysql+pymysql://notificationuser:password@host/notifications_db
```

### Run Locally

```bash
# Start the service
uvicorn src.main:app --host 0.0.0.0 --port 8013 --reload

# Health check
curl http://localhost:8013/health
```

## Docker

### Build

```bash
docker build -t notification-service:latest .
```

### Run

```bash
docker run -p 8013:8013 \
  -e DB_SECRET_NAME=dev-notifications-db-credentials \
  -e AWS_REGION=us-east-1 \
  notification-service:latest
```

## API Endpoints

### Health
- `GET /health` - Service health check

### Templates
- `POST /templates` - Create notification template
- `GET /templates` - List all templates
- `GET /templates/{template_id}` - Get template by ID
- `PUT /templates/{template_id}` - Update template
- `DELETE /templates/{template_id}` - Delete template (soft delete)

### Notifications
- `POST /notifications` - Send single notification
- `POST /notifications/batch` - Send batch notifications
- `GET /notifications/user/{user_id}` - Get user notifications
- `PUT /notifications/{notification_id}/read` - Mark as read

### User Preferences
- `GET /preferences/{user_id}` - Get user preferences
- `PUT /preferences/{user_id}` - Update user preferences
- `POST /preferences/{user_id}/channels/{channel}/toggle` - Toggle channel on/off

### Delivery Tracking
- `GET /delivery/{notification_id}` - Get delivery status
- `GET /delivery/batch/{batch_id}` - Get batch delivery status

See [NOTIFICATION_SERVICE_API_REFERENCE.md](NOTIFICATION_SERVICE_API_REFERENCE.md) for complete API documentation.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DB_SECRET_NAME` | AWS Secrets Manager secret name | `dev-notifications-db-credentials` |
| `DATABASE_URL` | Direct database URL (overrides Secrets Manager) | - |
| `DB_HOST` | Database hostname | From Secrets Manager |
| `DB_PORT` | Database port | `3306` |
| `DB_NAME` | Database name | `notifications_db` |
| `DB_USERNAME` | Database user | From Secrets Manager |
| `AWS_REGION` | AWS region for Secrets Manager | `us-east-1` |

## Database Schema

### Tables
- `notification_templates` - Reusable templates with variables
- `user_notification_preferences` - Per-user channel preferences
- `notification_channels` - Available delivery channels
- `notification_queue` - Pending notifications to deliver
- `notification_delivery_log` - Delivery status and history
- `notification_batches` - Batch processing metadata

### Indexes
- User ID lookups optimized
- Template type filtering indexed
- Delivery status tracking indexed
- Timestamp-based queries optimized

## Testing

```bash
# Run tests (requires PYTHONPATH)
cd notification-service
PYTHONPATH=. pytest

# With coverage
PYTHONPATH=. pytest --cov=src --cov-report=html

# View coverage report
open htmlcov/index.html
```

**Known Issues:**
- Test `test_notifications.py::test_soft_delete_notification` (line 771) expects soft-deleted items to remain in query results, but current implementation filters them out. 26 other tests pass.

## Deployment

### AWS ECS Service
- **Service**: `dev-notification-service`
- **Cluster**: `dev-nilbx-cluster`
- **Port**: 8013
- **Health Check**: `/health`

### Build & Deploy

```bash
# From project root
cd /Users/nicolasvalladares/NIL

# Build for ECS (linux/amd64)
docker buildx build --platform linux/amd64 \
  -t 193884054235.dkr.ecr.us-east-1.amazonaws.com/notification-service:latest \
  -f notification-service/Dockerfile \
  notification-service/

# Push to ECR
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin 193884054235.dkr.ecr.us-east-1.amazonaws.com
docker push 193884054235.dkr.ecr.us-east-1.amazonaws.com/notification-service:latest

# Force new deployment
aws ecs update-service \
  --cluster dev-nilbx-cluster \
  --service dev-notification-service \
  --force-new-deployment \
  --region us-east-1
```

## Architecture Notes

### Migration from Per-Service to Consolidated Database
This service was migrated from individual `notification_service_db` to the consolidated `notifications_db` as part of the 6-database architecture:

- **Old**: 12 per-service databases
- **New**: 6 workload-optimized databases
- **Benefits**: Reduced cost, simplified management, improved query performance
- **Migration**: Legacy migrations archived in `migrations/archive/`

### Integration Points
- **Auth Service**: User authentication via bearer tokens
- **Payment Service**: Notification of payment status
- **Contract Service**: Deal status updates
- **Compliance Service**: Compliance alert notifications

## Troubleshooting

### Database Connection Issues
```bash
# Check Secrets Manager
aws secretsmanager get-secret-value \
  --secret-id dev-notifications-db-credentials \
  --region us-east-1

# Verify database is running
aws rds describe-db-instances \
  --db-instance-identifier dev-notifications-db \
  --region us-east-1 \
  --query 'DBInstances[0].DBInstanceStatus'
```

### Service Logs
```bash
# Tail logs from ECS
aws logs tail /ecs/dev-notification-service \
  --follow \
  --region us-east-1
```

## Contributing

1. Create feature branch: `git checkout -b feature/notification-enhancement`
2. Make changes and test locally
3. Update tests and documentation
4. Submit pull request

## License

Proprietary - NILbx Platform
