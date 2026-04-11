"""Ticker Manager — WhatsApp command parser.

Parses inbound user messages and routes them to the appropriate
database operations. Sends replies via WhatsApp.
"""

import logging
import re

import pytz
import sentry_sdk

from services import database as db
from services.whatsapp import send_text_message
from utils.config import STRIPE_PAYMENT_LINK
from utils.exceptions import DatabaseError

logger = logging.getLogger(__name__)

# Ticker limits by subscription tier
TICKER_LIMIT_TRIAL = 10
TICKER_LIMIT_PAID = 25

HELP_TEXT = (
    "*SquawkBox Commands*\n\n"
    "ADD [TICKER] — Add a stock ticker (e.g. ADD AAPL)\n"
    "REMOVE [TICKER] — Remove a ticker\n"
    "LIST — Show your current tickers\n"
    "SCHEDULE HH:MM [TZ] — Set daily digest time (e.g. SCHEDULE 08:00 Europe/London)\n"
    "SCHEDULE OFF — Switch back to real-time alerts\n"
    "STOP — Unsubscribe from all notifications\n"
    "HELP — Show this message"
)


def handle_command(phone: str, text: str) -> None:
    """Parse and execute a WhatsApp command from a user.

    This is the main entry point called by the webhook handler.
    """
    text = text.strip()
    command = text.upper()

    # STOP must be handled first — regulatory requirement
    if command == "STOP":
        _handle_stop(phone)
        return

    # Look up user
    user = db.get_user_by_phone(phone)

    if command == "HELP":
        send_text_message(phone, HELP_TEXT)
        return

    if not user:
        send_text_message(
            phone,
            "Welcome! You need to sign up first. "
            "Please use a referral link to get started with a free 7-day trial.",
        )
        return

    if command.startswith("ADD "):
        _handle_add(user, text)
    elif command.startswith("REMOVE "):
        _handle_remove(user, text)
    elif command == "LIST":
        _handle_list(user)
    elif command.startswith("SCHEDULE"):
        _handle_schedule(user, text)
    else:
        send_text_message(
            user["phone_number"],
            "Unknown command. Reply HELP to see available commands.",
        )


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def _handle_stop(phone: str) -> None:
    """Unsubscribe user from all deliveries. Regulatory requirement."""
    user = db.get_user_by_phone(phone)
    if not user:
        send_text_message(phone, "You are not currently subscribed.")
        return

    try:
        db.update_user(user["id"], {"subscription_status": "cancelled"})
        db.deactivate_subscription(user["id"])
        send_text_message(
            phone,
            "You have been unsubscribed from all SquawkBox notifications. "
            "We're sorry to see you go! Reply HELP if you change your mind.",
        )
    except DatabaseError as e:
        sentry_sdk.capture_exception(e)
        send_text_message(phone, "An error occurred. Please try again later.")


def _handle_add(user: dict, text: str) -> None:
    """Add a ticker subscription."""
    phone = user["phone_number"]
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        send_text_message(phone, "Please specify a ticker. Example: ADD AAPL")
        return

    ticker = parts[1].upper().strip()

    if not _is_valid_ticker(ticker):
        send_text_message(
            phone,
            f"'{ticker}' is not a valid ticker symbol. "
            "Tickers must be 1–5 uppercase letters (e.g. AAPL, MSFT, TSLA).",
        )
        return

    # Check subscription limit
    status = user.get("subscription_status", "trial")
    limit = TICKER_LIMIT_PAID if status == "active" else TICKER_LIMIT_TRIAL
    current_count = db.get_ticker_count_for_user(user["id"])

    if current_count >= limit:
        if status != "active":
            send_text_message(
                phone,
                f"You've reached the free trial limit of {TICKER_LIMIT_TRIAL} tickers. "
                f"Upgrade to track up to {TICKER_LIMIT_PAID}: {STRIPE_PAYMENT_LINK}",
            )
        else:
            send_text_message(
                phone,
                f"You've reached the maximum of {TICKER_LIMIT_PAID} tickers.",
            )
        return

    try:
        db.add_ticker(user["id"], ticker)
        send_text_message(phone, f"Added *{ticker}* to your watchlist.")
    except DatabaseError as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            send_text_message(phone, f"*{ticker}* is already on your watchlist.")
        else:
            sentry_sdk.capture_exception(e)
            send_text_message(phone, "An error occurred. Please try again.")


