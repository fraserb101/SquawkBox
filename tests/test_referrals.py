"""Tests for services/referrals.py."""

from unittest.mock import MagicMock, patch

import pytest

from services import referrals


@pytest.fixture(autouse=True)
def mock_db():
    with patch("services.referrals.db") as mock:
        yield mock


@pytest.fixture(autouse=True)
def mock_send():
    with patch("services.referrals.send_text_message") as mock:
        yield mock


class TestHandleStartCommand:
    def test_existing_user_gets_message(self, mock_db, mock_send):
        mock_db.get_user_by_phone.return_value = {"id": "u1"}
        referrals.handle_start_command("+123", "START_ABC123")
        mock_send.assert_called_once()
        assert "already signed up" in mock_send.call_args[0][1]

    def test_invalid_code_sends_error(self, mock_db, mock_send):
        mock_db.get_user_by_phone.return_value = None
        mock_db.get_user_by_referral_code.return_value = None
        referrals.handle_start_command("+123", "START_INVALID")
        mock_send.assert_called_once()
        assert "not valid" in mock_send.call_args[0][1]

    def test_empty_code_sends_error(self, mock_db, mock_send):
        mock_db.get_user_by_phone.return_value = None
        referrals.handle_start_command("+123", "START_")
        mock_send.assert_called_once()
        assert "Invalid signup" in mock_send.call_args[0][1]

    def test_successful_signup(self, mock_db, mock_send):
        mock_db.get_user_by_phone.return_value = None
        referrer = {"id": "referrer-id", "phone_number": "+999", "referral_code": "ABC123"}
        mock_db.get_user_by_referral_code.return_value = referrer
        new_user = {
            "id": "new-id",
            "phone_number": "+123",
            "referral_code": "XYZ789",
            "trial_expiry": "2026-04-17T00:00:00+00:00",
        }
        mock_db.create_user.return_value = new_user
        mock_db.create_referral.return_value = {"id": "ref-id"}
        mock_db.extend_trial.return_value = referrer
        mock_db.grant_referral_reward.return_value = {"id": "ref-id"}

        referrals.handle_start_command("+123", "START_ABC123")

        mock_db.create_user.assert_called_once()
        mock_db.create_referral.assert_called_once_with(
            referrer_id="referrer-id", referred_user_id="new-id"
        )
        # Should send welcome message to new user + reward notification to referrer
        assert mock_send.call_count >= 2


class TestGenerateReferralLink:
    @patch("services.referrals.YOUR_WHATSAPP_NUMBER", "15551234567")
    def test_generates_correct_link(self):
        user = {"referral_code": "ABC123"}
        link = referrals.generate_referral_link(user)
        assert link == "https://wa.me/15551234567?text=START_ABC123"
