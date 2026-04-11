"""Admin endpoints for internal management.

All routes are protected by X-Admin-Secret header. Never expose publicly.
"""

import logging

import sentry_sdk
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from services import database as db
from utils.config import ADMIN_SECRET
from utils.exceptions import DatabaseError

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------


def verify_admin(x_admin_secret: str = Header(...)):
    """Verify the admin secret header on every request."""
    if x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Invalid admin secret")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/extend-trial", dependencies=[Depends(verify_admin)])
async def extend_trial(user_id: str, days: int = Query(default=7, ge=1, le=365)):
    """Extend a user's trial by the specified number of days."""
    try:
        user = db.extend_trial(user_id, days)
        return {
            "status": "ok",
            "user_id": user_id,
            "new_trial_expiry": user.get("trial_expiry"),
            "days_added": days,
        }
    except DatabaseError as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/users/{user_id}/cancel", dependencies=[Depends(verify_admin)])
async def cancel_user(user_id: str):
    """Cancel a user's subscription and pause delivery."""
    try:
        db.update_user(user_id, {"subscription_status": "cancelled"})
        db.deactivate_subscription(user_id)
        return {"status": "ok", "user_id": user_id, "subscription_status": "cancelled"}
    except DatabaseError as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/users/{user_id}/history", dependencies=[Depends(verify_admin)])
async def user_history(user_id: str, limit: int = Query(default=50, ge=1, le=200)):
    """View squawk delivery history for a user."""
    try:
        history = db.get_user_delivery_history(user_id, limit=limit)
        return {"user_id": user_id, "deliveries": history, "count": len(history)}
    except DatabaseError as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/stats", dependencies=[Depends(verify_admin)])
async def stats():
    """View aggregate system statistics."""
    try:
        return db.get_stats()
    except DatabaseError as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users/{user_id}", dependencies=[Depends(verify_admin)])
async def get_user(user_id: str):
    """View a specific user's details."""
    try:
        user = db.get_user_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        tickers = db.get_tickers_for_user(user_id)
        return {**user, "tickers": tickers}
    except DatabaseError as e:
        sentry_sdk.capture_exception(e)
        raise HTTPException(status_code=500, detail=str(e))
