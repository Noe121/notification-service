-- Stub migration for notification_db (dev, MySQL 8.4). Replace with real schema.

CREATE TABLE IF NOT EXISTS notifications (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL COMMENT 'Reference to auth_db.users',
    type VARCHAR(100) NOT NULL,
    payload JSON,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
