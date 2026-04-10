"""Referral and onboarding flow.

Handles START_[CODE] commands for new user signups and manages the
viral referral reward system.
"""

import logging
from datetime import datetime, timezone

import sentry_sdk

from services import database as db
from services.whatsapp import send_text_message
from utils.config import STRIPE_PAYMENT_LINK, TERMS_URL, YOUR_WHATSAPP_NUMBER
from utils.exceptions import DatabaseError

logger = logging.getLogger(__name__)

# Reward constants
REFERRAL_REWARD_DAYS = 7
REFERRAL_REWARD_TYPE = "trial_extension_7d"


def handle_start_command(phone: str, text: str) -> None:
    """Handle a START_[CODE] message from a new user.

    Validates the referral code, creates the user, records the referral,
    grants the referrer reward, and sends the welcome message.
    """
    # Check if user already exists
    existing = db.get_user_by_phone(phone)
    if existing:
        send_text_message(
            phone,
            "You're already signed up! Reply HELP to see available commands.",
        )
        return

    # Extract referral code from START_[CODE]
    code = text.strip().upper().replace("START_", "", 1).strip()
    if not code:
        send_text_message(
            phone,
            "Invalid signup link. Please use a valid referral link to get started.",
        )
        return

    # Validate that referral code exists
    referrer = db.get_user_by_referral_code(code)
    if not referrer:
        send_text_message(
            phone,
            "That referral code is not valid. Please check your link and try again, "
            "or contact support for help.",
        )
        return

    try:
        # Create the new user
        now = datetime.now(timezone.utc)
        new_user = db.create_user(
            phone=phone,
            referred_by=referrer["id"],
            terms_accepted_at=now,
        )

        # Record the referral
        referral = db.create_referral(
            referrer_id=referrer["id"],
            referred_user_id=new_user["id"],
        )

        # Grant the referrer a trial extension reward
        try:
            db.extend_trial(referrer["id"], REFERRAL_REWARD_DAYS)
            db.grant_referral_reward(referral["id"], REFERRAL_REWARD_TYPE)
            send_text_message(
                referrer["phone_number"],
                "Someone just signed up using your referral link! "
                f"You've earned an extra {REFERRAL_REWARD_DAYS} days on your trial.",
            )
        except Exception as e:
            # Referrer reward failure should not block new user onboarding
            sentry_sdk.capture_exception(e)
            logger.error(f"Failed to grant referral reward: {e}")

        # Send welcome message to the new user
        _send_welcome_message(new_user)

    except DatabaseError as e:
        sentry_sdk.capture_exception(e)
        send_text_message(
            phone,
            "Something went wrong during signup. Please try again later.",
        )


def _send_welcome_message(user: dict) -> None:
    """Send the onboarding welcome message to a new user.

    Includes trial details, auto-renewal terms, T&C link, and command list.
    """
    trial_expiry = user.get("trial_expiry", "")
    if trial_expiry:
        # Format the date nicely
        exp_dt = datetime.fromisoformat(trial_expiry.replace("Z", "+00:00"))
        expiry_str = exp_dt.strftime("%B %d, %Y at %H:%M UTC")
    else:
        expiry_str = "7 days from now"

    welcome = (
        "Welcome to *SquawkBox*! 🎙️\n\n"
        f"Your free trial is active until *{expiry_str}*.\n\n"
        "*Auto-renewal terms:*\n"
        f"After your trial, your subscription will auto-renew monthly at the standard rate. "
        "You can cancel anytime by replying STOP.\n\n"
        f"By continuing, you agree to our Terms & Conditions: {TERMS_URL}\n\n"
        "*Get started:*\n"
        "ADD [TICKER] — Track a stock (e.g. ADD AAPL)\n"
        "REMOVE [TICKER] — Stop tracking a stock\n"
        "LIST — See your tracked stocks\n"
        "SCHEDULE HH:MM [TZ] — Set a daily digest time\n"
        "HELP — Show all commands\n"
        "STOP — Unsubscribe\n\n"
        "Start by adding your first ticker!"
    )
    send_text_message(user["phone_number"], welcome)


def generate_referral_link(user: dict) -> str:
    """Generate a WhatsApp referral link for the user.

    Returns a wa.me deep link that opens a chat with the START_[CODE] command.
    """
    code = user.get("referral_code", "")
    whatsapp_number = YOUR_WHATSAPP_NUMBER
    return f"https://wa.me/{whatsapp_number}?text=START_{code}"


def send_referral_prompt(user: dict) -> None:
    """Send the user their referral link after their first squawk delivery."""
    link = generate_referral_link(user)
    send_text_message(
        user["phone_number"],
        "Enjoying SquawkBox? Share it with a friend and "
        f"get 7 extra days free!\n\nYour referral link: {link}",
    )
