"""Tests for notification delivery provider clients.

Validates AWS SES (email), Twilio (SMS), and FCM (push) integrations.
All provider APIs are mocked — these are unit tests, not integration tests.
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock, AsyncMock

import pytest


def _channel(channel_type: str, value: str, channel_id: int = 1) -> dict:
    return {"id": channel_id, "channel_type": channel_type, "channel_value": value}


def _notification(nid: int = 1, title: str = "Test", message: str = "Hello") -> dict:
    return {"id": nid, "title": title, "message": message, "data_payload": {}}


# ---------------------------------------------------------------------------
# AWS SES Email
# ---------------------------------------------------------------------------

class TestSESEmail:

    @pytest.mark.asyncio
    @patch("boto3.client")
    async def test_sends_email_via_ses(self, mock_boto):
        from src.workers.provider_clients import send_email_ses, _send_counts
        _send_counts.clear()

        mock_ses = MagicMock()
        mock_ses.send_email.return_value = {"MessageId": "ses_msg_001"}
        mock_boto.return_value = mock_ses

        result = await send_email_ses(
            _channel("email", "user@example.com"),
            _notification(1),
        )

        assert result["external_message_id"] == "ses_msg_001"
        assert result["response_metadata"]["provider"] == "ses"
        mock_ses.send_email.assert_called_once()

        # Verify email address was passed to SES
        call_kwargs = mock_ses.send_email.call_args[1]
        assert "user@example.com" in call_kwargs["Destination"]["ToAddresses"]

    @pytest.mark.asyncio
    @patch("boto3.client")
    async def test_rate_limits_excessive_sends(self, mock_boto):
        from src.workers.provider_clients import send_email_ses, _send_counts, _MAX_SENDS_PER_USER_HOUR
        _send_counts.clear()

        mock_ses = MagicMock()
        mock_ses.send_email.return_value = {"MessageId": "ses_msg"}
        mock_boto.return_value = mock_ses

        # Send up to the limit
        for i in range(_MAX_SENDS_PER_USER_HOUR):
            await send_email_ses(_channel("email", "rate@test.com"), _notification(i))

        # Next send should raise
        with pytest.raises(Exception, match="Rate limit"):
            await send_email_ses(_channel("email", "rate@test.com"), _notification(999))

        _send_counts.clear()


# ---------------------------------------------------------------------------
# Twilio SMS
# ---------------------------------------------------------------------------

class TestTwilioSMS:

    @pytest.mark.asyncio
    @patch.dict("os.environ", {
        "TWILIO_ACCOUNT_SID": "AC_test",
        "TWILIO_AUTH_TOKEN": "tok_test",
        "TWILIO_FROM_NUMBER": "+15551234567",
    })
    @patch("twilio.rest.Client")
    async def test_sends_sms_via_twilio(self, mock_twilio_cls):
        from src.workers.provider_clients import send_sms_twilio, _send_counts
        _send_counts.clear()

        mock_message = MagicMock()
        mock_message.sid = "SM_test_001"
        mock_message.status = "queued"
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message
        mock_twilio_cls.return_value = mock_client

        result = await send_sms_twilio(
            _channel("sms", "+15559876543"),
            _notification(1, message="Hello from NILBx"),
        )

        assert result["external_message_id"] == "SM_test_001"
        assert result["response_metadata"]["provider"] == "twilio"

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"TWILIO_ACCOUNT_SID": "", "TWILIO_AUTH_TOKEN": "", "TWILIO_FROM_NUMBER": ""})
    async def test_raises_if_twilio_not_configured(self):
        from src.workers.provider_clients import send_sms_twilio, _send_counts
        _send_counts.clear()

        with pytest.raises(Exception, match="not configured"):
            await send_sms_twilio(
                _channel("sms", "+15559876543"),
                _notification(1),
            )


# ---------------------------------------------------------------------------
# FCM Push
# ---------------------------------------------------------------------------

class TestFCMPush:

    @pytest.mark.asyncio
    async def test_sends_push_via_fcm(self):
        import sys

        # Mock firebase_admin before importing
        mock_firebase = MagicMock()
        mock_firebase._apps = {"default": True}
        mock_messaging = MagicMock()
        mock_messaging.send.return_value = "projects/test/messages/fcm_001"
        mock_firebase.messaging = mock_messaging

        sys.modules["firebase_admin"] = mock_firebase
        sys.modules["firebase_admin.credentials"] = MagicMock()
        sys.modules["firebase_admin.messaging"] = mock_messaging

        # Re-import to pick up mocked firebase
        from importlib import reload
        import src.workers.provider_clients as pc
        reload(pc)
        pc._send_counts.clear()

        # FCM registration tokens in production are ~150-180 chars of URL-safe
        # alphanumerics plus ``:`` ``_`` ``-``. provider_clients._is_plausible_fcm_token
        # enforces a 100-300 char length via _FCM_TOKEN_RE — any shorter value
        # is rejected before send. Use a realistic-shape token so the test
        # exercises the happy path rather than the reject-early guard.
        realistic_fcm_token = (
            "cR8pX-VwT3yKqLmN7bHfAs:APA91bFmJkL4oP6tRz9xYnQwEuBvCdXfHgJkMmNoPqRrSsTtUuVvWwXxYyZz"
            "0123456789abcdef0123456789ABCDEF-_ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        )
        assert 100 <= len(realistic_fcm_token) <= 300, "fixture must match prod validator"

        result = await pc.send_push_fcm(
            _channel("push", realistic_fcm_token),
            _notification(1),
        )

        assert "fcm_001" in result["external_message_id"]
        assert result["response_metadata"]["provider"] == "fcm"

        # Cleanup
        sys.modules.pop("firebase_admin", None)
        sys.modules.pop("firebase_admin.credentials", None)
        sys.modules.pop("firebase_admin.messaging", None)


# ---------------------------------------------------------------------------
# PII Safety
# ---------------------------------------------------------------------------

class TestPIISafety:

    def test_hash_recipient_does_not_reveal_original(self):
        from src.workers.provider_clients import _hash_recipient

        hashed = _hash_recipient("test@example.com")
        assert "test@example.com" not in hashed
        assert len(hashed) == 12  # Truncated SHA256

    def test_same_input_produces_same_hash(self):
        from src.workers.provider_clients import _hash_recipient

        h1 = _hash_recipient("user@nilbx.com")
        h2 = _hash_recipient("user@nilbx.com")
        assert h1 == h2

    def test_different_inputs_produce_different_hashes(self):
        from src.workers.provider_clients import _hash_recipient

        h1 = _hash_recipient("user1@nilbx.com")
        h2 = _hash_recipient("user2@nilbx.com")
        assert h1 != h2
