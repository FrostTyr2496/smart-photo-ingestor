"""Output file generation: manifest.csv, Summary.md, Notes.md (Req 6.1–6.5)."""

import csv
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ManifestRecord:
    """One row in manifest.csv."""
    source_path: str
    dest_path: str
    raw_backup_path: str
    sha256_hash: str
    file_size: int
    camera_make: str
    camera_model: str
    lens_model: str
    capture_date: str
    iso: str
    aperture: str
    shutter_speed: str
    device_code: str
    operation_type: str
    duplicate_status: str


@dataclass
class IngestSummaryData:
    """Aggregated stats passed to Summary.md generator."""
    event_name: str
    event_date: str
    archive_root: str

    total_files: int = 0
    files_copied: int = 0
    files_moved: int = 0
    files_skipped: int = 0
    duplicates_found: int = 0
    total_size_bytes: int = 0

    file_counts_by_type: Dict[str, int] = field(default_factory=dict)
    cameras: Dict[str, int] = field(default_factory=dict)
    lenses: Dict[str, int] = field(default_factory=dict)
    date_range: Optional[tuple] = None          # (min_date_str, max_date_str)
    iso_range: Optional[tuple] = None           # (min_int, max_int)
    aperture_range: Optional[tuple] = None      # (min_float, max_float)
    focal_length_range: Optional[tuple] = None  # (min_float, max_float)
    files_with_gps: int = 0
    errors: List[str] = field(default_factory=list)
    raw_backup_directory: Optional[str] = None
    processing_time_seconds: float = 0.0


@dataclass
class OutputFiles:
    """Paths to files that were (or would be) created."""
    manifest_path: Optional[Path] = None
    summary_path: Optional[Path] = None
    notes_path: Optional[Path] = None
    errors: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# OutputGenerator
# ---------------------------------------------------------------------------

