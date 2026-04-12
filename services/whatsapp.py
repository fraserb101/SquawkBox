"""WhatsApp integration via Twilio.

Handles Twilio webhook signature verification, inbound message routing,
per-sender rate limiting, text message sending, and voice note delivery.

Voice notes are served to Twilio out-of-band via a short-lived media
endpoint backed by Redis: raw OGG/Opus bytes are cached under a random
token, and Twilio fetches them via PUBLIC_BASE_URL/media/{token}.
"""

import logging
import secrets
import time

import httpx
import sentry_sdk
from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse
from twilio.request_validator import RequestValidator

from utils.audio_converter import convert_to_ogg_opus
from utils.config import (
    PUBLIC_BASE_URL,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_WHATSAPP_FROM,
)
from utils.exceptions import DeliveryError
from utils.redis_client import get_redis, get_redis_binary

logger = logging.getLogger(__name__)

router = APIRouter()

TWILIO_MESSAGES_URL = (
    f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
)

# Rate limiting: sliding window of 5 messages per phone per 60 seconds,
# implemented via a Redis sorted set keyed on the sender's phone number.
RATE_LIMIT_KEY_PREFIX = "ratelimit:whatsapp:"
RATE_LIMIT_MAX_REQUESTS = 5
RATE_LIMIT_WINDOW_SECONDS = 60

# Media token storage: audio bytes cached in Redis so Twilio can fetch them
# via PUBLIC_BASE_URL/media/{token}. TTL is long enough to cover Twilio's
# delivery + retry window but short enough to minimise exposure.
MEDIA_TOKEN_TTL_SECONDS = 600  # 10 minutes
MEDIA_TOKEN_PREFIX = "twilio:media:"

# Empty TwiML response — we reply out-of-band via the Twilio REST API, so
# every webhook returns this body to suppress Twilio's "no response" warning.
_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


# ---------------------------------------------------------------------------
# Webhook Endpoint
# ---------------------------------------------------------------------------


@router.post("/webhook")
async def receive_webhook(request: Request):
    """Process inbound WhatsApp messages from Twilio.

    Verifies the X-Twilio-Signature header, applies per-sender rate limiting
    (5 msgs / 60s, returning HTTP 429 if exceeded), then routes the message
    to the appropriate handler. Always returns an empty TwiML body to
    acknowledge the webhook.
    """
    form = dict(await request.form())

    # Verify Twilio signature
    signature = request.headers.get("X-Twilio-Signature", "")
    full_url = f"{PUBLIC_BASE_URL.rstrip('/')}{request.url.path}"
    if not _verify_signature(full_url, form, signature):
        logger.warning("Invalid Twilio webhook signature — rejecting request")
        return Response(status_code=403)

    # Per-sender rate limiting
    phone = _extract_sender(form)
    if phone and not _check_rate_limit(phone):
        logger.warning(f"Rate limit exceeded for {phone} — returning 429")
        return PlainTextResponse("Too many requests", status_code=429)

    try:
        _process_webhook_payload(form)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Error processing webhook: {e}")

    return Response(content=_EMPTY_TWIML, media_type="application/xml")


def _verify_signature(full_url: str, params: dict, signature_header: str) -> bool:
    """Verify the X-Twilio-Signature header.

    Twilio computes: base64(hmac_sha1(auth_token, url + concatenated_sorted_params))
    where params are concatenated as key+value pairs in lexicographic order.
    We delegate the math to Twilio's RequestValidator so media-message and
    Unicode edge cases are handled correctly.

    Important: Twilio signs the *public* URL it POSTed to, so behind a
    reverse proxy we must reconstruct it from PUBLIC_BASE_URL rather than
    from FastAPI's internal request.url.
    """
    if not signature_header:
        return False
    try:
        validator = RequestValidator(TWILIO_AUTH_TOKEN)
        return validator.validate(full_url, params, signature_header)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return False


def _extract_sender(form: dict) -> str:
    """Pull the sender phone from a Twilio webhook form payload.

    Twilio uses the `whatsapp:+15551234567` format in the `From` field —
    strip the prefix so downstream handlers see a bare E.164 number.
    """
    raw = form.get("From", "")
    if isinstance(raw, str) and raw.startswith("whatsapp:"):
        return raw[len("whatsapp:"):]
    return raw or ""


