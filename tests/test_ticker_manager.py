"""Tests for services/ticker_manager.py."""

from unittest.mock import MagicMock, patch

import pytest

from services import ticker_manager


@pytest.fixture(autouse=True)
def mock_db():
    with patch("services.ticker_manager.db") as mock:
        yield mock


@pytest.fixture(autouse=True)
def mock_send():
    with patch("services.ticker_manager.send_text_message") as mock:
        yield mock


class TestIsValidTicker:
    def test_valid_tickers(self):
        assert ticker_manager._is_valid_ticker("AAPL") is True
        assert ticker_manager._is_valid_ticker("A") is True
        assert ticker_manager._is_valid_ticker("ABCDE") is True

    def test_invalid_tickers(self):
        assert ticker_manager._is_valid_ticker("") is False
        assert ticker_manager._is_valid_ticker("ABCDEF") is False  # 6 chars
        assert ticker_manager._is_valid_ticker("aapl") is False  # lowercase
        assert ticker_manager._is_valid_ticker("AA1") is False  # has digit
        assert ticker_manager._is_valid_ticker("AA PL") is False  # has space


class TestHandleCommand:
    def test_stop_handled_first(self, mock_db, mock_send):
        user = {"id": "u1", "phone_number": "+123", "subscription_status": "active"}
        mock_db.get_user_by_phone.return_value = user
        ticker_manager.handle_command("+123", "STOP")
        mock_db.update_user.assert_called_once()
        mock_db.deactivate_subscription.assert_called_once()

    def test_stop_for_unknown_user(self, mock_db, mock_send):
        mock_db.get_user_by_phone.return_value = None
        ticker_manager.handle_command("+123", "STOP")
        mock_send.assert_called_once()
        assert "not currently subscribed" in mock_send.call_args[0][1]

    def test_help_command(self, mock_db, mock_send):
        mock_db.get_user_by_phone.return_value = None  # HELP doesn't need user
        ticker_manager.handle_command("+123", "HELP")
        mock_send.assert_called_once()
        assert "Commands" in mock_send.call_args[0][1]

    def test_unknown_user_prompted_to_sign_up(self, mock_db, mock_send):
        mock_db.get_user_by_phone.return_value = None
        ticker_manager.handle_command("+123", "ADD AAPL")
        mock_send.assert_called_once()
        assert "sign up" in mock_send.call_args[0][1].lower()

    def test_add_ticker(self, mock_db, mock_send):
        user = {"id": "u1", "phone_number": "+123", "subscription_status": "trial"}
        mock_db.get_user_by_phone.return_value = user
        mock_db.get_ticker_count_for_user.return_value = 2
        mock_db.add_ticker.return_value = {"id": "sub-id"}
        ticker_manager.handle_command("+123", "ADD AAPL")
        mock_db.add_ticker.assert_called_once_with("u1", "AAPL")
        assert "Added" in mock_send.call_args[0][1]

    def test_add_invalid_ticker(self, mock_db, mock_send):
        user = {"id": "u1", "phone_number": "+123", "subscription_status": "trial"}
        mock_db.get_user_by_phone.return_value = user
        ticker_manager.handle_command("+123", "ADD 123")
        assert "not a valid ticker" in mock_send.call_args[0][1]

    def test_add_ticker_at_trial_limit(self, mock_db, mock_send):
        user = {"id": "u1", "phone_number": "+123", "subscription_status": "trial"}
        mock_db.get_user_by_phone.return_value = user
        mock_db.get_ticker_count_for_user.return_value = 10  # At trial limit
        ticker_manager.handle_command("+123", "ADD AAPL")
        assert "limit" in mock_send.call_args[0][1].lower()

    def test_remove_ticker(self, mock_db, mock_send):
        user = {"id": "u1", "phone_number": "+123", "subscription_status": "trial"}
        mock_db.get_user_by_phone.return_value = user
        mock_db.remove_ticker.return_value = True
        ticker_manager.handle_command("+123", "REMOVE AAPL")
        assert "Removed" in mock_send.call_args[0][1]

    def test_list_tickers(self, mock_db, mock_send):
        user = {"id": "u1", "phone_number": "+123", "subscription_status": "trial"}
        mock_db.get_user_by_phone.return_value = user
        mock_db.get_tickers_for_user.return_value = ["AAPL", "MSFT"]
        ticker_manager.handle_command("+123", "LIST")
        msg = mock_send.call_args[0][1]
        assert "AAPL" in msg
        assert "MSFT" in msg

    def test_schedule_set(self, mock_db, mock_send):
        user = {"id": "u1", "phone_number": "+123", "subscription_status": "trial"}
        mock_db.get_user_by_phone.return_value = user
        mock_db.set_notification_schedule.return_value = user
        ticker_manager.handle_command("+123", "SCHEDULE 08:00 Europe/London")
        mock_db.set_notification_schedule.assert_called_once_with("u1", "08:00", "Europe/London")

    def test_schedule_off(self, mock_db, mock_send):
        user = {"id": "u1", "phone_number": "+123", "subscription_status": "trial"}
        mock_db.get_user_by_phone.return_value = user
        mock_db.set_notification_schedule.return_value = user
        ticker_manager.handle_command("+123", "SCHEDULE OFF")
        mock_db.set_notification_schedule.assert_called_once_with("u1", None, None)

    def test_schedule_invalid_timezone(self, mock_db, mock_send):
        user = {"id": "u1", "phone_number": "+123", "subscription_status": "trial"}
        mock_db.get_user_by_phone.return_value = user
        ticker_manager.handle_command("+123", "SCHEDULE 08:00 Mars/Olympus")
        assert "Unknown timezone" in mock_send.call_args[0][1]

    def test_unknown_command(self, mock_db, mock_send):
        user = {"id": "u1", "phone_number": "+123", "subscription_status": "trial"}
        mock_db.get_user_by_phone.return_value = user
        ticker_manager.handle_command("+123", "FOOBAR")
        assert "Unknown command" in mock_send.call_args[0][1]
