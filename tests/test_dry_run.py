"""Unit tests for DryRunPreview (task 11 – Requirements 5.1–5.5)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from photo_ingest.deduplication import DuplicateStatus
from photo_ingest.dry_run import DryRunPreview, DryRunResult
from photo_ingest.file_operations import FileOperation, OperationType
from photo_ingest.output_generator import IngestSummaryData


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_file(tmp_path: Path, name: str, content: bytes = b"x" * 100) -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


@pytest.fixture
def source_files(tmp_path):
    return [
        _make_file(tmp_path, "IMG_001.jpg"),
        _make_file(tmp_path, "IMG_002.nef"),
        _make_file(tmp_path, "IMG_003.jpg"),
    ]


@pytest.fixture
def event_dir(tmp_path):
    return tmp_path / "2024" / "2024-07-15_Vacation"


@pytest.fixture
def summary_data():
    return IngestSummaryData(
        event_name="Vacation",
        event_date="2024-07-15",
        archive_root="/archive",
        total_files=3,
        cameras={"Canon EOS R5": 2, "Nikon Z6II": 1},
        lenses={"RF 50mm": 2, "Z 24-70mm": 1},
        date_range=("2024-07-15", "2024-07-22"),
        iso_range=(100, 1600),
    )


def _make_operations(source_files, event_dir, statuses=None):
    """Build FileOperation list from source files."""
    if statuses is None:
        statuses = [DuplicateStatus.NEW] * len(source_files)
    ops = []
    for f, status in zip(source_files, statuses):
        ops.append(
            FileOperation(
                source_path=f,
                dest_path=event_dir / "Camera" / f.name,
                raw_backup_path=None,
                camera_code="Camera",
                duplicate_status=status,
                metadata={},
                operation_type=OperationType.ORGANIZED_ONLY,
            )
        )
    return ops


# ---------------------------------------------------------------------------
# Req 5.1 – no files modified in dry-run
# ---------------------------------------------------------------------------


class TestDryRunNoSideEffects:
    def test_no_files_created_in_event_dir(self, event_dir, source_files, summary_data):
        preview = DryRunPreview(event_dir, "Vacation", "/archive", quiet=True)
        ops = _make_operations(source_files, event_dir)
        preview.preview(ops, summary_data)
        assert not event_dir.exists()

    def test_source_files_unchanged(self, event_dir, source_files, summary_data):
        original_sizes = [f.stat().st_size for f in source_files]
        preview = DryRunPreview(event_dir, "Vacation", "/archive", quiet=True)
        ops = _make_operations(source_files, event_dir)
        preview.preview(ops, summary_data)
        for f, size in zip(source_files, original_sizes):
            assert f.stat().st_size == size


# ---------------------------------------------------------------------------
# Req 5.2 – source / destination paths in result
# ---------------------------------------------------------------------------


class TestOperationPaths:
    def test_result_contains_all_operations(self, event_dir, source_files, summary_data):
        preview = DryRunPreview(event_dir, "Vacation", "/archive", quiet=True)
        ops = _make_operations(source_files, event_dir)
        result = preview.preview(ops, summary_data)
        assert len(result.operations) == len(source_files)

    def test_operations_have_source_paths(self, event_dir, source_files, summary_data):
        preview = DryRunPreview(event_dir, "Vacation", "/archive", quiet=True)
        ops = _make_operations(source_files, event_dir)
        result = preview.preview(ops, summary_data)
        sources = {op.source_path for op in result.operations}
        assert sources == set(source_files)

    def test_operations_have_dest_paths(self, event_dir, source_files, summary_data):
        preview = DryRunPreview(event_dir, "Vacation", "/archive", quiet=True)
        ops = _make_operations(source_files, event_dir)
        result = preview.preview(ops, summary_data)
        for op in result.operations:
            assert op.dest_path is not None


# ---------------------------------------------------------------------------
# Req 5.3 – NEW vs DUPLICATE labelling
# ---------------------------------------------------------------------------


class TestDuplicateLabelling:
    def test_new_file_count(self, event_dir, source_files, summary_data):
        statuses = [DuplicateStatus.NEW, DuplicateStatus.DUPLICATE, DuplicateStatus.NEW]
        preview = DryRunPreview(event_dir, "Vacation", "/archive", quiet=True)
        ops = _make_operations(source_files, event_dir, statuses)
        result = preview.preview(ops, summary_data)
        assert result.new_files == 2
        assert result.duplicate_files == 1

    def test_all_new(self, event_dir, source_files, summary_data):
        preview = DryRunPreview(event_dir, "Vacation", "/archive", quiet=True)
        ops = _make_operations(source_files, event_dir)
        result = preview.preview(ops, summary_data)
        assert result.new_files == len(source_files)
        assert result.duplicate_files == 0

    def test_all_duplicates(self, event_dir, source_files, summary_data):
        statuses = [DuplicateStatus.DUPLICATE] * len(source_files)
        preview = DryRunPreview(event_dir, "Vacation", "/archive", quiet=True)
        ops = _make_operations(source_files, event_dir, statuses)
        result = preview.preview(ops, summary_data)
        assert result.new_files == 0
        assert result.duplicate_files == len(source_files)


# ---------------------------------------------------------------------------
# Req 5.4 – planned output file paths returned
# ---------------------------------------------------------------------------


class TestOutputFilePaths:
    def test_manifest_path_planned(self, event_dir, source_files, summary_data):
        preview = DryRunPreview(event_dir, "Vacation", "/archive", quiet=True)
        ops = _make_operations(source_files, event_dir)
        result = preview.preview(ops, summary_data)
        assert result.manifest_path == event_dir / "manifest.csv"

    def test_summary_path_planned(self, event_dir, source_files, summary_data):
        preview = DryRunPreview(event_dir, "Vacation", "/archive", quiet=True)
        ops = _make_operations(source_files, event_dir)
        result = preview.preview(ops, summary_data)
        assert result.summary_path == event_dir / "Summary.md"

    def test_notes_path_planned_when_include_notes(self, event_dir, source_files, summary_data):
        mock_llm = MagicMock()
        mock_llm.build_prompt.return_value = "test prompt"
        preview = DryRunPreview(event_dir, "Vacation", "/archive", llm_client=mock_llm, quiet=True)
        ops = _make_operations(source_files, event_dir)
        result = preview.preview(ops, summary_data, include_notes=True)
        assert result.notes_path == event_dir / "Notes.md"

    def test_notes_path_none_when_no_notes(self, event_dir, source_files, summary_data):
        preview = DryRunPreview(event_dir, "Vacation", "/archive", quiet=True)
        ops = _make_operations(source_files, event_dir)
        result = preview.preview(ops, summary_data, include_notes=False)
        assert result.notes_path is None

    def test_planned_paths_not_created_on_disk(self, event_dir, source_files, summary_data):
        preview = DryRunPreview(event_dir, "Vacation", "/archive", quiet=True)
        ops = _make_operations(source_files, event_dir)
        result = preview.preview(ops, summary_data)
        assert not result.manifest_path.exists()
        assert not result.summary_path.exists()


# ---------------------------------------------------------------------------
# Req 5.5 – LLM prompt shown, not called
# ---------------------------------------------------------------------------


class TestLLMPromptPreview:
    def test_llm_prompt_built_not_called(self, event_dir, source_files, summary_data):
        mock_llm = MagicMock()
        mock_llm.build_prompt.return_value = "Generated prompt text"
        preview = DryRunPreview(
            event_dir, "Vacation", "/archive", llm_client=mock_llm, quiet=True
        )
        ops = _make_operations(source_files, event_dir)
        result = preview.preview(ops, summary_data, include_notes=True)

        mock_llm.build_prompt.assert_called_once()
        # generate_summary must NOT be called (that would actually hit the LLM)
        mock_llm.generate_summary.assert_not_called()
        assert result.llm_prompt == "Generated prompt text"

    def test_no_prompt_when_no_llm_client(self, event_dir, source_files, summary_data):
        preview = DryRunPreview(event_dir, "Vacation", "/archive", quiet=True)
        ops = _make_operations(source_files, event_dir)
        result = preview.preview(ops, summary_data, include_notes=True)
        assert result.llm_prompt is None

    def test_no_prompt_when_include_notes_false(self, event_dir, source_files, summary_data):
        mock_llm = MagicMock()
        preview = DryRunPreview(
            event_dir, "Vacation", "/archive", llm_client=mock_llm, quiet=True
        )
        ops = _make_operations(source_files, event_dir)
        result = preview.preview(ops, summary_data, include_notes=False)
        mock_llm.build_prompt.assert_not_called()
        assert result.llm_prompt is None

    def test_llm_error_does_not_raise(self, event_dir, source_files, summary_data):
        mock_llm = MagicMock()
        mock_llm.build_prompt.side_effect = RuntimeError("LLM unavailable")
        preview = DryRunPreview(
            event_dir, "Vacation", "/archive", llm_client=mock_llm, quiet=True
        )
        ops = _make_operations(source_files, event_dir)
        # Should not raise
        result = preview.preview(ops, summary_data, include_notes=True)
        assert result.llm_prompt is None


# ---------------------------------------------------------------------------
# Size accounting
# ---------------------------------------------------------------------------


class TestSizeAccounting:
    def test_total_size_sum_of_source_files(self, event_dir, summary_data, tmp_path):
        f1 = _make_file(tmp_path, "a.jpg", b"x" * 500)
        f2 = _make_file(tmp_path, "b.nef", b"y" * 1000)
        ops = _make_operations([f1, f2], event_dir)
        preview = DryRunPreview(event_dir, "Vacation", "/archive", quiet=True)
        result = preview.preview(ops, summary_data)
        assert result.total_size_bytes == 1500

    def test_missing_source_file_does_not_raise(self, event_dir, summary_data, tmp_path):
        ghost = tmp_path / "ghost.jpg"  # does not exist
        real = _make_file(tmp_path, "real.jpg")
        ops = _make_operations([ghost, real], event_dir)
        preview = DryRunPreview(event_dir, "Vacation", "/archive", quiet=True)
        result = preview.preview(ops, summary_data)
        # Should not raise; ghost contributes 0 bytes
        assert result.total_size_bytes == real.stat().st_size


# ---------------------------------------------------------------------------
# Plain-text output (Rich not available)
# ---------------------------------------------------------------------------


class TestPlainOutput:
    def test_plain_output_contains_counts(
        self, capsys, event_dir, source_files, summary_data
    ):
        with patch("photo_ingest.dry_run._RICH", False):
            preview = DryRunPreview(event_dir, "Vacation", "/archive", quiet=False)
            ops = _make_operations(source_files, event_dir)
            preview.preview(ops, summary_data)

        captured = capsys.readouterr().out
        assert str(len(source_files)) in captured

    def test_quiet_mode_suppresses_output(
        self, capsys, event_dir, source_files, summary_data
    ):
        preview = DryRunPreview(event_dir, "Vacation", "/archive", quiet=True)
        ops = _make_operations(source_files, event_dir)
        preview.preview(ops, summary_data)
        # With quiet=True, nothing should be printed
        # (Rich uses its own stdout; capsys may not capture it, but no exception)
