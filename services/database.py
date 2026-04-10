"""Database layer — all Supabase queries.

Every other module imports from here. All write functions raise on failure
(never return None silently). Read functions return None when no record is found.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import sentry_sdk
from supabase import Client, create_client

from utils.config import SUPABASE_KEY, SUPABASE_URL
from utils.exceptions import DatabaseError

_client: Optional[Client] = None


def get_supabase() -> Client:
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


def _generate_referral_code() -> str:
    return secrets.token_urlsafe(6).upper()[:8]


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def get_user_by_phone(phone: str) -> Optional[dict]:
    """Look up a user by phone number. Returns None if not found."""
    try:
        resp = get_supabase().table("users").select("*").eq("phone_number", phone).execute()
        return resp.data[0] if resp.data else None
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DatabaseError(f"Failed to look up user by phone: {e}")


def get_user_by_id(user_id: str) -> Optional[dict]:
    """Look up a user by ID. Returns None if not found."""
    try:
        resp = get_supabase().table("users").select("*").eq("id", user_id).execute()
        return resp.data[0] if resp.data else None
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DatabaseError(f"Failed to look up user by ID: {e}")


def get_user_by_referral_code(code: str) -> Optional[dict]:
    """Look up a user by referral code. Returns None if not found."""
    try:
        resp = get_supabase().table("users").select("*").eq("referral_code", code).execute()
        return resp.data[0] if resp.data else None
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DatabaseError(f"Failed to look up user by referral code: {e}")


def create_user(
    phone: str,
    referral_code: Optional[str] = None,
    referred_by: Optional[str] = None,
    terms_accepted_at: Optional[datetime] = None,
) -> dict:
    """Create a new user with a 7-day trial. Raises DatabaseError on failure."""
    now = datetime.now(timezone.utc)
    code = referral_code or _generate_referral_code()

    data = {
        "phone_number": phone,
        "referral_code": code,
        "referred_by": referred_by,
        "trial_expiry": (now + timedelta(days=7)).isoformat(),
        "subscription_status": "trial",
        "terms_accepted_at": (terms_accepted_at or now).isoformat(),
    }

    try:
        resp = get_supabase().table("users").insert(data).execute()
        if not resp.data:
            raise DatabaseError("User insert returned no data")
        user = resp.data[0]

        # Also create a subscriptions record for the user
        get_supabase().table("subscriptions").insert({
            "user_id": user["id"],
            "is_active": True,
        }).execute()

        return user
    except DatabaseError:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DatabaseError(f"Failed to create user: {e}")


def update_user(user_id: str, updates: dict) -> dict:
    """Update user fields. Raises DatabaseError on failure."""
    try:
        resp = get_supabase().table("users").update(updates).eq("id", user_id).execute()
        if not resp.data:
            raise DatabaseError(f"User update returned no data for {user_id}")
        return resp.data[0]
    except DatabaseError:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DatabaseError(f"Failed to update user {user_id}: {e}")


# ---------------------------------------------------------------------------
# Ticker Subscriptions
# ---------------------------------------------------------------------------


def get_tickers_for_user(user_id: str) -> list[str]:
    """Return the list of tickers a user is subscribed to."""
    try:
        resp = (
            get_supabase()
            .table("ticker_subscriptions")
            .select("ticker")
            .eq("user_id", user_id)
            .execute()
        )
        return [row["ticker"] for row in resp.data]
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DatabaseError(f"Failed to get tickers for user {user_id}: {e}")


def get_ticker_count_for_user(user_id: str) -> int:
    """Return the number of tickers a user is subscribed to."""
    return len(get_tickers_for_user(user_id))


def add_ticker(user_id: str, ticker: str) -> dict:
    """Add a ticker subscription. Raises DatabaseError on failure."""
    try:
        resp = (
            get_supabase()
            .table("ticker_subscriptions")
            .insert({"user_id": user_id, "ticker": ticker.upper()})
            .execute()
        )
        if not resp.data:
            raise DatabaseError("Ticker insert returned no data")
        return resp.data[0]
    except DatabaseError:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DatabaseError(f"Failed to add ticker {ticker} for user {user_id}: {e}")


def remove_ticker(user_id: str, ticker: str) -> bool:
    """Remove a ticker subscription. Returns True if removed, False if not found."""
    try:
        resp = (
            get_supabase()
            .table("ticker_subscriptions")
            .delete()
            .eq("user_id", user_id)
            .eq("ticker", ticker.upper())
            .execute()
        )
        return bool(resp.data)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DatabaseError(f"Failed to remove ticker {ticker} for user {user_id}: {e}")


# ---------------------------------------------------------------------------
# Notification Schedule
# ---------------------------------------------------------------------------


def set_notification_schedule(user_id: str, time_str: Optional[str], tz_str: Optional[str]) -> dict:
    """Set or clear the user's daily notification schedule.

    Pass time_str=None to revert to real-time push.
    """
    return update_user(user_id, {
        "notification_time": time_str,
        "notification_tz": tz_str,
    })


# ---------------------------------------------------------------------------
# Users by Ticker (for delivery)
# ---------------------------------------------------------------------------


def get_users_for_ticker(ticker: str) -> list[dict]:
    """Get all active users subscribed to a given ticker.

    Returns users with subscription_status in ('trial', 'active') whose
    trial hasn't expired (for trial users).
    """
    try:
        resp = (
            get_supabase()
            .table("ticker_subscriptions")
            .select("user_id, users(*)")
            .eq("ticker", ticker.upper())
            .execute()
        )
        now = datetime.now(timezone.utc)
        active_users = []
        for row in resp.data:
            user = row.get("users")
            if not user:
                continue
            status = user.get("subscription_status")
            if status == "active":
                active_users.append(user)
            elif status == "trial":
                trial_exp = user.get("trial_expiry")
                if trial_exp:
                    exp_dt = datetime.fromisoformat(trial_exp.replace("Z", "+00:00"))
                    if exp_dt > now:
                        active_users.append(user)
        return active_users
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DatabaseError(f"Failed to get users for ticker {ticker}: {e}")


# ---------------------------------------------------------------------------
# Squawk Logs & Deduplication
# ---------------------------------------------------------------------------


def compute_url_hash(url: str) -> str:
    """Compute MD5 hash of an article URL for deduplication."""
    return hashlib.md5(url.encode()).hexdigest()


def hash_already_processed(url_hash: str) -> bool:
    """Check if a news article (by URL hash) has already been fully processed."""
    try:
        resp = (
            get_supabase()
            .table("squawk_logs")
            .select("id")
            .eq("news_url_hash", url_hash)
            .execute()
        )
        return bool(resp.data)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DatabaseError(f"Failed to check hash {url_hash}: {e}")


def save_squawk_log(url_hash: str, ticker: str, status: str) -> str:
    """Save a squawk log entry. Only call on full pipeline success.

    Returns the squawk_id (UUID).
    Raises DatabaseError on failure.
    """
    try:
        resp = (
            get_supabase()
            .table("squawk_logs")
            .insert({
                "news_url_hash": url_hash,
                "ticker": ticker.upper(),
                "status": status,
            })
            .execute()
        )
        if not resp.data:
            raise DatabaseError("Squawk log insert returned no data")
        return resp.data[0]["id"]
    except DatabaseError:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DatabaseError(f"Failed to save squawk log: {e}")


def save_squawk_delivery(squawk_id: str, user_id: str) -> dict:
    """Record that a user received a specific squawk. Raises DatabaseError on failure."""
    try:
        resp = (
            get_supabase()
            .table("squawk_deliveries")
            .insert({
                "squawk_id": squawk_id,
                "user_id": user_id,
            })
            .execute()
        )
        if not resp.data:
            raise DatabaseError("Squawk delivery insert returned no data")
        return resp.data[0]
    except DatabaseError:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DatabaseError(f"Failed to save squawk delivery: {e}")


# ---------------------------------------------------------------------------
# Subscriptions table
# ---------------------------------------------------------------------------


def update_subscription(user_id: str, updates: dict) -> dict:
    """Update the subscriptions record for a user."""
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        resp = (
            get_supabase()
            .table("subscriptions")
            .update(updates)
            .eq("user_id", user_id)
            .execute()
        )
        if not resp.data:
            raise DatabaseError(f"Subscription update returned no data for {user_id}")
        return resp.data[0]
    except DatabaseError:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DatabaseError(f"Failed to update subscription for {user_id}: {e}")


def deactivate_subscription(user_id: str) -> dict:
    """Deactivate a user's subscription (pause delivery)."""
    return update_subscription(user_id, {"is_active": False})


