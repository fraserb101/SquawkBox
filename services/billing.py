"""Billing service — Stripe integration and subscription management.

Handles Stripe webhooks, trial expiry notifications, and the
subscription guard that gates squawk delivery.
"""

import logging
from datetime import datetime, timezone

import sentry_sdk
import stripe
from fastapi import APIRouter, Request, Response

from services import database as db
from services.whatsapp import send_text_message
from utils.config import STRIPE_PAYMENT_LINK, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET
from utils.exceptions import DatabaseError

logger = logging.getLogger(__name__)

router = APIRouter()

stripe.api_key = STRIPE_SECRET_KEY


# ---------------------------------------------------------------------------
# Subscription Guard
# ---------------------------------------------------------------------------


def is_user_eligible_for_delivery(user: dict) -> bool:
    """Check whether a user should receive squawk deliveries.

    Returns True for trial users (whose trial hasn't expired) and active subscribers.
    Returns False for expired or cancelled users.
    """
    status = user.get("subscription_status")

    if status == "active":
        return True

    if status == "trial":
        trial_expiry = user.get("trial_expiry")
        if not trial_expiry:
            return False
        exp_dt = datetime.fromisoformat(trial_expiry.replace("Z", "+00:00"))
        return exp_dt > datetime.now(timezone.utc)

    # expired, cancelled, or unknown — do not deliver
    return False


# ---------------------------------------------------------------------------
# Trial Expiry Notifications (called by Celery beat)
# ---------------------------------------------------------------------------


def check_expiring_trials() -> int:
    """Send reminders to users whose trials expire within 24 hours.

    Returns the number of notifications sent.
    """
    try:
        expiring_users = db.get_expiring_trials(within_hours=24)
    except DatabaseError as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Failed to fetch expiring trials: {e}")
        return 0

    sent_count = 0
    for user in expiring_users:
        try:
            trial_expiry = user.get("trial_expiry", "")
            if trial_expiry:
                exp_dt = datetime.fromisoformat(trial_expiry.replace("Z", "+00:00"))
                expiry_str = exp_dt.strftime("%B %d, %Y at %H:%M UTC")
            else:
                expiry_str = "soon"

            message = (
                "Your SquawkBox free trial expires on "
                f"*{expiry_str}*.\n\n"
                "Subscribe to keep receiving AI-powered stock alerts:\n"
                f"{STRIPE_PAYMENT_LINK}\n\n"
                "*Subscription details:*\n"
                "• Auto-renews monthly until cancelled\n"
                "• Cancel anytime by replying STOP or CANCEL\n\n"
                "Don't miss out on your daily squawks!"
            )
            send_text_message(user["phone_number"], message)
            sent_count += 1
        except Exception as e:
            # One failure must not block others
            sentry_sdk.capture_exception(e)
            logger.error(f"Failed to send trial expiry notification to {user.get('id')}: {e}")

    return sent_count


def expire_overdue_trials() -> int:
    """Mark trial users whose trial has passed as expired.

    Returns the number of users expired.
    """
    try:
        # Get all trial users
        sb = db.get_supabase()
        now = datetime.now(timezone.utc)
        resp = (
            sb.table("users")
            .select("id, phone_number, trial_expiry")
            .eq("subscription_status", "trial")
            .lt("trial_expiry", now.isoformat())
            .execute()
        )

        expired_count = 0
        for user in resp.data:
            try:
                db.update_user(user["id"], {"subscription_status": "expired"})
                db.deactivate_subscription(user["id"])
                expired_count += 1
            except Exception as e:
                sentry_sdk.capture_exception(e)
                logger.error(f"Failed to expire trial for user {user['id']}: {e}")

        return expired_count
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Failed to expire overdue trials: {e}")
        return 0


# ---------------------------------------------------------------------------
# Stripe Webhook
# ---------------------------------------------------------------------------


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events for subscription management."""
    body = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(body, sig, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError as e:
        logger.warning(f"Invalid Stripe webhook signature: {e}")
        return Response(status_code=400)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Stripe webhook parse error: {e}")
        return Response(status_code=400)

    event_type = event.get("type", "")
    data = event.get("data", {}).get("object", {})

    try:
        if event_type == "checkout.session.completed":
            _handle_checkout_completed(data)
        elif event_type == "invoice.payment_failed":
            _handle_payment_failed(data)
        elif event_type == "customer.subscription.deleted":
            _handle_subscription_deleted(data)
        else:
            logger.info(f"Unhandled Stripe event type: {event_type}")
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Error processing Stripe event {event_type}: {e}")

    return {"status": "ok"}


def _handle_checkout_completed(data: dict) -> None:
    """Process a successful checkout — activate the user's subscription."""
    customer_id = data.get("customer")
    customer_email = data.get("customer_details", {}).get("email")
    customer_phone = data.get("customer_details", {}).get("phone")

    # Try to find user by phone from metadata or customer details
    metadata = data.get("metadata", {})
    phone = metadata.get("phone") or customer_phone

    if not phone:
        logger.warning(f"Checkout completed but no phone found. Customer: {customer_id}")
        sentry_sdk.capture_message(
            f"Stripe checkout without phone. customer_id={customer_id}",
            level="warning",
        )
        return

    user = db.get_user_by_phone(phone)
    if not user:
        logger.warning(f"Checkout completed but user not found for phone: {phone}")
        return

    db.update_user(user["id"], {
        "subscription_status": "active",
        "stripe_customer_id": customer_id,
    })
    db.update_subscription(user["id"], {"is_active": True})

    send_text_message(
        phone,
        "Your SquawkBox subscription is now active! "
        "You'll continue receiving AI-powered stock squawks. "
        "Reply HELP for commands.",
    )


def _handle_payment_failed(data: dict) -> None:
    """Handle a failed invoice payment."""
    customer_id = data.get("customer")
    if not customer_id:
        return

    # Look up user by stripe_customer_id
    try:
        sb = db.get_supabase()
        resp = sb.table("users").select("*").eq("stripe_customer_id", customer_id).execute()
        if not resp.data:
            logger.warning(f"Payment failed for unknown customer: {customer_id}")
            return

        user = resp.data[0]
        send_text_message(
            user["phone_number"],
            "Your SquawkBox payment failed. "
            "Please update your payment method to continue receiving squawks:\n"
            f"{STRIPE_PAYMENT_LINK}\n\n"
            "If you need help, reply HELP.",
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Error handling payment failure for customer {customer_id}: {e}")


def _handle_subscription_deleted(data: dict) -> None:
    """Handle subscription cancellation from Stripe."""
    customer_id = data.get("customer")
    if not customer_id:
        return

    try:
        sb = db.get_supabase()
        resp = sb.table("users").select("*").eq("stripe_customer_id", customer_id).execute()
        if not resp.data:
            logger.warning(f"Subscription deleted for unknown customer: {customer_id}")
            return

        user = resp.data[0]
        db.update_user(user["id"], {"subscription_status": "cancelled"})
        db.deactivate_subscription(user["id"])

        send_text_message(
            user["phone_number"],
            "Your SquawkBox subscription has been cancelled. "
            "You will no longer receive squawks. "
            "Reply HELP if you'd like to resubscribe.",
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Error handling subscription deletion for customer {customer_id}: {e}")
