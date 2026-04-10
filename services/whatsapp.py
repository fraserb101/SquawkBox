"""WhatsApp webhook handling and message delivery.

Handles Meta webhook verification, signature validation, inbound message routing,
voice note delivery, and text message sending.
"""

import hashlib
import hmac
import logging
import tempfile

import httpx
import sentry_sdk
from fastapi import APIRouter, Query, Request, Response

from utils.audio_converter import convert_to_ogg_opus
from utils.config import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_APP_SECRET,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_VERIFY_TOKEN,
)
from utils.exceptions import AudioConversionError, DeliveryError

logger = logging.getLogger(__name__)

router = APIRouter()

META_API_BASE = f"https://graph.facebook.com/v19.0/{WHATSAPP_PHONE_NUMBER_ID}"
HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
}


# ---------------------------------------------------------------------------
# Webhook Endpoints
# ---------------------------------------------------------------------------


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    """Meta hub.challenge verification handshake."""
    if hub_mode == "subscribe" and hub_verify_token == WHATSAPP_VERIFY_TOKEN:
        return Response(content=hub_challenge, media_type="text/plain")
    return Response(status_code=403)


@router.post("/webhook")
async def receive_webhook(request: Request):
    """Process inbound WhatsApp messages.

    Always returns 200 to Meta to prevent retries, even on internal errors.
    """
    body = await request.body()

    # Verify X-Hub-Signature-256
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(body, signature):
        logger.warning("Invalid webhook signature — rejecting request")
        return Response(status_code=403)

    try:
        payload = await request.json()
        _process_webhook_payload(payload)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Error processing webhook: {e}")

    # Always return 200 — Meta retries on non-200
    return {"status": "ok"}


def _verify_signature(body: bytes, signature_header: str) -> bool:
    """Verify the X-Hub-Signature-256 header from Meta."""
    if not signature_header:
        return False
    expected = "sha256=" + hmac.new(
        WHATSAPP_APP_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _process_webhook_payload(payload: dict) -> None:
    """Extract messages from webhook payload and route them."""
    # Lazy imports to avoid circular dependencies
    from services.ticker_manager import handle_command
    from services.referrals import handle_start_command

    entries = payload.get("entry", [])
    for entry in entries:
        changes = entry.get("changes", [])
        for change in changes:
            value = change.get("value", {})
            messages = value.get("messages", [])
            for message in messages:
                if message.get("type") != "text":
                    continue
                phone = message.get("from", "")
                text = message.get("text", {}).get("body", "").strip()
                if not phone or not text:
                    continue

                try:
                    # Route START_[CODE] to referrals, everything else to ticker_manager
                    if text.upper().startswith("START_"):
                        handle_start_command(phone, text)
                    else:
                        handle_command(phone, text)
                except Exception as e:
                    sentry_sdk.capture_exception(e)
                    logger.error(f"Error handling message from {phone}: {e}")


# ---------------------------------------------------------------------------
# Message Delivery
# ---------------------------------------------------------------------------


def send_text_message(phone: str, text: str) -> dict:
    """Send a text message via WhatsApp Cloud API.

    Raises DeliveryError on failure.
    """
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{META_API_BASE}/messages",
                headers=HEADERS,
                json={
                    "messaging_product": "whatsapp",
                    "to": phone,
                    "type": "text",
                    "text": {"body": text},
                },
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        sentry_sdk.capture_exception(e)
        raise DeliveryError(
            f"WhatsApp text delivery failed ({e.response.status_code}): {e.response.text}"
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DeliveryError(f"WhatsApp text delivery error: {e}")


def send_voice_note(phone: str, audio_bytes: bytes, input_format: str = "wav") -> dict:
    """Convert audio to OGG/Opus and send as a WhatsApp voice note.

    Steps:
    1. Convert audio to .ogg (libopus) via FFmpeg
    2. Upload media to Meta
    3. Send voice message using returned media_id

    Raises AudioConversionError or DeliveryError on failure.
    """
    # Step 1: Convert to OGG/Opus
    ogg_bytes = convert_to_ogg_opus(audio_bytes, input_format=input_format)

    # Step 2: Upload to Meta media endpoint
    media_id = _upload_media(ogg_bytes)

    # Step 3: Send voice message
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{META_API_BASE}/messages",
                headers=HEADERS,
                json={
                    "messaging_product": "whatsapp",
                    "to": phone,
                    "type": "audio",
                    "audio": {"id": media_id},
                },
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        sentry_sdk.capture_exception(e)
        raise DeliveryError(
            f"WhatsApp voice delivery failed ({e.response.status_code}): {e.response.text}"
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DeliveryError(f"WhatsApp voice delivery error: {e}")


def _upload_media(ogg_bytes: bytes) -> str:
    """Upload audio bytes to Meta's media endpoint. Returns the media_id."""
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                f"{META_API_BASE}/media",
                headers={"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"},
                data={"messaging_product": "whatsapp", "type": "audio/ogg; codecs=opus"},
                files={"file": ("audio.ogg", ogg_bytes, "audio/ogg")},
            )
            resp.raise_for_status()
            data = resp.json()
            media_id = data.get("id")
            if not media_id:
                raise DeliveryError(f"Media upload returned no id: {data}")
            return media_id
    except httpx.HTTPStatusError as e:
        sentry_sdk.capture_exception(e)
        raise DeliveryError(
            f"Media upload failed ({e.response.status_code}): {e.response.text}"
        )
    except DeliveryError:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DeliveryError(f"Media upload error: {e}")
