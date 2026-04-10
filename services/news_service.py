"""News Service — fetch and deduplicate financial news.

Uses NewsData.io to fetch business news, filters by subscribed tickers,
and deduplicates via URL hashing against squawk_logs.
"""

import logging

import sentry_sdk
from newsdataapi import NewsDataApiClient

from services import database as db
from utils.config import NEWSDATA_API_KEY

logger = logging.getLogger(__name__)


class Article:
    """Lightweight container for a news article."""

    def __init__(self, title: str, url: str, description: str, content: str, ticker: str):
        self.title = title
        self.url = url
        self.description = description or ""
        self.content = content or ""
        self.ticker = ticker
        self.url_hash = db.compute_url_hash(url)

    @property
    def body_text(self) -> str:
        """Return the best available body text for this article."""
        return self.content or self.description or self.title

    @property
    def is_headline_only(self) -> bool:
        """True if the article body is < 200 characters (headline-only)."""
        return len(self.body_text) < 200


def fetch_news(tickers: list[str]) -> list[Article]:
    """Fetch unprocessed news articles relevant to the given tickers.

    Queries NewsData.io for business news in English, filters for articles
    mentioning at least one of the provided tickers, and skips articles
    that have already been processed (by URL hash).

    Returns only unprocessed articles. On API error, returns an empty list
    (the poller should continue to the next cycle).
    """
    if not tickers:
        return []

    try:
        client = NewsDataApiClient(apikey=NEWSDATA_API_KEY)

        # Query with all tickers joined as keywords
        query = " OR ".join(tickers)
        response = client.news_api(
            q=query,
            category="business",
            language="en",
        )

        if response.get("status") != "success":
            logger.warning(f"NewsData.io returned non-success status: {response.get('status')}")
            return []

        results = response.get("results", [])
        if not results:
            return []

        articles = []
        ticker_set = {t.upper() for t in tickers}

        for item in results:
            title = item.get("title", "")
            url = item.get("link", "")
            description = item.get("description", "")
            content = item.get("content", "")

            if not url or not title:
                continue

            # Find which ticker(s) this article matches
            combined_text = f"{title} {description} {content}".upper()
            matched_ticker = None
            for ticker in ticker_set:
                if ticker in combined_text:
                    matched_ticker = ticker
                    break

            if not matched_ticker:
                continue

            # Deduplication: skip if already processed
            url_hash = db.compute_url_hash(url)
            try:
                if db.hash_already_processed(url_hash):
                    continue
            except Exception as e:
                sentry_sdk.capture_exception(e)
                logger.error(f"Hash check failed for {url}: {e}")
                continue

            articles.append(Article(
                title=title,
                url=url,
                description=description,
                content=content,
                ticker=matched_ticker,
            ))

        logger.info(f"Fetched {len(articles)} unprocessed articles for {len(tickers)} tickers")
        return articles

    except Exception as e:
        # On any error, log and return empty — the poller should continue
        sentry_sdk.capture_exception(e)
        logger.error(f"NewsData.io fetch failed: {e}")
        return []


def get_all_subscribed_tickers() -> list[str]:
    """Get a deduplicated list of all tickers any active user is subscribed to.

    Used by the poller to know which tickers to query news for.
    """
    try:
        sb = db.get_supabase()
        resp = sb.table("ticker_subscriptions").select("ticker").execute()
        return list({row["ticker"] for row in resp.data})
    except Exception as e:
        sentry_sdk.capture_exception(e)
        logger.error(f"Failed to get all subscribed tickers: {e}")
        return []
