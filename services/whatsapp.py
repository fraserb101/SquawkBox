"""WhatsApp webhook handling and message delivery.

Handles Meta webhook verification, signature validation, inbound message routing,
voice note delivery, and text message sending.
"""

import hashlib
import hmac
import logging
import time

import httpx
import sentry_sdk
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import PlainTextResponse

from utils.audio_converter import convert_to_ogg_opus
from utils.config import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_APP_SECRET,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_VERIFY_TOKEN,
)
from utils.exceptions import DeliveryError
from utils.redis_client import get_redis

logger = logging.getLogger(__name__)

router = APIRouter()

META_API_BASE = f"https://graph.facebook.com/v19.0/{WHATSAPP_PHONE_NUMBER_ID}"
HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
}

# Rate limiting: sliding window of 5 messages per phone per 60 seconds,
# implemented via a Redis sorted set keyed on the sender's phone number.
# Timestamps (ms) are the score; entries older than the window are trimmed
# on every check.
RATE_LIMIT_KEY_PREFIX = "ratelimit:whatsapp:"
RATE_LIMIT_MAX_REQUESTS = 5
RATE_LIMIT_WINDOW_SECONDS = 60


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

    Applies per-sender rate limiting (5 messages / 60s). Returns HTTP 429
    if any sender in the payload has exceeded their quota. Otherwise
    processes messages and always returns 200 to Meta.
    """
    body = await request.body()

    # Verify X-Hub-Signature-256
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(body, signature):
        logger.warning("Invalid webhook signature — rejecting request")
        return Response(status_code=403)

    # Parse payload for rate-limit check
    try:
        payload = await request.json()
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Error parsing webhook payload: {e}")
        return {"status": "ok"}

    # Per-sender rate limiting — reject the whole payload if any sender is over quota
    senders = _extract_senders(payload)
    for phone in senders:
        if not _check_rate_limit(phone):
            logger.warning(f"Rate limit exceeded for {phone} — returning 429")
            return PlainTextResponse("Too many requests", status_code=429)

    try:
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


def _extract_senders(payload: dict) -> list[str]:
    """Walk the webhook payload and collect unique sender phone numbers."""
    senders: list[str] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for message in change.get("value", {}).get("messages", []):
                phone = message.get("from", "")
                if phone and phone not in senders:
                    senders.append(phone)
    return senders


def _check_rate_limit(phone: str) -> bool:
    """Sliding-window rate limiter using a Redis sorted set.

    For each sender, we keep a sorted set of timestamps (ms) scored by
    timestamp. On every check we:
      1. Trim entries older than the window.
      2. Count remaining entries.
      3. If under the limit, add the current timestamp and allow.
      4. If at/over the limit, deny (without adding a new entry).

    Returns True if the request is allowed, False if the limit is exceeded.
    Fails open on Redis errors — we don't want a Redis outage to cause
    users to be locked out.
    """
    now_ms = int(time.time() * 1000)
    window_start_ms = now_ms - (RATE_LIMIT_WINDOW_SECONDS * 1000)
    key = f"{RATE_LIMIT_KEY_PREFIX}{phone}"

    try:
        r = get_redis()
        # Atomic pipeline: trim old entries, count, conditionally add.
        pipe = r.pipeline()
        pipe.zremrangebyscore(key, 0, window_start_ms)
        pipe.zcard(key)
        results = pipe.execute()
        current_count = results[1]

        if current_count >= RATE_LIMIT_MAX_REQUESTS:
            return False

        # Under the limit — record this request and refresh the TTL.
        pipe = r.pipeline()
        pipe.zadd(key, {str(now_ms): now_ms})
        pipe.expire(key, RATE_LIMIT_WINDOW_SECONDS)
        pipe.execute()
        return True
    except Exception as e:
        # Fail open: log to Sentry but let the request through.
        sentry_sdk.capture_exception(e)
        logger.warning(f"Rate limit check failed for {phone}, allowing: {e}")
        return True


def _process_webhook_payload(payload: dict) -> None:
    """Extract messages from webhook payload and route them."""
    # Lazy imports to avoid circular dependencies
    from services.referrals import handle_start_command
    from services.ticker_manager import handle_command

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
