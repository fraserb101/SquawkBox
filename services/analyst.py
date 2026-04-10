"""Analyst Pipeline — the core AI engine.

Takes a news article through the full pipeline:
1. Context enrichment (conditional Tavily research)
2. Script generation (Together AI / Llama 3)
3. Voice synthesis (Cartesia)
4. Delivery to subscribed users
5. Logging (only on full success)

CRITICAL: news_url_hash is only saved to squawk_logs if the ENTIRE pipeline
succeeds. If any step fails, the hash is NOT saved, allowing retry on the
next poll cycle.
"""

import logging
from datetime import datetime, timezone

import httpx
import sentry_sdk
from tavily import TavilyClient
from together import Together

from services import database as db
from services.billing import is_user_eligible_for_delivery
from services.news_service import Article
from services.referrals import send_referral_prompt
from services.whatsapp import send_voice_note
from utils.config import (
    CARTESIA_API_KEY,
    CARTESIA_VOICE_ID,
    TAVILY_API_KEY,
    TOGETHER_API_KEY,
)
from utils.exceptions import DeliveryError, ScriptGenerationError, TTSError

logger = logging.getLogger(__name__)

DISCLAIMER = "This is not financial advice."
MAX_SCRIPT_WORDS = 150
TRUNCATION_THRESHOLD = 160

SYSTEM_PROMPT = (
    "You are a high-energy financial news reporter delivering a concise voice note. "
    "Your style is urgent, professional, and engaging — like a breaking news anchor. "
    "STRICT RULES:\n"
    f"1. Your script MUST be {MAX_SCRIPT_WORDS} words or fewer. This is a hard limit.\n"
    "2. Get straight to the point — no filler, no preamble.\n"
    "3. Cover: what happened, why it matters, potential market impact.\n"
    f'4. You MUST end with exactly: "{DISCLAIMER}"\n'
    "5. Do not use hashtags, emojis, or markdown formatting.\n"
    "6. Write as spoken word — this will be read aloud."
)


def process_article(article: Article) -> None:
    """Run the full analyst pipeline for a single article.

    If any step fails, logs the error to Sentry and returns without
    saving the URL hash — allowing retry on the next poll cycle.
    """
    ticker = article.ticker
    url = article.url
    url_hash = article.url_hash

    logger.info(f"Processing article: {article.title} (ticker={ticker})")

    # Step 1: Context enrichment (conditional)
    try:
        context = _enrich_context(article)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Context enrichment failed for {url}: {e}")
        return  # Do NOT save hash

    # Step 2: Script generation
    try:
        script = _generate_script(article, context)
    except (ScriptGenerationError, Exception) as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Script generation failed for {url}: {e}")
        return  # Do NOT save hash

    # Step 3: Voice synthesis
    try:
        audio_bytes = _synthesize_voice(script)
    except (TTSError, Exception) as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Voice synthesis failed for {url}: {e}")
        return  # Do NOT save hash

    # Step 4: Deliver to all subscribed users
    try:
        recipients = _deliver_to_users(ticker, audio_bytes)
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Delivery orchestration failed for {url}: {e}")
        return  # Do NOT save hash

    if not recipients:
        logger.info(f"No eligible recipients for {ticker} — skipping hash save")
        return

    # Step 5: Log success — ONLY now save the hash
    try:
        squawk_id = db.save_squawk_log(url_hash, ticker, status="delivered")

        for user_id in recipients:
            try:
                db.save_squawk_delivery(squawk_id, user_id)
            except Exception as e:
                sentry_sdk.capture_exception(e)
                logger.error(f"Failed to save delivery record for user {user_id}: {e}")

        logger.info(
            f"Pipeline complete for {ticker}: {article.title} "
            f"— delivered to {len(recipients)} users"
        )
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Failed to save squawk log for {url}: {e}")
        # Hash not saved — article will be retried


# ---------------------------------------------------------------------------
# Step 1: Context Enrichment
# ---------------------------------------------------------------------------


def _enrich_context(article: Article) -> str:
    """Conditionally call Tavily for short/headline-only articles.

    Only invoked when the article body is < 200 characters to save cost.
    """
    if not article.is_headline_only:
        return article.body_text

    try:
        client = TavilyClient(api_key=TAVILY_API_KEY)
        query = f"{article.title} financial impact context"
        response = client.search(query=query, max_results=3)
        results = response.get("results", [])

        enriched_parts = [article.body_text]
        for result in results:
            content = result.get("content", "")
            if content:
                enriched_parts.append(content)

        combined = "\n\n".join(enriched_parts)
        logger.info(f"Tavily enriched context for: {article.title}")
        return combined
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.warning(f"Tavily enrichment failed, using original text: {e}")
        return article.body_text


