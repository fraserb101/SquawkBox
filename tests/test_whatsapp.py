"""Tests for services/whatsapp.py (Twilio adapter)."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app():
    """Create a test app with only the WhatsApp router."""
    with patch.dict("os.environ", {
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_KEY": "test-key",
        "NEWSDATA_API_KEY": "test",
        "TAVILY_API_KEY": "test",
        "TOGETHER_API_KEY": "test",
        "CARTESIA_API_KEY": "test",
        "CARTESIA_VOICE_ID": "test",
        "TWILIO_ACCOUNT_SID": "ACtest",
        "TWILIO_AUTH_TOKEN": "test-token",
        "TWILIO_WHATSAPP_FROM": "+14155238886",
        "PUBLIC_BASE_URL": "https://test.example.com",
        "STRIPE_SECRET_KEY": "sk_test_xxx",
        "STRIPE_WEBHOOK_SECRET": "whsec_test",
        "STRIPE_PAYMENT_LINK": "https://pay.stripe.com/test",
        "ADMIN_SECRET": "admin-secret",
        "TERMS_URL": "https://example.com/terms",
    }):
        from fastapi import FastAPI

        from services.whatsapp import router
        app = FastAPI()
        app.include_router(router)
        yield app


@pytest.fixture
def client(app):
    return TestClient(app)


def _sign(url: str, params: dict) -> str:
    """Compute a valid X-Twilio-Signature for the given URL + params."""
    from twilio.request_validator import RequestValidator
    return RequestValidator("test-token").compute_signature(url, params)


class TestVerifySignature:
    def test_valid_signature(self):
        from services.whatsapp import _verify_signature
        url = "https://test.example.com/webhook"
        params = {"From": "whatsapp:+15551234567", "Body": "hi"}
        sig = _sign(url, params)
        assert _verify_signature(url, params, sig) is True

    def test_invalid_signature(self):
        from services.whatsapp import _verify_signature
        url = "https://test.example.com/webhook"
        params = {"From": "whatsapp:+15551234567", "Body": "hi"}
        assert _verify_signature(url, params, "bogus") is False

    def test_empty_signature(self):
        from services.whatsapp import _verify_signature
        assert _verify_signature("https://any", {"Body": "hi"}, "") is False

    def test_tampered_params_rejected(self):
        from services.whatsapp import _verify_signature
        url = "https://test.example.com/webhook"
        params = {"From": "whatsapp:+15551234567", "Body": "hi"}
        sig = _sign(url, params)
        # Tamper with the body after signing
        params["Body"] = "hello world"
        assert _verify_signature(url, params, sig) is False


class TestExtractSender:
    def test_strips_whatsapp_prefix(self):
        from services.whatsapp import _extract_sender
        assert _extract_sender({"From": "whatsapp:+15551234567"}) == "+15551234567"

    def test_empty_form(self):
        from services.whatsapp import _extract_sender
        assert _extract_sender({}) == ""

    def test_missing_from(self):
        from services.whatsapp import _extract_sender
        assert _extract_sender({"Body": "hi"}) == ""


class TestCheckRateLimit:
    def test_allows_under_limit(self):
        from services.whatsapp import _check_rate_limit
        with patch("services.whatsapp.get_redis") as mock_get:
            mock_r = MagicMock()
            mock_get.return_value = mock_r
            mock_r.pipeline.return_value.execute.side_effect = [
                [0, 2],  # zremrangebyscore, zcard
                [1, True],  # zadd, expire
            ]
            assert _check_rate_limit("+123") is True

    def test_denies_at_limit(self):
        from services.whatsapp import _check_rate_limit
        with patch("services.whatsapp.get_redis") as mock_get:
            mock_r = MagicMock()
            mock_get.return_value = mock_r
            mock_r.pipeline.return_value.execute.return_value = [0, 5]
            assert _check_rate_limit("+123") is False

    def test_denies_over_limit(self):
        from services.whatsapp import _check_rate_limit
        with patch("services.whatsapp.get_redis") as mock_get:
            mock_r = MagicMock()
            mock_get.return_value = mock_r
            mock_r.pipeline.return_value.execute.return_value = [0, 10]
            assert _check_rate_limit("+123") is False

    def test_fails_open_on_redis_error(self):
        from services.whatsapp import _check_rate_limit
        with patch("services.whatsapp.get_redis") as mock_get:
            mock_get.side_effect = Exception("Redis down")
            assert _check_rate_limit("+123") is True


class TestSendTextMessage:
    @patch("services.whatsapp.httpx.Client")
    def test_sends_message(self, mock_client_cls):
        from services.whatsapp import send_text_message
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"sid": "SM123"}
        mock_client.post.return_value = mock_resp

        result = send_text_message("+15551234567", "Hello")
        assert result["sid"] == "SM123"

        # Verify the Twilio API was called with whatsapp: prefixes
        call_kwargs = mock_client.post.call_args.kwargs
        data = call_kwargs["data"]
        assert data["To"] == "whatsapp:+15551234567"
        assert data["Body"] == "Hello"
        assert data["From"].startswith("whatsapp:")


class TestSendVoiceNote:
    @patch("services.whatsapp.httpx.Client")
    @patch("services.whatsapp.get_redis_binary")
    @patch("services.whatsapp.convert_to_ogg_opus")
    def test_caches_and_sends_media_url(self, mock_convert, mock_redis_bin, mock_client_cls):
        from services.whatsapp import send_voice_note
        mock_convert.return_value = b"OGGOPUSBYTES"
        mock_r = MagicMock()
        mock_redis_bin.return_value = mock_r

        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"sid": "SM456"}
        mock_client.post.return_value = mock_resp

        result = send_voice_note("+15551234567", b"WAVBYTES", input_format="wav")
        assert result["sid"] == "SM456"

        # Verify audio was cached in Redis with the right TTL
        mock_r.set.assert_called_once()
        set_args, set_kwargs = mock_r.set.call_args
        assert set_args[1] == b"OGGOPUSBYTES"
        assert set_kwargs["ex"] == 600

        # Verify MediaUrl points at our public base + /media/{token}
        call_kwargs = mock_client.post.call_args.kwargs
        media_url = call_kwargs["data"]["MediaUrl"]
        assert media_url.startswith("https://test.example.com/media/")
        assert call_kwargs["data"]["To"] == "whatsapp:+15551234567"


class TestServeMediaEndpoint:
    def test_returns_cached_bytes(self, client):
        with patch("services.whatsapp.get_redis_binary") as mock_get:
            mock_r = MagicMock()
            mock_get.return_value = mock_r
            mock_r.get.return_value = b"OGGOPUSBYTES"
            resp = client.get("/media/abc123")
            assert resp.status_code == 200
            assert resp.content == b"OGGOPUSBYTES"
            assert resp.headers["content-type"] == "audio/ogg"

    def test_returns_404_when_missing(self, client):
        with patch("services.whatsapp.get_redis_binary") as mock_get:
            mock_r = MagicMock()
            mock_get.return_value = mock_r
            mock_r.get.return_value = None
            resp = client.get("/media/unknown")
            assert resp.status_code == 404


class TestWebhookIntegration:
    def _build_request(self, phone: str, text: str = "HELP"):
        params = {"From": f"whatsapp:{phone}", "Body": text}
        sig = _sign("https://test.example.com/webhook", params)
        return params, sig

    def test_webhook_rejects_missing_signature(self, client):
        resp = client.post(
            "/webhook",
            data={"From": "whatsapp:+15551112222", "Body": "HELP"},
        )
        assert resp.status_code == 403

    def test_webhook_rejects_bad_signature(self, client):
        resp = client.post(
            "/webhook",
            data={"From": "whatsapp:+15551112222", "Body": "HELP"},
            headers={"X-Twilio-Signature": "bogus"},
        )
        assert resp.status_code == 403

    @patch("services.whatsapp._process_webhook_payload")
    @patch("services.whatsapp._check_rate_limit")
    def test_webhook_returns_429_when_rate_limited(self, mock_rate, mock_process, client):
        mock_rate.return_value = False
        params, sig = self._build_request("+15551112222")
        resp = client.post(
            "/webhook",
            data=params,
            headers={"X-Twilio-Signature": sig},
        )
        assert resp.status_code == 429
        assert resp.text == "Too many requests"
        mock_process.assert_not_called()

    @patch("services.whatsapp._process_webhook_payload")
    @patch("services.whatsapp._check_rate_limit")
    def test_webhook_allows_within_limit(self, mock_rate, mock_process, client):
        mock_rate.return_value = True
        params, sig = self._build_request("+15551112222")
        resp = client.post(
            "/webhook",
            data=params,
            headers={"X-Twilio-Signature": sig},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/xml")
        mock_process.assert_called_once()
