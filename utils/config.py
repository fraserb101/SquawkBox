"""Centralized configuration loaded from environment variables."""

import os

from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return val


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default)


# Supabase
SUPABASE_URL = _require("SUPABASE_URL")
SUPABASE_KEY = _require("SUPABASE_KEY")

# Redis
REDIS_URL = _optional("REDIS_URL", "redis://localhost:6379/0")

# News
NEWSDATA_API_KEY = _require("NEWSDATA_API_KEY")

# Research
TAVILY_API_KEY = _require("TAVILY_API_KEY")

# AI Inference
TOGETHER_API_KEY = _require("TOGETHER_API_KEY")

# Voice
CARTESIA_API_KEY = _require("CARTESIA_API_KEY")
CARTESIA_VOICE_ID = _require("CARTESIA_VOICE_ID")

# WhatsApp via Twilio
TWILIO_ACCOUNT_SID = _require("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = _require("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = _require("TWILIO_WHATSAPP_FROM")

# Public base URL (e.g. ngrok tunnel) where Twilio POSTs the webhook and
# fetches cached voice notes from. Used both for signature verification
# and to construct MediaUrl values.
PUBLIC_BASE_URL = _require("PUBLIC_BASE_URL")

# Stripe
STRIPE_SECRET_KEY = _require("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = _require("STRIPE_WEBHOOK_SECRET")
STRIPE_PAYMENT_LINK = _require("STRIPE_PAYMENT_LINK")

# Admin
ADMIN_SECRET = _require("ADMIN_SECRET")

# Sentry
SENTRY_DSN = _optional("SENTRY_DSN")

# WhatsApp number
YOUR_WHATSAPP_NUMBER = _optional("YOUR_WHATSAPP_NUMBER")

# Legal
TERMS_URL = _require("TERMS_URL")
