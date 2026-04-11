"""Celery worker configuration.

Provides the Celery app instance and task definitions for background
processing. Celery beat handles recurring tasks (news polling, trial expiry).

Run worker:  celery -A celery_worker worker --loglevel=info
Run beat:    celery -A celery_worker beat --loglevel=info
"""

import logging

import sentry_sdk
from celery import Celery
from celery.schedules import crontab

from services.analyst import process_article
from services.billing import check_expiring_trials as _check_expiring_trials
from services.billing import expire_overdue_trials as _expire_overdue_trials
from services.news_service import Article, fetch_news, get_all_subscribed_tickers
from tasks.scheduled_tasks import run_scheduled_deliveries
from utils.config import REDIS_URL

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Celery App
# ---------------------------------------------------------------------------

celery_app = Celery(
    "squawkbox",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# ---------------------------------------------------------------------------
# Beat Schedule
# ---------------------------------------------------------------------------

celery_app.conf.beat_schedule = {
    "poll-news-every-15-minutes": {
        "task": "celery_worker.poll_news",
        "schedule": 900.0,  # 15 minutes
    },
    "check-expiring-trials-daily": {
        "task": "celery_worker.check_expiring_trials",
        "schedule": crontab(hour=9, minute=0),  # 09:00 UTC daily
    },
    "expire-overdue-trials-daily": {
        "task": "celery_worker.expire_overdue_trials",
        "schedule": crontab(hour=0, minute=15),  # 00:15 UTC daily
    },
    "deliver-scheduled-digests": {
        "task": "celery_worker.deliver_scheduled_digests",
        "schedule": 900.0,  # 15 minutes — matches the news poll interval
    },
}


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@celery_app.task(name="celery_worker.poll_news")
def poll_news():
    """Fetch news for all subscribed tickers and process new articles."""
    tickers = get_all_subscribed_tickers()
    if not tickers:
        logger.info("No subscribed tickers — skipping news poll")
        return

    articles = fetch_news(tickers)
    logger.info(f"News poll: {len(articles)} new articles for {len(tickers)} tickers")

    for article in articles:
        try:
            process_article(article)
        except Exception as e:
            # One article failure must not block the batch
            sentry_sdk.capture_exception(e)
            logger.error(f"Failed to process article {article.url}: {e}")


@celery_app.task(name="celery_worker.check_expiring_trials")
def check_expiring_trials():
    """Send trial expiry notifications."""
    sent = _check_expiring_trials()
    logger.info(f"Sent {sent} trial expiry notifications")


@celery_app.task(name="celery_worker.expire_overdue_trials")
def expire_overdue_trials():
    """Mark overdue trials as expired."""
    expired = _expire_overdue_trials()
    logger.info(f"Expired {expired} overdue trials")


@celery_app.task(name="celery_worker.deliver_scheduled_digests")
def deliver_scheduled_digests():
    """Deliver scheduled digests for users whose notification_time matches now."""
    run_scheduled_deliveries()


@celery_app.task(name="celery_worker.process_single_article")
def process_single_article_task(title: str, url: str, description: str, content: str, ticker: str):
    """Process a single article (can be enqueued as an async task)."""
    article = Article(
        title=title, url=url, description=description, content=content, ticker=ticker
    )
    process_article(article)
