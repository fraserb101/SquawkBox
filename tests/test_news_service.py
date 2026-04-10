"""Tests for services/news_service.py."""

from unittest.mock import MagicMock, patch

import pytest

from services.news_service import Article, fetch_news


class TestArticle:
    def test_body_text_prefers_content(self):
        a = Article("Title", "http://x.com", "Desc", "Full content here with details", "AAPL")
        assert a.body_text == "Full content here with details"

    def test_body_text_falls_back_to_description(self):
        a = Article("Title", "http://x.com", "Short desc", "", "AAPL")
        assert a.body_text == "Short desc"

    def test_body_text_falls_back_to_title(self):
        a = Article("Title", "http://x.com", "", "", "AAPL")
        assert a.body_text == "Title"

    def test_is_headline_only_short(self):
        a = Article("Short", "http://x.com", "Brief", "", "AAPL")
        assert a.is_headline_only is True

    def test_is_headline_only_long(self):
        long_content = "x" * 250
        a = Article("Title", "http://x.com", "", long_content, "AAPL")
        assert a.is_headline_only is False

    def test_url_hash_is_md5(self):
        import hashlib
        url = "http://example.com/article"
        a = Article("T", url, "", "", "AAPL")
        assert a.url_hash == hashlib.md5(url.encode()).hexdigest()


class TestFetchNews:
    @patch("services.news_service.db")
    @patch("services.news_service.NewsDataApiClient")
    def test_returns_unprocessed_articles(self, mock_client_cls, mock_db):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.news_api.return_value = {
            "status": "success",
            "results": [
                {"title": "AAPL up 5%", "link": "http://x.com/1", "description": "Apple surged", "content": "Apple content"},
                {"title": "MSFT down", "link": "http://x.com/2", "description": "Microsoft fell", "content": ""},
            ],
        }
        mock_db.compute_url_hash.side_effect = lambda url: f"hash_{url}"
        mock_db.hash_already_processed.return_value = False

        articles = fetch_news(["AAPL"])
        assert len(articles) == 1
        assert articles[0].ticker == "AAPL"

    @patch("services.news_service.db")
    @patch("services.news_service.NewsDataApiClient")
    def test_skips_processed_articles(self, mock_client_cls, mock_db):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.news_api.return_value = {
            "status": "success",
            "results": [
                {"title": "AAPL news", "link": "http://x.com/1", "description": "Apple", "content": "content"},
            ],
        }
        mock_db.compute_url_hash.return_value = "hash1"
        mock_db.hash_already_processed.return_value = True

        articles = fetch_news(["AAPL"])
        assert len(articles) == 0

    @patch("services.news_service.NewsDataApiClient")
    def test_returns_empty_on_api_error(self, mock_client_cls):
        mock_client_cls.return_value.news_api.side_effect = Exception("API down")
        articles = fetch_news(["AAPL"])
        assert articles == []

    def test_returns_empty_for_no_tickers(self):
        assert fetch_news([]) == []