class OutputGenerator:
    """Creates manifest.csv, Summary.md, and Notes.md in the event directory."""

    def __init__(self, event_dir: Path):
        """
        Args:
            event_dir: The event root directory, e.g.
                       ``/archive/2024/2024-07-15_Shoot/``
                       Files are placed directly here, not in camera sub-dirs.
        """
        self.event_dir = event_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_all(
        self,
        records: List[ManifestRecord],
        summary_data: IngestSummaryData,
        notes_content: Optional[str] = None,
        dry_run: bool = False,
    ) -> OutputFiles:
        """Generate all output files.

        Args:
            records: One ManifestRecord per processed file.
            summary_data: Aggregated statistics for Summary.md.
            notes_content: Pre-generated LLM text, or None to skip Notes.md.
            dry_run: When True, return planned paths without writing anything.

        Returns:
            OutputFiles with paths (and any errors encountered).
        """
        result = OutputFiles()

        manifest_path = self.event_dir / "manifest.csv"
        summary_path = self.event_dir / "Summary.md"
        notes_path = self.event_dir / "Notes.md" if notes_content else None

        if dry_run:
            result.manifest_path = manifest_path
            result.summary_path = summary_path
            result.notes_path = notes_path
            return result

        result.manifest_path = self._write_manifest(manifest_path, records, result)
        result.summary_path = self._write_summary(summary_path, summary_data, result)
        if notes_content:
            result.notes_path = self._write_notes(notes_path, notes_content, result)

        return result

    # ------------------------------------------------------------------
    # manifest.csv
    # ------------------------------------------------------------------

    def write_manifest(
        self,
        records: List[ManifestRecord],
        dry_run: bool = False,
    ) -> Optional[Path]:
        """Write manifest.csv; returns path on success, None on failure."""
        path = self.event_dir / "manifest.csv"
        if dry_run:
            return path
        errors: List[str] = []
        return self._write_manifest(path, records, _ErrorAccumulator(errors))

    def _write_manifest(
        self,
        path: Path,
        records: List[ManifestRecord],
        errors: "_ErrorAccumulator",
    ) -> Optional[Path]:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow([
                    "source_path", "dest_path", "raw_backup_path",
                    "sha256_hash", "file_size",
                    "camera_make", "camera_model", "lens_model",
                    "capture_date", "iso", "aperture", "shutter_speed",
                    "device_code", "operation_type", "duplicate_status",
                ])
                for rec in records:
                    writer.writerow([
                        rec.source_path, rec.dest_path, rec.raw_backup_path,
                        rec.sha256_hash, rec.file_size,
                        rec.camera_make, rec.camera_model, rec.lens_model,
                        rec.capture_date, rec.iso, rec.aperture, rec.shutter_speed,
                        rec.device_code, rec.operation_type, rec.duplicate_status,
                    ])
            logger.info("Wrote manifest: %s (%d records)", path, len(records))
            return path
        except Exception as exc:
            msg = f"Failed to write manifest.csv: {exc}"
            logger.error(msg)
            errors.append(msg)
            return None

    # ------------------------------------------------------------------
    # Summary.md
    # ------------------------------------------------------------------

    def write_summary(
        self,
        summary_data: IngestSummaryData,
        dry_run: bool = False,
    ) -> Optional[Path]:
        """Write Summary.md; returns path on success, None on failure."""
        path = self.event_dir / "Summary.md"
        if dry_run:
            return path
        errors: List[str] = []
        return self._write_summary(path, summary_data, _ErrorAccumulator(errors))

    def _write_summary(
        self,
        path: Path,
        d: IngestSummaryData,
        errors: "_ErrorAccumulator",
    ) -> Optional[Path]:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            content = self._build_summary_markdown(d)
            path.write_text(content, encoding="utf-8")
            logger.info("Wrote summary: %s", path)
            return path
        except Exception as exc:
            msg = f"Failed to write Summary.md: {exc}"
            logger.error(msg)
            errors.append(msg)
            return None

    @staticmethod
    def _build_summary_markdown(d: IngestSummaryData) -> str:
        def _fmt_size(n: int) -> str:
            for unit in ("B", "KB", "MB", "GB", "TB"):
                if n < 1024:
                    return f"{n:.1f} {unit}"
                n /= 1024
            return f"{n:.1f} PB"

        lines: List[str] = []
        lines.append(f"# Ingest Summary — {d.event_name}")
        lines.append("")
        lines.append(f"**Date:** {d.event_date}  ")
        lines.append(f"**Archive:** `{d.archive_root}`  ")
        if d.processing_time_seconds:
            lines.append(f"**Processing time:** {d.processing_time_seconds:.1f}s  ")
        lines.append("")

        # File counts
        lines.append("## Files")
        lines.append("")
        lines.append(f"| Metric | Count |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Total files processed | {d.total_files:,} |")
        if d.files_copied:
            lines.append(f"| Files copied | {d.files_copied:,} |")
        if d.files_moved:
            lines.append(f"| Files moved | {d.files_moved:,} |")
        if d.duplicates_found:
            lines.append(f"| Duplicates skipped | {d.duplicates_found:,} |")
        if d.files_skipped:
            lines.append(f"| Files skipped | {d.files_skipped:,} |")
        lines.append(f"| Total size | {_fmt_size(d.total_size_bytes)} |")
        lines.append("")

        if d.file_counts_by_type:
            lines.append("### By Type")
            lines.append("")
            lines.append("| Type | Count |")
            lines.append("|------|-------|")
            for ftype, count in sorted(d.file_counts_by_type.items()):
                lines.append(f"| {ftype.upper()} | {count:,} |")
            lines.append("")

        # Date range
        if d.date_range:
            lines.append("## Date Range")
            lines.append("")
            lines.append(f"**From:** {d.date_range[0]}  ")
            lines.append(f"**To:** {d.date_range[1]}  ")
            lines.append("")

        # Equipment
        if d.cameras:
            lines.append("## Cameras")
            lines.append("")
            lines.append("| Camera | Files |")
            lines.append("|--------|-------|")
            for cam, cnt in sorted(d.cameras.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"| {cam} | {cnt:,} |")
            lines.append("")

        if d.lenses:
            lines.append("## Lenses")
            lines.append("")
            lines.append("| Lens | Files |")
            lines.append("|------|-------|")
            for lens, cnt in sorted(d.lenses.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"| {lens} | {cnt:,} |")
            lines.append("")

        # Technical ranges
        any_tech = d.iso_range or d.aperture_range or d.focal_length_range
        if any_tech:
            lines.append("## Technical Settings")
            lines.append("")
            if d.iso_range:
                lines.append(f"- **ISO:** {d.iso_range[0]} – {d.iso_range[1]}")
            if d.aperture_range:
                lines.append(f"- **Aperture:** f/{d.aperture_range[0]:.1f} – f/{d.aperture_range[1]:.1f}")
            if d.focal_length_range:
                lines.append(
                    f"- **Focal length:** {d.focal_length_range[0]:.0f}mm – {d.focal_length_range[1]:.0f}mm"
                )
            if d.files_with_gps:
                lines.append(f"- **GPS data:** {d.files_with_gps:,} files")
            lines.append("")

        # Raw backup
        if d.raw_backup_directory:
            lines.append("## Raw Backup")
            lines.append("")
            lines.append(f"Backed up to: `{d.raw_backup_directory}`  ")
            lines.append("")

        # Errors
        if d.errors:
            lines.append("## Errors")
            lines.append("")
            for err in d.errors:
                lines.append(f"- {err}")
            lines.append("")

        lines.append(
            f"*Generated by Photo Ingest Tool on "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"
        )
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # Notes.md
    # ------------------------------------------------------------------

    def write_notes(
        self,
        content: str,
        dry_run: bool = False,
    ) -> Optional[Path]:
        """Write Notes.md; returns path on success, None on failure."""
        path = self.event_dir / "Notes.md"
        if dry_run:
            return path
        errors: List[str] = []
        return self._write_notes(path, content, _ErrorAccumulator(errors))

    def _write_notes(
        self,
        path: Path,
        content: str,
        errors: "_ErrorAccumulator",
    ) -> Optional[Path]:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            logger.info("Wrote notes: %s", path)
            return path
        except Exception as exc:
            msg = f"Failed to write Notes.md: {exc}"
            logger.error(msg)
            errors.append(msg)
            return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _ErrorAccumulator(list):
    """Simple list subclass used as error sink inside _write_* methods."""
    pass


