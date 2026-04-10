"""Audio conversion utility for WhatsApp voice notes.

WhatsApp voice notes require .ogg container with libopus codec.
Any other format will silently fail to play.
"""

import os
import subprocess
import tempfile

from utils.exceptions import AudioConversionError


def convert_to_ogg_opus(audio_bytes: bytes, input_format: str = "wav") -> bytes:
    """Convert audio bytes to .ogg with libopus codec for WhatsApp compatibility.

    Args:
        audio_bytes: Raw audio data from TTS provider.
        input_format: Format of the input audio (e.g. 'wav', 'mp3', 'raw').

    Returns:
        Bytes of the converted .ogg/opus audio file.

    Raises:
        AudioConversionError: If FFmpeg conversion fails or output is empty.
    """
    with tempfile.NamedTemporaryFile(suffix=f".{input_format}", delete=False) as infile:
        infile.write(audio_bytes)
        input_path = infile.name

    output_path = input_path.rsplit(".", 1)[0] + ".ogg"

    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i", input_path,
                "-c:a", "libopus",
                "-b:a", "64k",
                "-vn",
                "-f", "ogg",
                output_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            raise AudioConversionError(
                f"FFmpeg conversion failed (exit code {result.returncode}): {result.stderr}"
            )

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise AudioConversionError(
                "FFmpeg produced an empty or missing output file"
            )

        with open(output_path, "rb") as f:
            return f.read()

    except subprocess.TimeoutExpired:
        raise AudioConversionError("FFmpeg conversion timed out after 30 seconds")
    except AudioConversionError:
        raise
    except Exception as e:
        raise AudioConversionError(f"Unexpected error during audio conversion: {e}")
    finally:
        for path in (input_path, output_path):
            try:
                os.unlink(path)
            except OSError:
                pass
