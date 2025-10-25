-- Notification Service Database Schema
-- Multi-channel notification management with delivery tracking

-- ============================================================================
-- Notification Templates
-- ============================================================================

CREATE TABLE IF NOT EXISTS notification_templates (
    id INT AUTO_INCREMENT PRIMARY KEY,
    template_name VARCHAR(100) NOT NULL UNIQUE,
    template_type VARCHAR(50) NOT NULL,
    subject VARCHAR(255),
    content TEXT NOT NULL,
    variables TEXT COMMENT 'JSON array of variable names',
    priority VARCHAR(20) DEFAULT 'normal',
    retry_policy TEXT COMMENT 'JSON retry configuration',
    is_active INT DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP NOT NULL,
    is_deleted INT DEFAULT 0,
    deleted_at DATETIME,
    INDEX idx_notification_templates_type_active (template_type, is_active),
    INDEX idx_notification_templates_name (template_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- User Notification Preferences
-- ============================================================================

CREATE TABLE IF NOT EXISTS user_notification_preferences (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL UNIQUE,
    email_enabled INT DEFAULT 1,
    sms_enabled INT DEFAULT 1,
    push_enabled INT DEFAULT 1,
    in_app_enabled INT DEFAULT 1,
    email_frequency VARCHAR(50) DEFAULT 'immediate',
    sms_frequency VARCHAR(50) DEFAULT 'immediate',
    push_frequency VARCHAR(50) DEFAULT 'immediate',
    quiet_hours_start VARCHAR(5) COMMENT 'HH:MM format',
    quiet_hours_end VARCHAR(5) COMMENT 'HH:MM format',
    timezone VARCHAR(50) DEFAULT 'UTC',
    notification_categories TEXT COMMENT 'JSON array of category preferences',
    do_not_disturb_enabled INT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP NOT NULL,
    is_deleted INT DEFAULT 0,
    deleted_at DATETIME,
    INDEX idx_user_notification_prefs_user_dnd (user_id, do_not_disturb_enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- Notifications
-- ============================================================================

CREATE TABLE IF NOT EXISTS notifications (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    template_id INT NOT NULL,
    notification_type VARCHAR(50) NOT NULL,
    title VARCHAR(255),
    message TEXT NOT NULL,
    data_payload TEXT COMMENT 'JSON additional data',
    priority VARCHAR(20) DEFAULT 'normal',
    is_read INT DEFAULT 0,
    read_at DATETIME,
    expires_at DATETIME,
    source_system VARCHAR(100) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP NOT NULL,
    is_deleted INT DEFAULT 0,
    deleted_at DATETIME,
    FOREIGN KEY (template_id) REFERENCES notification_templates(id),
    INDEX idx_notifications_user_read (user_id, is_read),
    INDEX idx_notifications_user_created (user_id, created_at),
    INDEX idx_notifications_type_created (notification_type, created_at),
    INDEX idx_notifications_user_priority (user_id, priority)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- Notification Channels
-- ============================================================================

CREATE TABLE IF NOT EXISTS notification_channels (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    channel_type VARCHAR(50) NOT NULL,
    channel_value VARCHAR(500) NOT NULL,
    is_verified INT DEFAULT 0,
    is_primary INT DEFAULT 0,
    verification_token VARCHAR(500),
    verification_attempts INT DEFAULT 0,
    verified_at DATETIME,
    is_active INT DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP NOT NULL,
    is_deleted INT DEFAULT 0,
    deleted_at DATETIME,
    INDEX idx_notification_channels_user_type (user_id, channel_type),
    INDEX idx_notification_channels_verified (is_verified, is_active),
    INDEX idx_notification_channels_value (channel_value)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- Delivery Logs
-- ============================================================================

CREATE TABLE IF NOT EXISTS delivery_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    notification_id INT NOT NULL,
    channel_id INT NOT NULL,
    delivery_channel VARCHAR(50) NOT NULL,
    delivery_status VARCHAR(50) NOT NULL,
    status_code INT,
    error_message TEXT,
    retry_count INT DEFAULT 0,
    max_retries INT DEFAULT 3,
    next_retry_at DATETIME,
    delivered_at DATETIME,
    external_message_id VARCHAR(500),
    response_metadata TEXT COMMENT 'JSON metadata from provider',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP NOT NULL,
    is_deleted INT DEFAULT 0,
    deleted_at DATETIME,
    FOREIGN KEY (notification_id) REFERENCES notifications(id),
    FOREIGN KEY (channel_id) REFERENCES notification_channels(id),
    INDEX idx_delivery_logs_notification_status (notification_id, delivery_status),
    INDEX idx_delivery_logs_channel_status (delivery_channel, delivery_status),
    INDEX idx_delivery_logs_retry_time (next_retry_at, retry_count)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- Notification Batches
-- ============================================================================

CREATE TABLE IF NOT EXISTS notification_batches (
    id INT AUTO_INCREMENT PRIMARY KEY,
    batch_name VARCHAR(255) NOT NULL,
    batch_type VARCHAR(50) NOT NULL,
    template_id INT NOT NULL,
    target_user_count INT,
    scheduled_send_time DATETIME,
    batch_status VARCHAR(50) NOT NULL,
    sent_count INT DEFAULT 0,
    failed_count INT DEFAULT 0,
    bounce_count INT DEFAULT 0,
    success_rate FLOAT,
    started_at DATETIME,
    completed_at DATETIME,
    created_by INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP NOT NULL,
    is_deleted INT DEFAULT 0,
    deleted_at DATETIME,
    FOREIGN KEY (template_id) REFERENCES notification_templates(id),
    INDEX idx_notification_batches_status (batch_status, created_at),
    INDEX idx_notification_batches_scheduled (scheduled_send_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================================
-- Sample Data
-- ============================================================================

INSERT INTO notification_templates (template_name, template_type, subject, content, priority, is_active)
VALUES
    ('welcome_email', 'email', 'Welcome to Our Platform!', 'Welcome {{user_name}}! We''re excited to have you on board.', 'normal', 1),
    ('order_confirmation', 'email', 'Order {{order_id}} Confirmed', 'Your order has been confirmed and will be shipped soon.', 'high', 1),
    ('payment_reminder', 'email', 'Payment Reminder', 'This is a friendly reminder about your upcoming payment.', 'normal', 1),
    ('account_alert', 'sms', NULL, 'Security Alert: New login from {{device}}', 'high', 1),
    ('promotional_push', 'push', NULL, 'Special offer just for you!', 'normal', 1);