def build_manifest_record(
    source_path: Path,
    dest_path: Optional[Path],
    raw_backup_path: Optional[Path],
    metadata: Dict[str, Any],
    operation_type: str,
    duplicate_status: str,
    sha256_hash: str = "",
) -> ManifestRecord:
    """Convenience factory that extracts EXIF fields from a metadata dict.

    Args:
        source_path: Original source file.
        dest_path: Organized destination (or None).
        raw_backup_path: Raw backup path (or None).
        metadata: EXIF / file metadata dict.
        operation_type: 'organized', 'raw_backup', or 'both'.
        duplicate_status: 'new', 'duplicate', or 'similar'.
        sha256_hash: Pre-computed hash, or empty string (computed lazily).

    Returns:
        A populated ManifestRecord.
    """
    # Compute hash lazily if not provided
    if not sha256_hash and source_path.exists():
        try:
            h = hashlib.sha256()
            with open(source_path, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
            sha256_hash = h.hexdigest()
        except Exception:
            sha256_hash = ""

    # Normalise capture date
    capture_date = ""
    dt_val = metadata.get("DateTime") or metadata.get("DateTimeOriginal")
    if dt_val:
        try:
            if isinstance(dt_val, str):
                if "T" in dt_val:
                    capture_date = dt_val.split("T")[0]
                else:
                    # EXIF colon-separated format
                    capture_date = dt_val.replace(":", "-", 2).split(" ")[0]
            elif hasattr(dt_val, "strftime"):
                capture_date = dt_val.strftime("%Y-%m-%d")
        except Exception:
            pass

    file_size = 0
    try:
        file_size = source_path.stat().st_size
    except Exception:
        pass

    aperture = metadata.get("FNumber", "")
    if isinstance(aperture, float):
        aperture = f"f/{aperture:.1f}"

    return ManifestRecord(
        source_path=str(source_path),
        dest_path=str(dest_path) if dest_path else "",
        raw_backup_path=str(raw_backup_path) if raw_backup_path else "",
        sha256_hash=sha256_hash,
        file_size=file_size,
        camera_make=str(metadata.get("Make", "")),
        camera_model=str(metadata.get("Model", "")),
        lens_model=str(metadata.get("LensModel", "")),
        capture_date=capture_date,
        iso=str(metadata.get("ISO", "")),
        aperture=str(aperture),
        shutter_speed=str(metadata.get("ExposureTime", "")),
        device_code=str(metadata.get("device_code", "Unknown")),
        operation_type=operation_type,
        duplicate_status=duplicate_status,
    )
