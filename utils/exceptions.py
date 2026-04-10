"""Typed exceptions for the Squawk Engine pipeline."""


class SquawkEngineError(Exception):
    """Base exception for all Squawk Engine errors."""


class AudioConversionError(SquawkEngineError):
    """Raised when FFmpeg audio conversion fails."""


class TTSError(SquawkEngineError):
    """Raised when Cartesia text-to-speech fails."""


class DeliveryError(SquawkEngineError):
    """Raised when WhatsApp message delivery fails."""


class DatabaseError(SquawkEngineError):
    """Raised when a database operation fails."""


class NewsServiceError(SquawkEngineError):
    """Raised when the news fetching service fails."""


class ScriptGenerationError(SquawkEngineError):
    """Raised when AI script generation fails."""