# ---------------------------------------------------------------------------
# Step 2: Script Generation
# ---------------------------------------------------------------------------


def _generate_script(article: Article, context: str) -> str:
    """Generate a voice-note script using Together AI (Llama 3).

    Enforces the 150-word limit and ensures the disclaimer is present.
    """
    user_prompt = (
        f"Write a voice note script about this financial news:\n\n"
        f"Headline: {article.title}\n"
        f"Ticker: {article.ticker}\n\n"
        f"Context:\n{context[:3000]}"  # Cap context to avoid token overflow
    )

    try:
        client = Together(api_key=TOGETHER_API_KEY)
        response = client.chat.completions.create(
            model="meta-llama/Llama-3-70b-chat-hf",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=400,
            temperature=0.7,
        )

        script = response.choices[0].message.content.strip()
    except Exception as e:
        raise ScriptGenerationError(f"Together AI call failed: {e}")

    # Enforce word limit
    words = script.split()
    if len(words) > TRUNCATION_THRESHOLD:
        sentry_sdk.capture_message(
            f"Script exceeded {TRUNCATION_THRESHOLD} words ({len(words)} words) "
            f"for article: {article.title}",
            level="warning",
        )
        # Truncate to MAX_SCRIPT_WORDS and re-append disclaimer
        words = words[:MAX_SCRIPT_WORDS]
        script = " ".join(words)
        if not script.rstrip(".").endswith(DISCLAIMER.rstrip(".")):
            script = script.rstrip() + " " + DISCLAIMER

    # Ensure disclaimer is present
    if DISCLAIMER not in script:
        script = script.rstrip() + " " + DISCLAIMER

    return script


# ---------------------------------------------------------------------------
# Step 3: Voice Synthesis
# ---------------------------------------------------------------------------


def _synthesize_voice(script: str) -> bytes:
    """Convert script text to audio bytes using Cartesia TTS.

    Returns raw audio bytes (WAV format from Cartesia).
    Raises TTSError on failure.
    """
    try:
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                "https://api.cartesia.ai/tts/bytes",
                headers={
                    "X-API-Key": CARTESIA_API_KEY,
                    "Cartesia-Version": "2024-06-10",
                    "Content-Type": "application/json",
                },
                json={
                    "model_id": "sonic-2",
                    "transcript": script,
                    "voice": {"mode": "id", "id": CARTESIA_VOICE_ID},
                    "output_format": {
                        "container": "wav",
                        "encoding": "pcm_s16le",
                        "sample_rate": 24000,
                    },
                },
            )
            resp.raise_for_status()
            audio_bytes = resp.content

            if not audio_bytes or len(audio_bytes) < 100:
                raise TTSError("Cartesia returned empty or too-small audio")

            return audio_bytes
    except httpx.HTTPStatusError as e:
        raise TTSError(
            f"Cartesia TTS failed ({e.response.status_code}): {e.response.text}"
        )
    except TTSError:
        raise
    except Exception as e:
        raise TTSError(f"Cartesia TTS error: {e}")


# ---------------------------------------------------------------------------
# Step 4: Delivery
# ---------------------------------------------------------------------------


def _deliver_to_users(ticker: str, audio_bytes: bytes) -> list[str]:
    """Deliver voice note to all eligible users subscribed to this ticker.

    Returns a list of user IDs that were successfully delivered to.
    One user's failure does not block other users.
    """
    users = db.get_users_for_ticker(ticker)
    delivered_user_ids = []

    for user in users:
        if not is_user_eligible_for_delivery(user):
            continue

        user_id = user["id"]
        phone = user["phone_number"]
        notification_time = user.get("notification_time")

        # If the user has a scheduled notification time, skip real-time delivery.
        # The scheduled task system will handle these users.
        if notification_time:
            continue

        try:
            send_voice_note(phone, audio_bytes, input_format="wav")
            delivered_user_ids.append(user_id)

            # After first successful delivery, send referral prompt
            _maybe_send_referral_prompt(user)

        except (DeliveryError, Exception) as e:
            # One user's failure must not block others
            sentry_sdk.capture_exception(e)
            logger.error(f"Delivery failed for user {user_id} ({phone}): {e}")

    return delivered_user_ids


def _maybe_send_referral_prompt(user: dict) -> None:
    """Send a referral prompt after the user's first squawk delivery."""
    try:
        history = db.get_user_delivery_history(user["id"], limit=2)
        # If this is the first delivery (history has 0 or 1 entries before this one)
        if len(history) <= 1:
            send_referral_prompt(user)
    except Exception as e:
        # Non-critical — don't let this block delivery
        logger.debug(f"Referral prompt check failed for {user['id']}: {e}")