def _check_rate_limit(phone: str) -> bool:
    """Sliding-window rate limiter using a Redis sorted set.

    For each sender, we keep a sorted set of timestamps (ms) scored by
    timestamp. On every check we:
      1. Trim entries older than the window.
      2. Count remaining entries.
      3. If under the limit, add the current timestamp and allow.
      4. If at/over the limit, deny (without adding a new entry).

    Returns True if the request is allowed, False if the limit is exceeded.
    Fails open on Redis errors so a Redis outage doesn't lock users out.
    """
    now_ms = int(time.time() * 1000)
    window_start_ms = now_ms - (RATE_LIMIT_WINDOW_SECONDS * 1000)
    key = f"{RATE_LIMIT_KEY_PREFIX}{phone}"

    try:
        r = get_redis()
        pipe = r.pipeline()
        pipe.zremrangebyscore(key, 0, window_start_ms)
        pipe.zcard(key)
        results = pipe.execute()
        current_count = results[1]

        if current_count >= RATE_LIMIT_MAX_REQUESTS:
            return False

        pipe = r.pipeline()
        pipe.zadd(key, {str(now_ms): now_ms})
        pipe.expire(key, RATE_LIMIT_WINDOW_SECONDS)
        pipe.execute()
        return True
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning(f"Rate limit check failed for {phone}, allowing: {e}")
        return True


def _process_webhook_payload(form: dict) -> None:
    """Extract the message from a Twilio form payload and route it."""
    # Lazy imports to avoid circular dependencies
    from services.referrals import handle_start_command
    from services.ticker_manager import handle_command

    phone = _extract_sender(form)
    text = str(form.get("Body", "")).strip()
    if not phone or not text:
        return

    try:
        if text.upper().startswith("START_"):
            handle_start_command(phone, text)
        else:
            handle_command(phone, text)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Error handling message from {phone}: {e}")


# ---------------------------------------------------------------------------
# Media Endpoint — Twilio fetches cached voice notes from here
# ---------------------------------------------------------------------------


@router.get("/media/{token}")
async def serve_media(token: str):
    """Serve a cached voice note to Twilio.

    Twilio fetches this URL when delivering a WhatsApp media message. The
    OGG bytes are cached in Redis under a random token with a short TTL,
    so the audio is available for Twilio's fetch window but isn't kept
    around after it's been delivered.
    """
    try:
        audio_bytes = get_redis_binary().get(f"{MEDIA_TOKEN_PREFIX}{token}")
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return Response(status_code=503)

    if not audio_bytes:
        return Response(status_code=404)

    return Response(
        content=audio_bytes,
        media_type="audio/ogg",
        headers={"Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------------------
# Message Delivery
# ---------------------------------------------------------------------------


def send_text_message(phone: str, text: str) -> dict:
    """Send a text message via Twilio's WhatsApp API.

    Raises DeliveryError on failure.
    """
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                TWILIO_MESSAGES_URL,
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                data={
                    "From": f"whatsapp:{TWILIO_WHATSAPP_FROM}",
                    "To": f"whatsapp:{phone}",
                    "Body": text,
                },
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        sentry_sdk.capture_exception(e)
        raise DeliveryError(
            f"Twilio text delivery failed ({e.response.status_code}): {e.response.text}"
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DeliveryError(f"Twilio text delivery error: {e}")


def send_voice_note(phone: str, audio_bytes: bytes, input_format: str = "wav") -> dict:
    """Deliver audio as a WhatsApp voice note via Twilio.

    Steps:
    1. Convert audio to OGG/Opus via FFmpeg (WhatsApp's native voice format).
    2. Cache the OGG bytes in Redis under a random token (TTL 10 minutes).
    3. POST to the Twilio Messages API with MediaUrl pointing at our
       /media/{token} endpoint, so Twilio fetches the bytes and forwards
       them to the recipient.

    Raises AudioConversionError or DeliveryError on failure.
    """
    ogg_bytes = convert_to_ogg_opus(audio_bytes, input_format=input_format)

    token = secrets.token_urlsafe(32)
    try:
        get_redis_binary().set(
            f"{MEDIA_TOKEN_PREFIX}{token}",
            ogg_bytes,
            ex=MEDIA_TOKEN_TTL_SECONDS,
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DeliveryError(f"Failed to cache voice note in Redis: {e}")

    media_url = f"{PUBLIC_BASE_URL.rstrip('/')}/media/{token}"

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                TWILIO_MESSAGES_URL,
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                data={
                    "From": f"whatsapp:{TWILIO_WHATSAPP_FROM}",
                    "To": f"whatsapp:{phone}",
                    "MediaUrl": media_url,
                },
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        sentry_sdk.capture_exception(e)
        raise DeliveryError(
            f"Twilio voice delivery failed ({e.response.status_code}): {e.response.text}"
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DeliveryError(f"Twilio voice delivery error: {e}")
