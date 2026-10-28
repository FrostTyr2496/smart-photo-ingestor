"""IngestCommand – orchestrates the full ingest pipeline (task 14).

Requirements: 15.7, 15.8, 8.4
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .config import IngestConfig
from .database import DatabaseManager
from .deduplication import DeduplicationEngine, DuplicateStatus
from .device_detector import DeviceDetector
from .dry_run import DryRunPreview
from .exceptions import (
    PhotoIngestError,
    SourceDirectoryError,
    EXIFProcessingError,
    FileOperationError,
    ArchiveDirectoryError,
    DeduplicationError,
    format_user_error,
)
from .file_operations import (
    FileOperation,
    FileOperationsManager,
    OperationResults,
    RawBackupResult,
)
from .file_scanner import FileScanner
from .output_generator import (
    IngestSummaryData,
    ManifestRecord,
    OutputFiles,
    OutputGenerator,
    build_manifest_record,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class IngestResult:
    """Full result of an ingest run."""

    # Operation counts
    total_files: int = 0
    files_copied: int = 0
    files_moved: int = 0
    files_skipped: int = 0
    duplicates_found: int = 0
    total_size_bytes: int = 0

    # Output files
    manifest_path: Optional[Path] = None
    summary_path: Optional[Path] = None
    notes_path: Optional[Path] = None

    # Raw backup info
    raw_backup_result: Optional[RawBackupResult] = None

    # Errors accumulated during the run
    errors: List[str] = field(default_factory=list)

    # Timing
    processing_time_seconds: float = 0.0

    # Dry-run flag
    dry_run: bool = False


# ---------------------------------------------------------------------------
# IngestCommand
# ---------------------------------------------------------------------------


class IngestCommand:
    """Orchestrates the full photo ingest pipeline.

    Phases (in order):
    1. Scan source directory for supported files
    2. Extract EXIF metadata (using Pillow fallback)
    3. Detect devices from EXIF
    4. Deduplication check
    5. Plan file operations
    6. Execute (or preview in dry-run mode)
    7. Generate output files (manifest.csv, Summary.md, Notes.md)
    8. Flush deduplication DB
    """

    def __init__(
        self,
        config: IngestConfig,
        progress_callback: Optional[Callable] = None,
    ):
        """
        Args:
            config: Full ingest configuration.
            progress_callback: Optional ``(phase, current, total, message)`` callback.
        """
        self.config = config
        self.progress = progress_callback

        # Lazy-initialised in run() so tests can swap them via dependency injection
        self._db_manager: Optional[DatabaseManager] = None
        self._dedup_engine: Optional[DeduplicationEngine] = None
        self._scanner: Optional[FileScanner] = None
        self._device_detector: Optional[DeviceDetector] = None
        self._file_ops: Optional[FileOperationsManager] = None

    # ------------------------------------------------------------------
    # Public entry-point
    # ------------------------------------------------------------------

    def run(
        self,
        source: Path,
        event_name: str,
        copy_mode: bool = True,
        dry_run: bool = False,
        raw_only: bool = False,
        organized_only: bool = False,
    ) -> IngestResult:
        """Execute the ingest pipeline.

        Args:
            source:          Source directory to ingest from.
            event_name:      Human-readable event name (used in folder structure).
            copy_mode:       True = copy files, False = move them.
            dry_run:         Preview only – no files written.
            raw_only:        Only perform raw backup (no organised import).
            organized_only:  Only perform organised import (no raw backup).

        Returns:
            IngestResult with counts, paths, and errors.
        """
        if raw_only and organized_only:
            raise ValueError("Cannot specify both raw_only and organized_only")

        start_time = time.time()
        result = IngestResult(dry_run=dry_run)

        # --- initialise sub-components ---
        self._init_components()

        # Validate source before entering the error-absorbing try block so that
        # callers (and tests) receive a typed exception instead of having
        # it swallowed into result.errors.
        if not source.exists():
            raise SourceDirectoryError(source)

        try:
            # 1. Scan
            self._emit("scan", 0, 1, "Scanning source directory…")
            image_files = self._scan(source)
            result.total_files = len(image_files)
            self._emit("scan", 1, 1, f"Found {len(image_files)} files")

            if not image_files:
                logger.warning("No supported files found in %s", source)
                result.processing_time_seconds = time.time() - start_time
                return result

            # 2. Extract metadata
            self._emit("exif", 0, len(image_files), "Extracting EXIF metadata…")
            metadata_map = self._extract_metadata(image_files)
            self._emit("exif", len(image_files), len(image_files), "EXIF extraction complete")

            # 3. Device detection
            device_map = self._detect_devices(metadata_map)
            # Merge device_code into metadata
            for path, device_code in device_map.items():
                metadata_map[path]["device_code"] = device_code

            # 4. Deduplication (skip in raw-only mode per Req 4.7)
            if not raw_only:
                self._emit("dedup", 0, len(image_files), "Checking for duplicates…")
                dedup_results = self._check_duplicates(image_files)
                self._emit("dedup", len(image_files), len(image_files), "Deduplication complete")
            else:
                dedup_results = {p: DuplicateStatus.NEW for p in image_files}

            result.duplicates_found = sum(
                1 for s in dedup_results.values() if s == DuplicateStatus.DUPLICATE
            )

            # 5. Plan operations
            operations = self._file_ops.plan_operations(
                files_with_metadata=metadata_map,
                event_name=event_name,
                duplicate_results={p: r for p, r in dedup_results.items()},
                raw_only=raw_only,
                organized_only=organized_only,
            )

            # Compute event directory from first non-duplicate operation
            event_dir = self._resolve_event_dir(operations, event_name)

            # 6a. Dry-run preview
            if dry_run:
                summary_data = self._build_summary_data(
                    event_name, operations, metadata_map, result
                )
                preview = DryRunPreview(
                    event_dir=event_dir,
                    event_name=event_name,
                    archive_root=self.config.archive_root,
                    quiet=self.progress is None,
                )
                dry_result = preview.preview(operations, summary_data)
                result.manifest_path = dry_result.manifest_path
                result.summary_path = dry_result.summary_path
                result.notes_path = dry_result.notes_path
                result.files_copied = dry_result.new_files if copy_mode else 0
                result.files_moved = 0 if copy_mode else dry_result.new_files
                result.files_skipped = dry_result.duplicate_files
                result.total_size_bytes = dry_result.total_size_bytes
                result.processing_time_seconds = time.time() - start_time
                return result

            # 6b. Execute
            self._emit("copy", 0, len(operations), "Executing file operations…")
            op_results = self._file_ops.execute_operations(
                operations, copy_mode=copy_mode, dry_run=False
            )
            self._emit("copy", len(operations), len(operations), "File operations complete")

            result.files_copied = op_results.files_copied
            result.files_moved = op_results.files_moved
            result.files_skipped = op_results.files_skipped
            result.raw_backup_result = op_results.raw_backup_result
            result.errors.extend(op_results.errors)
            result.total_size_bytes = sum(
                p.stat().st_size for p in image_files if p.exists()
            )

            # 7. Output files
            summary_data = self._build_summary_data(
                event_name, operations, metadata_map, result
            )
            manifest_records = self._build_manifest_records(
                operations, metadata_map, dedup_results
            )
            notes_content = self._generate_notes(summary_data)

            gen = OutputGenerator(event_dir)
            out = gen.generate_all(
                manifest_records, summary_data, notes_content=notes_content
            )
            result.manifest_path = out.manifest_path
            result.summary_path = out.summary_path
            result.notes_path = out.notes_path
            result.errors.extend(out.errors)

            # 8. Flush dedup DB (only for real runs, not dry-run; not raw-only per Req 4.7)
            if not raw_only:
                self._store_dedup_records(operations, metadata_map, dedup_results)
                self._dedup_engine.flush_pending_records()

        except Exception as exc:
            msg = format_user_error(exc)
            logger.exception("Ingest pipeline error: %s", exc)
            result.errors.append(msg)

        result.processing_time_seconds = time.time() - start_time
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _init_components(self) -> None:
        """Lazy-initialise sub-components (allows test injection)."""
        if self._db_manager is None:
            self._db_manager = DatabaseManager(self.config.dedupe_store)
        if self._dedup_engine is None:
            self._dedup_engine = DeduplicationEngine(
                self._db_manager, self.config.performance
            )
        if self._scanner is None:
            self._scanner = FileScanner(self.config.file_types, self.config.performance)
        if self._device_detector is None:
            self._device_detector = DeviceDetector(self.config.devices)
        if self._file_ops is None:
            self._file_ops = FileOperationsManager(self.config, self.progress)

    def _scan(self, source: Path) -> List[Path]:
        """Return list of supported image/video file paths."""
        if not source.exists():
            raise SourceDirectoryError(source)
        scan_result = self._scanner.scan_directory(source, recursive=True)
        return [f.path for f in scan_result.files if f.file_type in ("raw", "jpeg", "video")]

    def _extract_metadata(self, files: List[Path]) -> Dict[Path, Dict[str, Any]]:
        """Extract EXIF metadata using Pillow fallback."""
        from .analyzer import PhotoAnalyzer

        analyzer = PhotoAnalyzer(self.config.file_types, self.config.performance, detailed=True)
        metadata_map: Dict[Path, Dict[str, Any]] = {}
        total = len(files)

        for i, path in enumerate(files):
            try:
                meta = analyzer._extract_detailed_exif(path)
                metadata_map[path] = meta if meta else {}
            except Exception as exc:
                logger.warning("EXIF extraction failed for %s: %s — using empty metadata", path, exc)
                metadata_map[path] = {}
            if self.progress and i % 10 == 0:
                self._emit("exif", i + 1, total, path.name)

        return metadata_map

    def _detect_devices(self, metadata_map: Dict[Path, Dict[str, Any]]) -> Dict[Path, str]:
        """Map each file path to its detected device code."""
        return {
            path: self._device_detector.detect_device(meta)
            for path, meta in metadata_map.items()
        }

    def _check_duplicates(
        self, files: List[Path]
    ) -> Dict[Path, DuplicateStatus]:
        """Run deduplication check on all files."""
        results: Dict[Path, DuplicateStatus] = {}
        total = len(files)
        for i, path in enumerate(files):
            try:
                check = self._dedup_engine.check_duplicate(path)
                results[path] = check.status
            except Exception as exc:
                logger.warning("Dedup check failed for %s: %s", path, exc)
                results[path] = DuplicateStatus.NEW
            if self.progress and i % 10 == 0:
                self._emit("dedup", i + 1, total, path.name)
        return results

    def _resolve_event_dir(
        self, operations: List[FileOperation], event_name: str
    ) -> Path:
        """Derive the event root directory from planned operations."""
        for op in operations:
            if op.dest_path:
                # dest_path is archive/YYYY/YYYY-MM-DD_Event/Device/filename
                # event dir is two levels up from the file
                return op.dest_path.parent.parent
        # Fallback: build from archive_root + today + event_name
        today = datetime.now().strftime("%Y-%m-%d")
        year = today[:4]
        return Path(self.config.archive_root) / year / f"{today}_{event_name}"

    def _build_summary_data(
        self,
        event_name: str,
        operations: List[FileOperation],
        metadata_map: Dict[Path, Dict[str, Any]],
        result: IngestResult,
    ) -> IngestSummaryData:
        """Aggregate stats for Summary.md."""
        from collections import Counter
        cameras: Counter = Counter()
        lenses: Counter = Counter()
        isos: list = []
        apertures: list = []
        focal_lengths: list = []
        dates: list = []
        gps_count = 0
        type_counts: Counter = Counter()

        for op in operations:
            meta = metadata_map.get(op.source_path, {})

            make = str(meta.get("Make", "")).strip()
            model = str(meta.get("Model", "")).strip()
            if make and model:
                cameras[f"{make} {model}"] += 1
            elif model:
                cameras[model] += 1

            lens = str(meta.get("LensModel", "")).strip()
            if lens:
                lenses[lens] += 1

            if meta.get("ISO") is not None:
                try:
                    isos.append(int(meta["ISO"]))
                except (ValueError, TypeError):
                    pass

            if meta.get("FNumber") is not None:
                try:
                    apertures.append(float(meta["FNumber"]))
                except (ValueError, TypeError):
                    pass

            if meta.get("FocalLength") is not None:
                try:
                    focal_lengths.append(float(meta["FocalLength"]))
                except (ValueError, TypeError):
                    pass

            if meta.get("DateTime"):
                try:
                    dt = meta["DateTime"]
                    if hasattr(dt, "strftime"):
                        dates.append(dt)
                except Exception:
                    pass

            if meta.get("GPSLatitude") is not None:
                gps_count += 1

            ext = op.source_path.suffix.lower().lstrip(".")
            for ftype, exts in [
                ("raw", self.config.file_types.raw),
                ("jpeg", self.config.file_types.jpeg),
                ("video", self.config.file_types.video),
            ]:
                if ext in exts:
                    type_counts[ftype] += 1
                    break

        date_range = None
        if dates:
            date_range = (
                min(dates).strftime("%Y-%m-%d"),
                max(dates).strftime("%Y-%m-%d"),
            )

        return IngestSummaryData(
            event_name=event_name,
            event_date=datetime.now().strftime("%Y-%m-%d"),
            archive_root=self.config.archive_root,
            total_files=result.total_files,
            files_copied=result.files_copied,
            files_moved=result.files_moved,
            files_skipped=result.files_skipped,
            duplicates_found=result.duplicates_found,
            total_size_bytes=result.total_size_bytes,
            file_counts_by_type=dict(type_counts),
            cameras=dict(cameras),
            lenses=dict(lenses),
            date_range=date_range,
            iso_range=(min(isos), max(isos)) if isos else None,
            aperture_range=(min(apertures), max(apertures)) if apertures else None,
            focal_length_range=(min(focal_lengths), max(focal_lengths)) if focal_lengths else None,
            files_with_gps=gps_count,
            errors=result.errors.copy(),
            raw_backup_directory=(
                str(result.raw_backup_result.backup_directory)
                if result.raw_backup_result
                else None
            ),
            processing_time_seconds=result.processing_time_seconds,
        )

    def _build_manifest_records(
        self,
        operations: List[FileOperation],
        metadata_map: Dict[Path, Dict[str, Any]],
        dedup_results: Dict[Path, DuplicateStatus],
    ) -> List[ManifestRecord]:
        """Build one ManifestRecord per operation."""
        records = []
        for op in operations:
            meta = metadata_map.get(op.source_path, {})
            status = dedup_results.get(op.source_path, DuplicateStatus.NEW)
            rec = build_manifest_record(
                source_path=op.source_path,
                dest_path=op.dest_path,
                raw_backup_path=op.raw_backup_path,
                metadata=meta,
                operation_type=op.operation_type.value,
                duplicate_status=status.value,
            )
            records.append(rec)
        return records

    def _generate_notes(self, summary_data: IngestSummaryData) -> Optional[str]:
        """Generate LLM notes if enabled; return None if disabled or failed."""
        if not self.config.llm.enabled:
            return None
        try:
            from .llm_client import LLMClient

            client = LLMClient(self.config.llm)
            return client.generate_summary(
                {
                    "cameras": summary_data.cameras,
                    "lenses": summary_data.lenses,
                    "date_range": summary_data.date_range or ("?", "?"),
                    "total_files": summary_data.total_files,
                    "iso_range": summary_data.iso_range or (0, 0),
                    "files_with_gps": summary_data.files_with_gps,
                }
            )
        except Exception as exc:
            logger.warning("LLM note generation failed: %s", exc)
            return None

    def _store_dedup_records(
        self,
        operations: List[FileOperation],
        metadata_map: Dict[Path, Dict[str, Any]],
        dedup_results: Dict[Path, DuplicateStatus],
    ) -> None:
        """Persist new files to the deduplication database."""
        for op in operations:
            if dedup_results.get(op.source_path) != DuplicateStatus.NEW:
                continue
            meta = metadata_map.get(op.source_path, {})
            try:
                hashes = self._dedup_engine.calculate_hashes(op.source_path)
                self._dedup_engine.store_file_record(
                    file_path=op.source_path,
                    hashes=hashes,
                    dest_path=op.dest_path,
                    raw_backup_path=op.raw_backup_path,
                    metadata=meta,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to store dedup record for %s: %s", op.source_path, exc
                )

    def _emit(self, phase: str, current: int, total: int, message: str) -> None:
        """Fire the progress callback if set."""
        if self.progress:
            try:
                self.progress(phase, current, total, message)
            except Exception:
                pass
