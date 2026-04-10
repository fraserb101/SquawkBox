"""Tests for services/database.py."""

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from services import database as db
from utils.exceptions import DatabaseError


@pytest.fixture(autouse=True)
def mock_supabase():
    """Mock the Supabase client for all tests."""
    mock_client = MagicMock()
    with patch.object(db, "_client", mock_client):
        with patch.object(db, "get_supabase", return_value=mock_client):
            yield mock_client


class TestGetUserByPhone:
    def test_returns_user_when_found(self, mock_supabase):
        user_data = {"id": "abc-123", "phone_number": "+1234567890"}
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [user_data]
        result = db.get_user_by_phone("+1234567890")
        assert result == user_data

    def test_returns_none_when_not_found(self, mock_supabase):
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        result = db.get_user_by_phone("+9999999999")
        assert result is None

    def test_raises_database_error_on_exception(self, mock_supabase):
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.side_effect = Exception("Connection failed")
        with pytest.raises(DatabaseError):
            db.get_user_by_phone("+1234567890")


class TestCreateUser:
    def test_creates_user_with_trial(self, mock_supabase):
        user_data = {
            "id": "new-user-id",
            "phone_number": "+1234567890",
            "referral_code": "ABC123",
            "subscription_status": "trial",
        }
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [user_data]

        result = db.create_user(phone="+1234567890")
        assert result["id"] == "new-user-id"
        assert result["subscription_status"] == "trial"

    def test_raises_on_empty_response(self, mock_supabase):
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = []
        with pytest.raises(DatabaseError, match="User insert returned no data"):
            db.create_user(phone="+1234567890")


class TestTickerOperations:
    def test_get_tickers(self, mock_supabase):
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"ticker": "AAPL"},
            {"ticker": "MSFT"},
        ]
        result = db.get_tickers_for_user("user-id")
        assert result == ["AAPL", "MSFT"]

    def test_add_ticker(self, mock_supabase):
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [
            {"id": "sub-id", "ticker": "AAPL"}
        ]
        result = db.add_ticker("user-id", "aapl")
        # Verify ticker was uppercased
        call_args = mock_supabase.table.return_value.insert.call_args
        assert call_args[0][0]["ticker"] == "AAPL"

    def test_remove_ticker_returns_true(self, mock_supabase):
        mock_supabase.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute.return_value.data = [{"id": "sub-id"}]
        result = db.remove_ticker("user-id", "AAPL")
        assert result is True

    def test_remove_ticker_returns_false_when_not_found(self, mock_supabase):
        mock_supabase.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        result = db.remove_ticker("user-id", "AAPL")
        assert result is False


class TestHashDeduplication:
    def test_compute_url_hash(self):
        url = "https://example.com/article/123"
        expected = hashlib.md5(url.encode()).hexdigest()
        assert db.compute_url_hash(url) == expected

    def test_hash_already_processed_true(self, mock_supabase):
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [{"id": "log-id"}]
        assert db.hash_already_processed("abc123") is True

    def test_hash_already_processed_false(self, mock_supabase):
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        assert db.hash_already_processed("abc123") is False


class TestSquawkLog:
    def test_save_squawk_log_returns_id(self, mock_supabase):
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [
            {"id": "squawk-id"}
        ]
        result = db.save_squawk_log("hash123", "AAPL", "delivered")
        assert result == "squawk-id"

    def test_save_squawk_log_raises_on_empty(self, mock_supabase):
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = []
        with pytest.raises(DatabaseError, match="Squawk log insert returned no data"):
            db.save_squawk_log("hash123", "AAPL", "delivered")


class TestExpiringTrials:
    def test_returns_expiring_users(self, mock_supabase):
        users = [{"id": "user-1", "trial_expiry": "2026-04-11T00:00:00+00:00"}]
        mock_supabase.table.return_value.select.return_value.eq.return_value.gte.return_value.lte.return_value.execute.return_value.data = users
        result = db.get_expiring_trials(within_hours=24)
        assert len(result) == 1


class TestReferrals:
    def test_create_referral(self, mock_supabase):
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [
            {"id": "ref-id", "referrer_id": "u1", "referred_user_id": "u2"}
        ]
        result = db.create_referral("u1", "u2")
        assert result["id"] == "ref-id"

    def test_create_referral_raises_on_empty(self, mock_supabase):
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = []
        with pytest.raises(DatabaseError):
            db.create_referral("u1", "u2")