# ---------------------------------------------------------------------------
# Referrals
# ---------------------------------------------------------------------------


def create_referral(referrer_id: str, referred_user_id: str) -> dict:
    """Record a referral. Raises DatabaseError on failure."""
    try:
        resp = (
            get_supabase()
            .table("referrals")
            .insert({
                "referrer_id": referrer_id,
                "referred_user_id": referred_user_id,
            })
            .execute()
        )
        if not resp.data:
            raise DatabaseError("Referral insert returned no data")
        return resp.data[0]
    except DatabaseError:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DatabaseError(f"Failed to create referral: {e}")


def grant_referral_reward(referral_id: str, reward_type: str) -> dict:
    """Mark a referral as rewarded."""
    try:
        resp = (
            get_supabase()
            .table("referrals")
            .update({"reward_granted": True, "reward_type": reward_type})
            .eq("id", referral_id)
            .execute()
        )
        if not resp.data:
            raise DatabaseError(f"Referral reward update returned no data for {referral_id}")
        return resp.data[0]
    except DatabaseError:
        raise
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DatabaseError(f"Failed to grant referral reward: {e}")


def extend_trial(user_id: str, days: int) -> dict:
    """Extend a user's trial by the given number of days."""
    user = get_user_by_id(user_id)
    if not user:
        raise DatabaseError(f"User {user_id} not found")

    current_expiry = user.get("trial_expiry")
    if current_expiry:
        base = datetime.fromisoformat(current_expiry.replace("Z", "+00:00"))
    else:
        base = datetime.now(timezone.utc)

    new_expiry = base + timedelta(days=days)
    return update_user(user_id, {"trial_expiry": new_expiry.isoformat()})


