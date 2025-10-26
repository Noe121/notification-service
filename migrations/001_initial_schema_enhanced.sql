-- NILBX Notification Service Database Schema
-- Database: notification_db (Port 3323) - LOCAL DEVELOPMENT ONLY
-- Cloud/Production: Uses nilbx_db (shared database)
-- Purpose: Multi-channel notification management with delivery tracking

-- Enable foreign key checks
SET FOREIGN_KEY_CHECKS = 1;

-- Create the database if it doesn't exist
CREATE DATABASE IF NOT EXISTS notification_db CHARACTER SET utf8mb4 COLLATE=utf8mb4_unicode_ci;
USE notification_db;

-- Create user with access from any host (for Docker containers)
CREATE USER IF NOT EXISTS 'notificationuser'@'%' IDENTIFIED BY 'notificationpass';
GRANT ALL PRIVILEGES ON notification_db.* TO 'notificationuser'@'%';
FLUSH PRIVILEGES;

-- ====================================
-- Core Tables from ENHANCED Schema
-- (Minimal reference tables for development)
-- ====================================

CREATE TABLE IF NOT EXISTS users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL,
    role ENUM('athlete', 'sponsor', 'fan', 'admin') NOT NULL DEFAULT 'fan',
    is_active BOOLEAN DEFAULT TRUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_email (email),
    INDEX idx_is_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='User reference table - synced from auth_db in production';

