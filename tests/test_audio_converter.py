"""Tests for utils/audio_converter.py."""

import subprocess
from unittest.mock import MagicMock, mock_open, patch

import pytest

from utils.audio_converter import convert_to_ogg_opus
from utils.exceptions import AudioConversionError


class TestConvertToOggOpus:
    @patch("utils.audio_converter.os.unlink")
    @patch("builtins.open", mock_open(read_data=b"ogg_audio_bytes"))
    @patch("utils.audio_converter.os.path.getsize", return_value=1000)
    @patch("utils.audio_converter.os.path.exists", return_value=True)
    @patch("utils.audio_converter.subprocess.run")
    @patch("utils.audio_converter.tempfile.NamedTemporaryFile")
    def test_successful_conversion(self, mock_tmp, mock_run, mock_exists, mock_size, mock_unlink):
        mock_tmp_file = MagicMock()
        mock_tmp_file.__enter__ = MagicMock(return_value=mock_tmp_file)
        mock_tmp_file.__exit__ = MagicMock(return_value=False)
        mock_tmp_file.name = "/tmp/test.wav"
        mock_tmp.return_value = mock_tmp_file

        mock_run.return_value = MagicMock(returncode=0, stderr="")

        result = convert_to_ogg_opus(b"input_audio")
        assert result == b"ogg_audio_bytes"
        mock_run.assert_called_once()
        # Check FFmpeg was called with libopus
        call_args = mock_run.call_args[0][0]
        assert "libopus" in call_args

    @patch("utils.audio_converter.os.unlink")
    @patch("utils.audio_converter.subprocess.run")
    @patch("utils.audio_converter.tempfile.NamedTemporaryFile")
    def test_ffmpeg_failure_raises(self, mock_tmp, mock_run, mock_unlink):
        mock_tmp_file = MagicMock()
        mock_tmp_file.__enter__ = MagicMock(return_value=mock_tmp_file)
        mock_tmp_file.__exit__ = MagicMock(return_value=False)
        mock_tmp_file.name = "/tmp/test.wav"
        mock_tmp.return_value = mock_tmp_file

        mock_run.return_value = MagicMock(returncode=1, stderr="codec not found")

        with pytest.raises(AudioConversionError, match="FFmpeg conversion failed"):
            convert_to_ogg_opus(b"input_audio")

    @patch("utils.audio_converter.os.unlink")
    @patch("utils.audio_converter.subprocess.run")
    @patch("utils.audio_converter.tempfile.NamedTemporaryFile")
    def test_timeout_raises(self, mock_tmp, mock_run, mock_unlink):
        mock_tmp_file = MagicMock()
        mock_tmp_file.__enter__ = MagicMock(return_value=mock_tmp_file)
        mock_tmp_file.__exit__ = MagicMock(return_value=False)
        mock_tmp_file.name = "/tmp/test.wav"
        mock_tmp.return_value = mock_tmp_file

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ffmpeg", timeout=30)

        with pytest.raises(AudioConversionError, match="timed out"):
            convert_to_ogg_opus(b"input_audio")
