-- Notification Service Migration: Add Contract Notification Templates
-- Migration: 002_add_contract_templates.sql
-- Purpose: Add notification templates for contract signing lifecycle events

USE notification_db;

-- Insert contract signing notification templates
INSERT INTO notification_templates (
    template_name,
    template_type,
    subject,
    content,
    priority,
    is_active,
    variables,
    retry_policy
) VALUES
-- Email: Signing Request
(
    'contract_signing_requested',
    'email',
    'Signature Required: {{contract_name}}',
    'Hello {{recipient_name}},\n\nYou have been invited to sign the following contract:\n\n**Contract:** {{contract_name}}\n**From:** {{sender_name}}\n\nPlease click the link below to review and sign the document:\n{{signing_url}}\n\nThis signing request will expire on {{expiration_date}}.\n\nIf you have any questions, please contact the sender directly.\n\nBest regards,\nNILbx Team',
    'high',
    TRUE,
    '["recipient_name", "contract_name", "sender_name", "signing_url", "expiration_date"]',
    '{"max_retries": 3, "retry_delay_seconds": 300}'
),

-- Email: Signing Completed (to all parties)
(
    'contract_signing_completed',
    'email',
    'Contract Signed: {{contract_name}}',
    'Hello {{recipient_name}},\n\nGreat news! The contract "{{contract_name}}" has been fully executed.\n\nAll parties have signed the document. You can download the signed copy from your dashboard or use the link below:\n\n{{pdf_download_url}}\n\nSigning Details:\n- Completed on: {{completion_date}}\n- Participants: {{participant_count}}\n\nThank you for using NILbx!\n\nBest regards,\nNILbx Team',
    'high',
    TRUE,
    '["recipient_name", "contract_name", "pdf_download_url", "completion_date", "participant_count"]',
    '{"max_retries": 3, "retry_delay_seconds": 300}'
),

-- Email: Signing Reminder
(
    'contract_signing_reminder',
    'email',
    'Reminder: Signature Required for {{contract_name}}',
    'Hello {{recipient_name}},\n\nThis is a friendly reminder that your signature is still needed on the contract "{{contract_name}}".\n\n**Status:** {{signing_status}}\n**Expires:** {{expiration_date}}\n\nPlease click here to sign: {{signing_url}}\n\nIf you have already signed, please disregard this message.\n\nBest regards,\nNILbx Team',
    'normal',
    TRUE,
    '["recipient_name", "contract_name", "signing_status", "expiration_date", "signing_url"]',
    '{"max_retries": 2, "retry_delay_seconds": 600}'
),

-- Email: Signing Canceled
(
    'contract_signing_canceled',
    'email',
    'Contract Canceled: {{contract_name}}',
    'Hello {{recipient_name}},\n\nThe contract "{{contract_name}}" has been canceled.\n\n**Reason:** {{cancellation_reason}}\n**Canceled by:** {{canceled_by}}\n**Date:** {{cancellation_date}}\n\nNo further action is required from you.\n\nIf you have questions about this cancellation, please contact the sender.\n\nBest regards,\nNILbx Team',
    'normal',
    TRUE,
    '["recipient_name", "contract_name", "cancellation_reason", "canceled_by", "cancellation_date"]',
    '{"max_retries": 2, "retry_delay_seconds": 300}'
),

-- Email: Signing Failed
(
    'contract_signing_failed',
    'email',
    'Contract Signing Issue: {{contract_name}}',
    'Hello {{recipient_name}},\n\nUnfortunately, there was an issue with the contract "{{contract_name}}".\n\n**Issue:** {{error_message}}\n**Date:** {{failure_date}}\n\nPlease contact support if you need assistance or would like to initiate a new signing request.\n\nBest regards,\nNILbx Team',
    'high',
    TRUE,
    '["recipient_name", "contract_name", "error_message", "failure_date"]',
    '{"max_retries": 3, "retry_delay_seconds": 300}'
),

-- Push: Signing Request
(
    'contract_signing_requested_push',
    'push',
    NULL,
    'You have a new contract to sign: {{contract_name}}',
    'high',
    TRUE,
    '["contract_name"]',
    '{"max_retries": 2, "retry_delay_seconds": 60}'
),

-- Push: Signing Completed
(
    'contract_signing_completed_push',
    'push',
    NULL,
    'Contract "{{contract_name}}" has been fully signed!',
    'normal',
    TRUE,
    '["contract_name"]',
    '{"max_retries": 2, "retry_delay_seconds": 60}'
),

-- In-App: Signing Request
(
    'contract_signing_requested_in_app',
    'in_app',
    'Signature Required',
    'You have been invited to sign "{{contract_name}}". Tap to review and sign.',
    'high',
    TRUE,
    '["contract_name"]',
    NULL
),

-- In-App: Signing Completed
(
    'contract_signing_completed_in_app',
    'in_app',
    'Contract Signed',
    'The contract "{{contract_name}}" has been fully executed. Download your copy now.',
    'normal',
    TRUE,
    '["contract_name"]',
    NULL
),

-- In-App: Partially Signed
(
    'contract_signing_partial_in_app',
    'in_app',
    'Waiting for Signatures',
    '{{signed_count}} of {{total_count}} parties have signed "{{contract_name}}".',
    'low',
    TRUE,
    '["contract_name", "signed_count", "total_count"]',
    NULL
),

-- SMS: Signing Request (for urgent signings)
(
    'contract_signing_requested_sms',
    'sms',
    NULL,
    'NILbx: You have a contract to sign. Check your email for "{{contract_name}}" or visit {{short_url}}',
    'high',
    TRUE,
    '["contract_name", "short_url"]',
    '{"max_retries": 1, "retry_delay_seconds": 300}'
);

-- Verification
SELECT 'Contract notification templates added successfully' as status;
SELECT template_name, template_type, priority FROM notification_templates WHERE template_name LIKE 'contract_%';