# ---------------------------------------------------------------------------
# Trial Expiry Queries
# ---------------------------------------------------------------------------


def get_expiring_trials(within_hours: int = 24) -> list[dict]:
    """Get trial users whose trial expires within the given number of hours."""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=within_hours)

    try:
        resp = (
            get_supabase()
            .table("users")
            .select("*")
            .eq("subscription_status", "trial")
            .gte("trial_expiry", now.isoformat())
            .lte("trial_expiry", cutoff.isoformat())
            .execute()
        )
        return resp.data
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DatabaseError(f"Failed to get expiring trials: {e}")


# ---------------------------------------------------------------------------
# Delivery History (Admin)
# ---------------------------------------------------------------------------


def get_user_delivery_history(user_id: str, limit: int = 50) -> list[dict]:
    """Get squawk delivery history for a user."""
    try:
        resp = (
            get_supabase()
            .table("squawk_deliveries")
            .select("*, squawk_logs(*)")
            .eq("user_id", user_id)
            .order("sent_at", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DatabaseError(f"Failed to get delivery history for {user_id}: {e}")


# ---------------------------------------------------------------------------
# Stats (Admin)
# ---------------------------------------------------------------------------


def get_stats() -> dict:
    """Get aggregate stats for the admin dashboard."""
    try:
        sb = get_supabase()

        all_users = sb.table("users").select("id, subscription_status").execute()
        total = len(all_users.data)
        trials = sum(1 for u in all_users.data if u["subscription_status"] == "trial")
        active = sum(1 for u in all_users.data if u["subscription_status"] == "active")

        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        today_squawks = (
            sb.table("squawk_logs")
            .select("id")
            .gte("created_at", today_start.isoformat())
            .execute()
        )

        return {
            "total_users": total,
            "active_trials": trials,
            "active_subscribers": active,
            "squawks_delivered_today": len(today_squawks.data),
        }
    except Exception as e:
        sentry_sdk.capture_exception(e)
        raise DatabaseError(f"Failed to get stats: {e}")