-- ====================================
-- Notification Templates
-- ====================================
CREATE TABLE IF NOT EXISTS notification_templates (
    id INT AUTO_INCREMENT PRIMARY KEY,
    template_name VARCHAR(100) NOT NULL UNIQUE,
    template_type VARCHAR(50) NOT NULL,
    subject VARCHAR(255),
    content TEXT NOT NULL,
    variables JSON COMMENT 'Array of variable names',
    priority VARCHAR(20) DEFAULT 'normal',
    retry_policy JSON COMMENT 'Retry configuration',
    is_active BOOLEAN DEFAULT TRUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    is_deleted BOOLEAN DEFAULT FALSE,
    deleted_at DATETIME,

    INDEX idx_type_active (template_type, is_active),
    INDEX idx_template_name (template_name),
    INDEX idx_is_deleted (is_deleted)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Notification message templates';

-- ====================================
-- User Notification Preferences
-- ====================================
CREATE TABLE IF NOT EXISTS user_notification_preferences (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL UNIQUE,
    email_enabled BOOLEAN DEFAULT TRUE,
    sms_enabled BOOLEAN DEFAULT TRUE,
    push_enabled BOOLEAN DEFAULT TRUE,
    in_app_enabled BOOLEAN DEFAULT TRUE,
    email_frequency VARCHAR(50) DEFAULT 'immediate',
    sms_frequency VARCHAR(50) DEFAULT 'immediate',
    push_frequency VARCHAR(50) DEFAULT 'immediate',
    quiet_hours_start VARCHAR(5) COMMENT 'HH:MM format',
    quiet_hours_end VARCHAR(5) COMMENT 'HH:MM format',
    timezone VARCHAR(50) DEFAULT 'UTC',
    notification_categories JSON COMMENT 'Category preferences',
    do_not_disturb_enabled BOOLEAN DEFAULT FALSE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    is_deleted BOOLEAN DEFAULT FALSE,
    deleted_at DATETIME,

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,

    INDEX idx_user_dnd (user_id, do_not_disturb_enabled),
    INDEX idx_is_deleted (is_deleted)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='User notification preferences and settings';

-- ====================================
-- Notifications
-- ====================================
CREATE TABLE IF NOT EXISTS notifications (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    template_id INT NOT NULL,
    notification_type VARCHAR(50) NOT NULL,
    title VARCHAR(255),
    message TEXT NOT NULL,
    data_payload JSON COMMENT 'Additional data',
    priority VARCHAR(20) DEFAULT 'normal',
    is_read BOOLEAN DEFAULT FALSE,
    read_at DATETIME,
    expires_at DATETIME,
    source_system VARCHAR(100) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    is_deleted BOOLEAN DEFAULT FALSE,
    deleted_at DATETIME,

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (template_id) REFERENCES notification_templates(id),

    INDEX idx_user_read (user_id, is_read),
    INDEX idx_user_created (user_id, created_at),
    INDEX idx_type_created (notification_type, created_at),
    INDEX idx_user_priority (user_id, priority),
    INDEX idx_is_deleted (is_deleted)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Individual notifications';

-- ====================================
-- Notification Channels
-- ====================================
CREATE TABLE IF NOT EXISTS notification_channels (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    channel_type VARCHAR(50) NOT NULL,
    channel_value VARCHAR(500) NOT NULL,
    is_verified BOOLEAN DEFAULT FALSE,
    is_primary BOOLEAN DEFAULT FALSE,
    verification_token VARCHAR(500),
    verification_attempts INT DEFAULT 0,
    verified_at DATETIME,
    is_active BOOLEAN DEFAULT TRUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    is_deleted BOOLEAN DEFAULT FALSE,
    deleted_at DATETIME,

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,

    INDEX idx_user_type (user_id, channel_type),
    INDEX idx_verified (is_verified, is_active),
    INDEX idx_channel_value (channel_value),
    INDEX idx_is_deleted (is_deleted)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='User notification delivery channels';

-- ====================================
-- Delivery Logs
-- ====================================
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
    response_metadata JSON COMMENT 'Metadata from provider',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    is_deleted BOOLEAN DEFAULT FALSE,
    deleted_at DATETIME,

    FOREIGN KEY (notification_id) REFERENCES notifications(id) ON DELETE CASCADE,
    FOREIGN KEY (channel_id) REFERENCES notification_channels(id) ON DELETE CASCADE,

    INDEX idx_notification_status (notification_id, delivery_status),
    INDEX idx_channel_status (delivery_channel, delivery_status),
    INDEX idx_retry_time (next_retry_at, retry_count),
    INDEX idx_is_deleted (is_deleted)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Notification delivery tracking';

-- ====================================
-- Notification Batches
-- ====================================
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
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    is_deleted BOOLEAN DEFAULT FALSE,
    deleted_at DATETIME,

    FOREIGN KEY (template_id) REFERENCES notification_templates(id),

    INDEX idx_status (batch_status, created_at),
    INDEX idx_scheduled (scheduled_send_time),
    INDEX idx_is_deleted (is_deleted)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Batch notification campaigns';

-- ====================================
-- Sample Data
-- ====================================

-- Sample users
INSERT IGNORE INTO users (id, email, name, role) VALUES
(1, 'athlete@example.com', 'John Athlete', 'athlete'),
(2, 'sponsor@example.com', 'Nike Representative', 'sponsor'),
(3, 'fan@example.com', 'Sports Fan', 'fan');

-- Sample notification templates
INSERT INTO notification_templates (template_name, template_type, subject, content, priority, is_active, variables)
VALUES
    ('welcome_email', 'email', 'Welcome to NILbx!',
     'Welcome {{user_name}}! We''re excited to have you on board.',
     'normal', TRUE, '["user_name"]'),
    ('deal_created', 'email', 'New NIL Deal Available',
     'A new deal has been created: {{deal_name}} for ${{amount}}',
     'high', TRUE, '["deal_name", "amount"]'),
    ('payment_confirmation', 'email', 'Payment Confirmed',
     'Your payment of ${{amount}} has been confirmed.',
     'high', TRUE, '["amount", "payment_id"]'),
    ('deal_reminder', 'push', NULL,
     'Don''t forget about your deal: {{deal_name}}',
     'normal', TRUE, '["deal_name"]'),
    ('account_alert', 'sms', NULL,
     'Security Alert: New login from {{device}}',
     'high', TRUE, '["device", "location"]');

-- ====================================
-- Database Verification
-- ====================================
SHOW TABLES;
SELECT 'Notification Service Database Created - ENHANCED Schema Compatible' as status;
