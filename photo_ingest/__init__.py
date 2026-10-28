"""Photo Ingest Tool for organizing photography imports."""

__version__ = "0.1.0"
__author__ = "Photo Ingest Tool"
__description__ = "A command-line tool for organizing photography imports on macOS"

from .exceptions import (  # noqa: F401
    PhotoIngestError,
    ConfigurationError,
    MissingDependencyError,
    SourceDirectoryError,
    EXIFProcessingError,
    FileOperationError,
    ArchiveDirectoryError,
    DeduplicationError,
    format_user_error,
)