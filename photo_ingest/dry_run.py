"""Dry-run preview: display planned ingest operations without touching files.

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .deduplication import DuplicateStatus
from .file_operations import FileOperation
from .output_generator import IngestSummaryData, OutputGenerator

logger = logging.getLogger(__name__)

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box as rich_box

    _RICH = True
except ImportError:  # pragma: no cover
    _RICH = False


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class DryRunResult:
    """Everything shown / returned by a dry-run preview."""

    operations: List[FileOperation]
    new_files: int
    duplicate_files: int
    total_size_bytes: int

    # Paths that *would* be created
    manifest_path: Optional[Path] = None
    summary_path: Optional[Path] = None
    notes_path: Optional[Path] = None

    # LLM prompt that *would* be sent (Req 5.5)
    llm_prompt: Optional[str] = None

    errors: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# DryRunPreview
# ---------------------------------------------------------------------------


class DryRunPreview:
    """Plans and displays an ingest dry-run without modifying the filesystem."""

    def __init__(
        self,
        event_dir: Path,
        event_name: str,
        archive_root: str,
        llm_client=None,
        quiet: bool = False,
        verbosity: int = 0,
    ):
        """
        Args:
            event_dir:    Planned event root directory (not yet created).
            event_name:   Human-readable event name.
            archive_root: Archive root path string (for display).
            llm_client:   Optional LLM client; used only to build the prompt.
            quiet:        Suppress all Rich output.
            verbosity:    0 = normal, 1 = verbose (show per-file table).
        """
        self.event_dir = event_dir
        self.event_name = event_name
        self.archive_root = archive_root
        self.llm_client = llm_client
        self.quiet = quiet
        self.verbosity = verbosity
        self._console = Console() if _RICH else None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def preview(
        self,
        operations: List[FileOperation],
        summary_data: IngestSummaryData,
        include_notes: bool = False,
        duplicates_found: Optional[int] = None,
    ) -> DryRunResult:
        """Build and display the dry-run preview.

        Args:
            operations:    Planned FileOperation list (from FileOperationsManager).
            summary_data:  Aggregated stats for the Summary.md preview.
            include_notes: Whether to show the LLM prompt preview.
            duplicates_found: Real duplicate count; plan_operations drops DUPLICATE ops,
                so counting from `operations` would always give 0.

        Returns:
            DryRunResult with counts and planned paths.
        """
        new_files = sum(
            1 for op in operations if op.duplicate_status != DuplicateStatus.DUPLICATE
        )
        duplicate_files = sum(
            1 for op in operations if op.duplicate_status == DuplicateStatus.DUPLICATE
        )
        if duplicates_found is not None:
            duplicate_files = duplicates_found
        total_size = sum(
            op.source_path.stat().st_size
            for op in operations
            if op.source_path.exists()
        )

        # Compute planned output file paths (dry_run=True means no write)
        gen = OutputGenerator(self.event_dir)
        out = gen.generate_all(
            records=[],
            summary_data=summary_data,
            notes_content="placeholder" if include_notes else None,
            dry_run=True,
        )

        # Build LLM prompt if applicable (Req 5.5)
        llm_prompt: Optional[str] = None
        if include_notes and self.llm_client is not None:
            try:
                llm_prompt = self.llm_client.build_prompt(
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
                logger.warning("Could not build LLM prompt: %s", exc)

        result = DryRunResult(
            operations=operations,
            new_files=new_files,
            duplicate_files=duplicate_files,
            total_size_bytes=total_size,
            manifest_path=out.manifest_path,
            summary_path=out.summary_path,
            notes_path=out.notes_path,
            llm_prompt=llm_prompt,
        )

        if not self.quiet:
            self._display(result, operations, summary_data)

        return result

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    def _display(
        self,
        result: DryRunResult,
        operations: List[FileOperation],
        summary_data: IngestSummaryData,
    ) -> None:
        if _RICH and self._console:
            self._display_rich(result, operations, summary_data)
        else:
            self._display_plain(result, operations)

    # ---- Rich output --------------------------------------------------

    def _display_rich(
        self,
        result: DryRunResult,
        operations: List[FileOperation],
        summary_data: IngestSummaryData,
    ) -> None:
        c = self._console

        # Summary panel
        def _fmt(n: int) -> str:
            for u in ("B", "KB", "MB", "GB"):
                if n < 1024:
                    return f"{n:.1f} {u}"
                n /= 1024
            return f"{n:.1f} TB"

        summary_text = (
            f"[bold]DRY RUN — no files will be modified[/bold]\n\n"
            f"📁 Total operations planned: {len(operations):,}\n"
            f"  ✅ New files:       {result.new_files:,}\n"
            f"  🔄 Duplicates:      {result.duplicate_files:,}\n"
            f"  💾 Total size:      {_fmt(result.total_size_bytes)}"
        )
        c.print(Panel(summary_text, title="📋 Ingest Plan", box=rich_box.ROUNDED))

        # Per-file table (verbose mode only — Req 5.2)
        if self.verbosity > 0 and operations:
            self._display_rich_operations_table(operations)

        # Output files that would be created (Req 5.4)
        self._display_rich_output_files(result)

        # LLM prompt preview (Req 5.5)
        if result.llm_prompt:
            c.print(
                Panel(
                    result.llm_prompt,
                    title="🤖 LLM Prompt (would be sent — not called in dry-run)",
                    box=rich_box.SIMPLE,
                )
            )

    def _display_rich_operations_table(self, operations: List[FileOperation]) -> None:
        tbl = Table(title="📋 Planned Operations", box=rich_box.SIMPLE)
        tbl.add_column("Source", style="cyan", no_wrap=False)
        tbl.add_column("Destination", style="green", no_wrap=False)
        tbl.add_column("Status", style="yellow")
        tbl.add_column("Type", style="blue")

        for op in operations[:50]:  # cap at 50 rows for readability
            status = (
                "[bold red]DUPLICATE[/bold red]"
                if op.duplicate_status == DuplicateStatus.DUPLICATE
                else "[bold green]NEW[/bold green]"
            )
            dest = str(op.dest_path or op.raw_backup_path or "")
            tbl.add_row(
                op.source_path.name,
                Path(dest).name if dest else "—",
                status,
                op.operation_type.value,
            )

        if len(operations) > 50:
            tbl.add_row(
                f"… and {len(operations) - 50} more",
                "", "", "",
            )
        self._console.print(tbl)

    def _display_rich_output_files(self, result: DryRunResult) -> None:
        lines = ["[bold]Files that would be created:[/bold]"]
        if result.manifest_path:
            lines.append(f"  📄 {result.manifest_path}")
        if result.summary_path:
            lines.append(f"  📝 {result.summary_path}")
        if result.notes_path:
            lines.append(f"  🤖 {result.notes_path}")
        if len(lines) == 1:
            lines.append("  (none)")
        self._console.print(Panel("\n".join(lines), title="📂 Output Files", box=rich_box.SIMPLE))

    # ---- Plain-text output --------------------------------------------

    def _display_plain(
        self,
        result: DryRunResult,
        operations: List[FileOperation],
    ) -> None:
        print("\n=== DRY RUN — no files will be modified ===")
        print(f"Total operations:  {len(operations):,}")
        print(f"  New files:       {result.new_files:,}")
        print(f"  Duplicates:      {result.duplicate_files:,}")
        print(f"  Total size:      {result.total_size_bytes:,} bytes")

        if self.verbosity > 0:
            print("\n=== Planned Operations ===")
            for op in operations[:50]:
                status = (
                    "[DUPLICATE]"
                    if op.duplicate_status == DuplicateStatus.DUPLICATE
                    else "[NEW]"
                )
                dest = op.dest_path or op.raw_backup_path
                print(f"  {status} {op.source_path.name} -> {Path(str(dest)).name if dest else 'N/A'}")
            if len(operations) > 50:
                print(f"  … and {len(operations) - 50} more")

        print("\n=== Output Files ===")
        if result.manifest_path:
            print(f"  manifest.csv -> {result.manifest_path}")
        if result.summary_path:
            print(f"  Summary.md   -> {result.summary_path}")
        if result.notes_path:
            print(f"  Notes.md     -> {result.notes_path}")

        if result.llm_prompt:
            print("\n=== LLM Prompt (not called in dry-run) ===")
            print(result.llm_prompt)
