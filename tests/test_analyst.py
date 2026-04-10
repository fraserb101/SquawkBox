"""Tests for services/analyst.py."""

from unittest.mock import MagicMock, patch

import pytest

from services.analyst import (
    DISCLAIMER,
    _enrich_context,
    _generate_script,
    _synthesize_voice,
    process_article,
)
from services.news_service import Article
from utils.exceptions import ScriptGenerationError, TTSError


@pytest.fixture
def article():
    return Article(
        title="Apple stock surges 10% on earnings beat",
        url="http://example.com/apple-surges",
        description="Apple Inc reported quarterly earnings that beat expectations.",
        content="Apple Inc reported quarterly earnings that significantly exceeded Wall Street expectations. " * 5,
        ticker="AAPL",
    )


@pytest.fixture
def headline_article():
    return Article(
        title="AAPL up 5%",
        url="http://example.com/aapl-brief",
        description="Brief",
        content="",
        ticker="AAPL",
    )


class TestEnrichContext:
    @patch("services.analyst.TavilyClient")
    def test_skips_tavily_for_full_articles(self, mock_tavily, article):
        result = _enrich_context(article)
        mock_tavily.assert_not_called()
        assert result == article.body_text

    @patch("services.analyst.TavilyClient")
    def test_calls_tavily_for_headlines(self, mock_tavily, headline_article):
        mock_client = MagicMock()
        mock_tavily.return_value = mock_client
        mock_client.search.return_value = {
            "results": [{"content": "Extra context about AAPL"}]
        }
        result = _enrich_context(headline_article)
        mock_client.search.assert_called_once()
        assert "Extra context" in result

    @patch("services.analyst.TavilyClient")
    def test_returns_original_on_tavily_failure(self, mock_tavily, headline_article):
        mock_tavily.return_value.search.side_effect = Exception("API error")
        result = _enrich_context(headline_article)
        assert result == headline_article.body_text


class TestGenerateScript:
    @patch("services.analyst.Together")
    def test_generates_script(self, mock_together, article):
        mock_client = MagicMock()
        mock_together.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            "Breaking news on Apple. The stock surged today. " + DISCLAIMER
        )
        mock_client.chat.completions.create.return_value = mock_response

        result = _generate_script(article, "context text")
        assert DISCLAIMER in result

    @patch("services.analyst.Together")
    def test_appends_disclaimer_if_missing(self, mock_together, article):
        mock_client = MagicMock()
        mock_together.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Script without disclaimer."
        mock_client.chat.completions.create.return_value = mock_response

        result = _generate_script(article, "context")
        assert result.endswith(DISCLAIMER)

    @patch("services.analyst.Together")
    def test_truncates_long_scripts(self, mock_together, article):
        mock_client = MagicMock()
        mock_together.return_value = mock_client
        # Generate a 200-word script
        long_script = " ".join(["word"] * 200)
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = long_script
        mock_client.chat.completions.create.return_value = mock_response

        result = _generate_script(article, "context")
        words = result.split()
        # Should be truncated to 150 words + disclaimer
        assert len(words) <= 160  # 150 + disclaimer words

    @patch("services.analyst.Together")
    def test_raises_on_api_failure(self, mock_together, article):
        mock_together.return_value.chat.completions.create.side_effect = Exception("API down")
        with pytest.raises(ScriptGenerationError):
            _generate_script(article, "context")


class TestSynthesizeVoice:
    @patch("services.analyst.httpx.Client")
    def test_returns_audio_bytes(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.content = b"\x00" * 1000  # Fake audio bytes
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp

        result = _synthesize_voice("Test script")
        assert len(result) == 1000

    @patch("services.analyst.httpx.Client")
    def test_raises_on_empty_audio(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.content = b""
        mock_resp.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_resp

        with pytest.raises(TTSError, match="empty"):
            _synthesize_voice("Test script")


class TestProcessArticle:
    @patch("services.analyst._maybe_send_referral_prompt")
    @patch("services.analyst.send_voice_note")
    @patch("services.analyst.db")
    @patch("services.analyst._synthesize_voice")
    @patch("services.analyst._generate_script")
    @patch("services.analyst._enrich_context")
    def test_full_pipeline_success(
        self, mock_enrich, mock_script, mock_voice, mock_db, mock_send_voice, mock_referral, article
    ):
        mock_enrich.return_value = "context"
        mock_script.return_value = f"Script text. {DISCLAIMER}"
        mock_voice.return_value = b"audio_bytes"
        mock_db.get_users_for_ticker.return_value = [
            {"id": "u1", "phone_number": "+123", "subscription_status": "active", "notification_time": None}
        ]
        mock_db.save_squawk_log.return_value = "squawk-id"

        with patch("services.analyst.is_user_eligible_for_delivery", return_value=True):
            process_article(article)

        mock_db.save_squawk_log.assert_called_once()
        mock_db.save_squawk_delivery.assert_called_once()

    @patch("services.analyst.db")
    @patch("services.analyst._enrich_context")
    def test_no_hash_saved_on_enrichment_failure(self, mock_enrich, mock_db, article):
        mock_enrich.side_effect = Exception("Tavily down")
        process_article(article)
        mock_db.save_squawk_log.assert_not_called()

    @patch("services.analyst.db")
    @patch("services.analyst._generate_script")
    @patch("services.analyst._enrich_context")
    def test_no_hash_saved_on_script_failure(self, mock_enrich, mock_script, mock_db, article):
        mock_enrich.return_value = "context"
        mock_script.side_effect = ScriptGenerationError("AI failed")
        process_article(article)
        mock_db.save_squawk_log.assert_not_called()

    @patch("services.analyst.db")
    @patch("services.analyst._synthesize_voice")
    @patch("services.analyst._generate_script")
    @patch("services.analyst._enrich_context")
    def test_no_hash_saved_on_tts_failure(self, mock_enrich, mock_script, mock_voice, mock_db, article):
        mock_enrich.return_value = "context"
        mock_script.return_value = "script"
        mock_voice.side_effect = TTSError("Cartesia failed")
        process_article(article)
        mock_db.save_squawk_log.assert_not_called()
