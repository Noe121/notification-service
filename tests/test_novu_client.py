import pytest

from src import novu_client


class _FakeResponse:
    def __init__(self, payload=None):
        self._payload = payload or {"acknowledged": True}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeAsyncClient:
    call_count = 0
    last_url = None
    last_json = None
    last_headers = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, json, headers):
        _FakeAsyncClient.call_count += 1
        _FakeAsyncClient.last_url = url
        _FakeAsyncClient.last_json = json
        _FakeAsyncClient.last_headers = headers
        return _FakeResponse()


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    novu_client.reset_welcome_idempotency_cache()
    novu_client.reset_email_verification_idempotency_cache()
    _FakeAsyncClient.call_count = 0
    _FakeAsyncClient.last_url = None
    _FakeAsyncClient.last_json = None
    _FakeAsyncClient.last_headers = None
    monkeypatch.setattr(novu_client, "NOVU_SECRET_KEY", "test-secret")
    monkeypatch.setattr(novu_client, "NOVU_API_URL", "https://api.novu.co/v1")
    monkeypatch.setattr(novu_client, "EMAIL_VERIFICATION_WORKFLOW_ID", "email-verification")
    monkeypatch.setattr(novu_client.httpx, "AsyncClient", _FakeAsyncClient)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "persona_type,persona_label,workflow_id",
    [
        ("creator", "student_athlete", "welcome-creator"),
        ("business", "brand", "welcome-business"),
        ("admin", "school_admin", "welcome-admin"),
    ],
)
async def test_trigger_welcome_workflow_routes_expected_workflow(persona_type, persona_label, workflow_id):
    result = await novu_client.trigger_welcome_workflow(
        user_id="user-1",
        email="test@example.com",
        first_name="Test",
        persona_type=persona_type,
        persona_label=persona_label,
    )

    assert result == {"acknowledged": True}
    assert _FakeAsyncClient.call_count == 1
    assert _FakeAsyncClient.last_url == "https://api.novu.co/v1/events/trigger"
    assert _FakeAsyncClient.last_json["name"] == workflow_id
    assert _FakeAsyncClient.last_json["payload"]["personaType"] == persona_type
    assert _FakeAsyncClient.last_json["payload"]["personaLabel"] == persona_label


@pytest.mark.asyncio
async def test_trigger_welcome_workflow_rejects_invalid_persona_label():
    with pytest.raises(ValueError, match="Invalid persona_label"):
        await novu_client.trigger_welcome_workflow(
            user_id="user-1",
            email="test@example.com",
            first_name="Test",
            persona_type="creator",
            persona_label="brand",
        )

    assert _FakeAsyncClient.call_count == 0


@pytest.mark.asyncio
async def test_trigger_welcome_workflow_rejects_unknown_persona_type():
    with pytest.raises(ValueError, match="Unknown persona_type"):
        await novu_client.trigger_welcome_workflow(
            user_id="user-1",
            email="test@example.com",
            first_name="Test",
            persona_type="unknown",  # type: ignore[arg-type]
            persona_label="brand",
        )

    assert _FakeAsyncClient.call_count == 0


@pytest.mark.asyncio
async def test_trigger_welcome_workflow_skips_duplicate_idempotency_key():
    first = await novu_client.trigger_welcome_workflow(
        user_id="user-1",
        email="test@example.com",
        first_name="Test",
        persona_type="business",
        persona_label="brand",
        idempotency_key="welcome:user-1:business",
    )
    second = await novu_client.trigger_welcome_workflow(
        user_id="user-1",
        email="test@example.com",
        first_name="Test",
        persona_type="business",
        persona_label="brand",
        idempotency_key="welcome:user-1:business",
    )

    assert first == {"acknowledged": True}
    assert second["status"] == "skipped"
    assert second["reason"] == "duplicate"
    assert second["idempotency_key"] == "welcome:user-1:business"
    assert _FakeAsyncClient.call_count == 1


@pytest.mark.asyncio
async def test_trigger_email_verification_otp_routes_expected_workflow():
    result = await novu_client.trigger_email_verification(
        user_id="user-2",
        email="verify@example.com",
        first_name="Verify",
        verification_method="otp",
        verification_code="123456",
        expires_minutes=15,
    )

    assert result == {"acknowledged": True}
    assert _FakeAsyncClient.call_count == 1
    assert _FakeAsyncClient.last_json["name"] == "email-verification"
    assert _FakeAsyncClient.last_json["payload"]["verificationMethod"] == "otp"
    assert _FakeAsyncClient.last_json["payload"]["verificationCode"] == "123456"
    assert _FakeAsyncClient.last_json["payload"]["magicLink"] == ""
    assert _FakeAsyncClient.last_json["payload"]["expiresMinutes"] == 15


@pytest.mark.asyncio
async def test_trigger_email_verification_magic_link_routes_expected_workflow():
    result = await novu_client.trigger_email_verification(
        user_id="user-3",
        email="verify@example.com",
        verification_method="magic_link",
        magic_link="https://nilbx.com/verify?token=abc",
    )

    assert result == {"acknowledged": True}
    assert _FakeAsyncClient.call_count == 1
    assert _FakeAsyncClient.last_json["name"] == "email-verification"
    assert _FakeAsyncClient.last_json["payload"]["verificationMethod"] == "magic_link"
    assert _FakeAsyncClient.last_json["payload"]["magicLink"] == "https://nilbx.com/verify?token=abc"


@pytest.mark.asyncio
async def test_trigger_email_verification_rejects_invalid_payload_combination():
    with pytest.raises(ValueError, match="verification_code is required"):
        await novu_client.trigger_email_verification(
            user_id="user-2",
            email="verify@example.com",
            verification_method="otp",
        )

    assert _FakeAsyncClient.call_count == 0


@pytest.mark.asyncio
async def test_trigger_email_verification_skips_duplicate_idempotency_key():
    first = await novu_client.trigger_email_verification(
        user_id="user-4",
        email="verify@example.com",
        verification_method="otp",
        verification_code="654321",
        idempotency_key="verify:user-4:otp",
    )
    second = await novu_client.trigger_email_verification(
        user_id="user-4",
        email="verify@example.com",
        verification_method="otp",
        verification_code="654321",
        idempotency_key="verify:user-4:otp",
    )

    assert first == {"acknowledged": True}
    assert second["status"] == "skipped"
    assert second["reason"] == "duplicate"
    assert second["idempotency_key"] == "verify:user-4:otp"
    assert _FakeAsyncClient.call_count == 1
