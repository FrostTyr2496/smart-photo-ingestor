"""Custom exception hierarchy for the Photo Ingest Tool.

All domain-specific errors are subclasses of PhotoIngestError so callers
can catch the broad base type or the narrow specific type as needed.

Requirements: 8.1, 8.2, 8.3, 8.4, 8.5
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Base exception
# ---------------------------------------------------------------------------


class PhotoIngestError(Exception):
    """Base exception for all photo ingest operations.

    Provides a ``user_message`` attribute that contains an actionable,
    human-readable description suitable for display without a stack trace.
    """

    def __init__(self, message: str, user_message: Optional[str] = None):
        super().__init__(message)
        # If no user-friendly override is given, fall back to the raw message.
        self.user_message: str = user_message or message

    def __str__(self) -> str:  # pragma: no cover
        return self.args[0]


# ---------------------------------------------------------------------------
# Configuration errors  (Req 8.1, 8.2)
# ---------------------------------------------------------------------------


class ConfigurationError(PhotoIngestError):
    """Configuration validation or loading errors.

    Raised when:
    - A required config file is missing or unreadable
    - Config YAML is malformed
    - Pydantic validation fails on a config field
    """


class MissingDependencyError(ConfigurationError):
    """An external tool or library required for operation is not available.

    Example: exiftool binary missing from PATH.   (Req 8.1)
    """

    def __init__(self, dependency: str, install_hint: Optional[str] = None):
        msg = f"Required dependency not found: {dependency}"
        hint = f"  Install with: {install_hint}" if install_hint else ""
        user_msg = f"Missing dependency: {dependency}.\n{hint}".strip()
        super().__init__(msg, user_msg)
        self.dependency = dependency
        self.install_hint = install_hint


class SourceDirectoryError(ConfigurationError):
    """Source directory does not exist or is not accessible.  (Req 8.2)"""

    def __init__(self, path: Path):
        msg = f"Source directory not found: {path}"
        user_msg = (
            f"Source directory does not exist: {path}\n"
            "Please check the path and try again."
        )
        super().__init__(msg, user_msg)
        self.path = path


# ---------------------------------------------------------------------------
# EXIF processing errors
# ---------------------------------------------------------------------------


class EXIFProcessingError(PhotoIngestError):
    """EXIF extraction errors.

    Raised when metadata extraction fails for a specific file and no
    fallback is possible.  Individual file failures are typically logged
    as warnings and processing continues (Req 8.4).
    """

    def __init__(self, file_path: Path, reason: str):
        msg = f"EXIF extraction failed for {file_path}: {reason}"
        user_msg = (
            f"Could not read metadata from {file_path.name}.\n"
            f"Reason: {reason}"
        )
        super().__init__(msg, user_msg)
        self.file_path = file_path
        self.reason = reason


# ---------------------------------------------------------------------------
# File operation errors  (Req 8.3, 8.4)
# ---------------------------------------------------------------------------


class FileOperationError(PhotoIngestError):
    """File copy/move operation errors.

    Raised for individual file failures.  The pipeline continues with the
    remaining files; errors are collected and surfaced in the result.
    (Req 8.4)
    """

    def __init__(self, source: Path, dest: Optional[Path], reason: str):
        dest_str = str(dest) if dest else "<unknown destination>"
        msg = f"File operation failed for {source} -> {dest_str}: {reason}"
        user_msg = (
            f"Failed to process {source.name}.\n"
            f"Reason: {reason}\n"
            "Processing will continue with remaining files."
        )
        super().__init__(msg, user_msg)
        self.source = source
        self.dest = dest
        self.reason = reason


class ArchiveDirectoryError(FileOperationError):
    """Archive output directory cannot be created.  (Req 8.3)"""

    def __init__(self, directory: Path, reason: str):
        super().__init__(
            source=directory,
            dest=None,
            reason=reason,
        )
        # Override with a more specific user message
        self.user_message = (
            f"Cannot create archive directory: {directory}\n"
            f"Reason: {reason}\n"
            "Check that you have write permission and sufficient disk space."
        )
        self.directory = directory


# ---------------------------------------------------------------------------
# Deduplication errors
# ---------------------------------------------------------------------------


class DeduplicationError(PhotoIngestError):
    """Deduplication database errors.

    Raised when the dedup database cannot be initialised, read, or written.
    Individual look-up failures are logged as warnings and treated as NEW.
    """

    def __init__(self, reason: str, db_path: Optional[Path] = None):
        path_info = f" (db: {db_path})" if db_path else ""
        msg = f"Deduplication error{path_info}: {reason}"
        user_msg = (
            f"Deduplication database error: {reason}\n"
            "The ingest will continue but duplicate detection may be impaired."
        )
        super().__init__(msg, user_msg)
        self.db_path = db_path
        self.reason = reason


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def format_user_error(exc: Exception) -> str:
    """Return a user-friendly, actionable error message for *any* exception.

    For known ``PhotoIngestError`` subclasses the ``user_message`` attribute
    is returned.  For unexpected exceptions a generic fallback is used so
    that raw Python tracebacks are never shown directly to end users.
    (Req 8.5)
    """
    if isinstance(exc, PhotoIngestError):
        return exc.user_message
    # Generic fallback – avoids leaking technical details
    return (
        f"An unexpected error occurred: {type(exc).__name__}: {exc}\n"
        "If this persists, run with --verbose for diagnostic details."
    )
