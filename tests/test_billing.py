"""Tests for services/billing.py."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from services import billing


class TestIsUserEligibleForDelivery:
    def test_active_user_eligible(self):
        user = {"subscription_status": "active"}
        assert billing.is_user_eligible_for_delivery(user) is True

    def test_trial_user_not_expired(self):
        future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        user = {"subscription_status": "trial", "trial_expiry": future}
        assert billing.is_user_eligible_for_delivery(user) is True

    def test_trial_user_expired(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        user = {"subscription_status": "trial", "trial_expiry": past}
        assert billing.is_user_eligible_for_delivery(user) is False

    def test_trial_user_no_expiry(self):
        user = {"subscription_status": "trial", "trial_expiry": None}
        assert billing.is_user_eligible_for_delivery(user) is False

    def test_expired_user_not_eligible(self):
        user = {"subscription_status": "expired"}
        assert billing.is_user_eligible_for_delivery(user) is False

    def test_cancelled_user_not_eligible(self):
        user = {"subscription_status": "cancelled"}
        assert billing.is_user_eligible_for_delivery(user) is False

    def test_unknown_status_not_eligible(self):
        user = {"subscription_status": "something_else"}
        assert billing.is_user_eligible_for_delivery(user) is False


class TestCheckExpiringTrials:
    @patch("services.billing.send_text_message")
    @patch("services.billing.db")
    def test_sends_notifications(self, mock_db, mock_send):
        expiring_users = [
            {
                "id": "u1",
                "phone_number": "+123",
                "trial_expiry": (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat(),
            },
        ]
        mock_db.get_expiring_trials.return_value = expiring_users
        result = billing.check_expiring_trials()
        assert result == 1
        mock_send.assert_called_once()

    @patch("services.billing.send_text_message")
    @patch("services.billing.db")
    def test_handles_db_error(self, mock_db, mock_send):
        from utils.exceptions import DatabaseError
        mock_db.get_expiring_trials.side_effect = DatabaseError("fail")
        result = billing.check_expiring_trials()
        assert result == 0

    @patch("services.billing.send_text_message")
    @patch("services.billing.db")
    def test_one_failure_doesnt_block_others(self, mock_db, mock_send):
        expiring_users = [
            {"id": "u1", "phone_number": "+111", "trial_expiry": (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()},
            {"id": "u2", "phone_number": "+222", "trial_expiry": (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()},
        ]
        mock_db.get_expiring_trials.return_value = expiring_users
        mock_send.side_effect = [Exception("network error"), None]
        result = billing.check_expiring_trials()
        assert result == 1  # Second user still got notified
