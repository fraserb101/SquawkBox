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

# WhatsApp
WHATSAPP_PHONE_NUMBER_ID = _require("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_ACCESS_TOKEN = _require("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_VERIFY_TOKEN = _require("WHATSAPP_VERIFY_TOKEN")
WHATSAPP_APP_SECRET = _require("WHATSAPP_APP_SECRET")

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
