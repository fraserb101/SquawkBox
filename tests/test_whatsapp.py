"""Tests for services/whatsapp.py."""

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app():
    """Create a test app with only the WhatsApp router."""
    # Need to mock config before import
    with patch.dict("os.environ", {
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_KEY": "test-key",
        "NEWSDATA_API_KEY": "test",
        "TAVILY_API_KEY": "test",
        "TOGETHER_API_KEY": "test",
        "CARTESIA_API_KEY": "test",
        "CARTESIA_VOICE_ID": "test",
        "WHATSAPP_PHONE_NUMBER_ID": "123",
        "WHATSAPP_ACCESS_TOKEN": "test-token",
        "WHATSAPP_VERIFY_TOKEN": "test-verify",
        "WHATSAPP_APP_SECRET": "test-secret",
        "STRIPE_SECRET_KEY": "sk_test_xxx",
        "STRIPE_WEBHOOK_SECRET": "whsec_test",
        "STRIPE_PAYMENT_LINK": "https://pay.stripe.com/test",
        "ADMIN_SECRET": "admin-secret",
        "TERMS_URL": "https://example.com/terms",
    }):
        from fastapi import FastAPI
        from services.whatsapp import router, _verify_signature
        app = FastAPI()
        app.include_router(router)
        yield app


@pytest.fixture
def client(app):
    return TestClient(app)


class TestWebhookVerification:
    def test_valid_verification(self, client):
        resp = client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "challenge_token",
                "hub.verify_token": "test-verify",
            },
        )
        assert resp.status_code == 200
        assert resp.text == "challenge_token"

    def test_invalid_verify_token(self, client):
        resp = client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "challenge_token",
                "hub.verify_token": "wrong-token",
            },
        )
        assert resp.status_code == 403


class TestSignatureVerification:
    def test_valid_signature(self):
        from services.whatsapp import _verify_signature
        body = b'{"test": true}'
        secret = "test-secret"
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert _verify_signature(body, sig) is True

    def test_invalid_signature(self):
        from services.whatsapp import _verify_signature
        assert _verify_signature(b"body", "sha256=invalid") is False

    def test_empty_signature(self):
        from services.whatsapp import _verify_signature
        assert _verify_signature(b"body", "") is False


class TestSendTextMessage:
    @patch("services.whatsapp.httpx.Client")
    def test_sends_message(self, mock_client_cls):
        from services.whatsapp import send_text_message
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"messages": [{"id": "msg-1"}]}
        mock_client.post.return_value = mock_resp

        result = send_text_message("+123", "Hello")
        assert result["messages"][0]["id"] == "msg-1"
