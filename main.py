"""SquawkBox — FastAPI application entry point.

Wires together all routers (WhatsApp webhook, Stripe webhook, admin)
and initializes Sentry for error tracking.

Run with: uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
"""

import logging

import sentry_sdk
from fastapi import FastAPI

from admin.admin import router as admin_router
from services.billing import router as billing_router
from services.whatsapp import router as whatsapp_router
from utils.config import SENTRY_DSN

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ---------------------------------------------------------------------------
# Sentry
# ---------------------------------------------------------------------------

if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
    )

# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="SquawkBox",
    description="AI-generated financial news voice notes via WhatsApp",
    version="1.0.0",
)

app.include_router(whatsapp_router, tags=["WhatsApp"])
app.include_router(billing_router, tags=["Billing"])
app.include_router(admin_router, prefix="/admin", tags=["Admin"])


@app.get("/health")
async def health():
    return {"status": "ok"}
