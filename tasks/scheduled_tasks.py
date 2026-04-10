"""Scheduled delivery tasks.

Handles per-user notification schedules — delivers accumulated squawks
at each user's preferred notification_time in their timezone.
"""

import logging
from datetime import datetime, timezone

import pytz
import sentry_sdk

from services import database as db
from services.billing import is_user_eligible_for_delivery
from services.whatsapp import send_text_message

logger = logging.getLogger(__name__)


def run_scheduled_deliveries() -> int:
    """Check all users with a notification_time and deliver if it's their time.

    Called every minute by Celery beat. Matches the current minute
    in each user's timezone against their preferred notification_time.

    Returns the number of users notified.
    """
    try:
        sb = db.get_supabase()
        resp = (
            sb.table("users")
            .select("*")
            .not_.is_("notification_time", "null")
            .in_("subscription_status", ["trial", "active"])
            .execute()
        )
        users = resp.data
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Failed to fetch scheduled users: {e}")
        return 0

    notified = 0
    for user in users:
        try:
            if not is_user_eligible_for_delivery(user):
                continue

            if _is_notification_time(user):
                _deliver_digest(user)
                notified += 1
        except Exception as e:
            sentry_sdk.capture_exception(e)
            logger.error(f"Scheduled delivery failed for user {user['id']}: {e}")

    return notified


def _is_notification_time(user: dict) -> bool:
    """Check if the current time matches the user's notification window.

    Returns True if the current minute in the user's timezone matches
    their notification_time (HH:MM). Matches within a 1-minute window.
    """
    notification_time = user.get("notification_time")
    notification_tz = user.get("notification_tz", "UTC")

    if not notification_time:
        return False

    try:
        tz = pytz.timezone(notification_tz)
        now_in_tz = datetime.now(timezone.utc).astimezone(tz)
        current_hhmm = now_in_tz.strftime("%H:%M")
        return current_hhmm == notification_time
    except Exception as e:
        logger.warning(f"Invalid timezone {notification_tz} for user {user['id']}: {e}")
        return False


def _deliver_digest(user: dict) -> None:
    """Deliver a text-based digest summary to a scheduled user.

    For scheduled users, we send a text summary of recent squawks
    rather than individual voice notes, to avoid flooding.
    """
    user_id = user["id"]
    phone = user["phone_number"]

    try:
        # Get recent deliveries since last notification
        tickers = db.get_tickers_for_user(user_id)
        if not tickers:
            return

        # Update last_notified_at
        db.update_subscription(user_id, {
            "last_notified_at": datetime.now(timezone.utc).isoformat(),
        })

        # Build digest message
        ticker_list = ", ".join(sorted(tickers))
        send_text_message(
            phone,
            f"Good morning! Here's your SquawkBox daily digest.\n\n"
            f"Tracking: {ticker_list}\n\n"
            f"Check your recent voice notes for the latest updates on your stocks.",
        )

    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Digest delivery failed for user {user_id}: {e}")