def _handle_remove(user: dict, text: str) -> None:
    """Remove a ticker subscription."""
    phone = user["phone_number"]
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        send_text_message(phone, "Please specify a ticker. Example: REMOVE AAPL")
        return

    ticker = parts[1].upper().strip()

    try:
        removed = db.remove_ticker(user["id"], ticker)
        if removed:
            send_text_message(phone, f"Removed *{ticker}* from your watchlist.")
        else:
            send_text_message(phone, f"*{ticker}* was not on your watchlist.")
    except DatabaseError as e:
        sentry_sdk.capture_exception(e)
        send_text_message(phone, "An error occurred. Please try again.")


def _handle_list(user: dict) -> None:
    """Reply with the user's current ticker list."""
    phone = user["phone_number"]
    try:
        tickers = db.get_tickers_for_user(user["id"])
        if tickers:
            ticker_list = ", ".join(sorted(tickers))
            send_text_message(phone, f"Your tickers: {ticker_list}")
        else:
            send_text_message(
                phone,
                "You have no tickers yet. Use ADD [TICKER] to start tracking stocks.",
            )
    except DatabaseError as e:
        sentry_sdk.capture_exception(e)
        send_text_message(phone, "An error occurred. Please try again.")


def _handle_schedule(user: dict, text: str) -> None:
    """Set or clear the user's notification schedule."""
    phone = user["phone_number"]
    parts = text.strip().split()

    # SCHEDULE OFF
    if len(parts) == 2 and parts[1].upper() == "OFF":
        try:
            db.set_notification_schedule(user["id"], None, None)
            send_text_message(
                phone,
                "Switched to real-time alerts. You'll get squawks as news breaks.",
            )
        except DatabaseError as e:
            sentry_sdk.capture_exception(e)
            send_text_message(phone, "An error occurred. Please try again.")
        return

    # SCHEDULE HH:MM [TZ]
    if len(parts) < 2:
        send_text_message(
            phone,
            "Usage: SCHEDULE HH:MM [timezone]\n"
            "Example: SCHEDULE 08:00 Europe/London\n"
            "Or: SCHEDULE OFF to switch to real-time alerts.",
        )
        return

    time_str = parts[1]
    tz_str = parts[2] if len(parts) >= 3 else "UTC"

    # Validate time format
    if not re.match(r"^\d{2}:\d{2}$", time_str):
        send_text_message(phone, "Invalid time format. Use HH:MM (e.g. 08:00).")
        return

    hour, minute = map(int, time_str.split(":"))
    if hour > 23 or minute > 59:
        send_text_message(phone, "Invalid time. Hours must be 00–23, minutes 00–59.")
        return

    # Validate timezone
    if tz_str not in pytz.all_timezones:
        # Try to find a close match
        matches = [tz for tz in pytz.all_timezones if tz_str.lower() in tz.lower()]
        if matches:
            suggestions = ", ".join(matches[:5])
            send_text_message(
                phone,
                f"Unknown timezone '{tz_str}'. Did you mean one of these?\n{suggestions}",
            )
        else:
            send_text_message(
                phone,
                f"Unknown timezone '{tz_str}'. "
                "Use a standard timezone like Europe/London, America/New_York, or Asia/Tokyo.",
            )
        return

    try:
        db.set_notification_schedule(user["id"], time_str, tz_str)
        send_text_message(
            phone,
            f"Daily digest set for *{time_str}* ({tz_str}). "
            "You'll receive a summary of all your squawks at that time.",
        )
    except DatabaseError as e:
        sentry_sdk.capture_exception(e)
        send_text_message(phone, "An error occurred. Please try again.")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _is_valid_ticker(ticker: str) -> bool:
    """Validate ticker format: 1–5 uppercase alpha characters."""
    return bool(re.match(r"^[A-Z]{1,5}$", ticker))
