"""Unit tests for OutputGenerator (task 12 – Requirements 6.1, 6.2, 6.4, 6.5)."""

import csv
from pathlib import Path

import pytest

from photo_ingest.output_generator import (
    IngestSummaryData,
    ManifestRecord,
    OutputFiles,
    OutputGenerator,
    build_manifest_record,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def event_dir(tmp_path: Path) -> Path:
    d = tmp_path / "2024" / "2024-07-15_Vacation" 
    d.mkdir(parents=True)
    return d


@pytest.fixture
def generator(event_dir: Path) -> OutputGenerator:
    return OutputGenerator(event_dir)


@pytest.fixture
def sample_records() -> list[ManifestRecord]:
    return [
        ManifestRecord(
            source_path="/src/IMG_001.jpg",
            dest_path="/archive/2024/event/Camera/IMG_001.jpg",
            raw_backup_path="/backup/2024-01-01/IMG_001.jpg",
            sha256_hash="abc123",
            file_size=1_048_576,
            camera_make="Canon",
            camera_model="EOS R5",
            lens_model="RF 50mm",
            capture_date="2024-07-15",
            iso="400",
            aperture="f/4.0",
            shutter_speed="1/125",
            device_code="R5",
            operation_type="both",
            duplicate_status="new",
        ),
        ManifestRecord(
            source_path="/src/IMG_002.nef",
            dest_path="/archive/2024/event/Camera/IMG_002.nef",
            raw_backup_path="",
            sha256_hash="def456",
            file_size=25_165_824,
            camera_make="Nikon",
            camera_model="Z 6II",
            lens_model="Z 24-70mm",
            capture_date="2024-07-15",
            iso="800",
            aperture="f/2.8",
            shutter_speed="1/500",
            device_code="Z6II",
            operation_type="organized",
            duplicate_status="new",
        ),
    ]


@pytest.fixture
def sample_summary() -> IngestSummaryData:
    return IngestSummaryData(
        event_name="Vacation",
        event_date="2024-07-15",
        archive_root="/archive",
        total_files=10,
        files_copied=8,
        files_skipped=2,
        duplicates_found=2,
        total_size_bytes=104_857_600,
        file_counts_by_type={"jpeg": 5, "raw": 5},
        cameras={"Canon EOS R5": 6, "Nikon Z 6II": 4},
        lenses={"RF 50mm": 4, "Z 24-70mm": 6},
        date_range=("2024-07-15", "2024-07-22"),
        iso_range=(100, 3200),
        aperture_range=(1.8, 11.0),
        focal_length_range=(24, 200),
        files_with_gps=3,
    )


# ---------------------------------------------------------------------------
# Manifest tests (Req 6.1)
# ---------------------------------------------------------------------------

class TestManifest:
    def test_manifest_written_to_event_dir(self, generator, sample_records, event_dir):
        path = generator.write_manifest(sample_records)
        assert path is not None
        assert path == event_dir / "manifest.csv"
        assert path.exists()

    def test_manifest_has_header_row(self, generator, sample_records):
        path = generator.write_manifest(sample_records)
        with open(path, newline="") as fh:
            reader = csv.DictReader(fh)
            assert "source_path" in reader.fieldnames
            assert "dest_path" in reader.fieldnames
            assert "sha256_hash" in reader.fieldnames
            assert "camera_make" in reader.fieldnames
            assert "capture_date" in reader.fieldnames

    def test_manifest_row_count_matches_records(self, generator, sample_records):
        path = generator.write_manifest(sample_records)
        with open(path, newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == len(sample_records)

    def test_manifest_row_values(self, generator, sample_records):
        path = generator.write_manifest(sample_records)
        with open(path, newline="") as fh:
            rows = list(csv.DictReader(fh))
        row = rows[0]
        assert row["source_path"] == "/src/IMG_001.jpg"
        assert row["sha256_hash"] == "abc123"
        assert row["camera_model"] == "EOS R5"
        assert row["capture_date"] == "2024-07-15"

    def test_manifest_dry_run_no_file_created(self, generator, sample_records, event_dir):
        path = generator.write_manifest(sample_records, dry_run=True)
        assert path == event_dir / "manifest.csv"
        assert not path.exists()

    def test_manifest_empty_records(self, generator):
        path = generator.write_manifest([])
        assert path is not None
        with open(path, newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert rows == []


# ---------------------------------------------------------------------------
# Summary.md tests (Req 6.2)
# ---------------------------------------------------------------------------

class TestSummary:
    def test_summary_written_to_event_dir(self, generator, sample_summary, event_dir):
        path = generator.write_summary(sample_summary)
        assert path is not None
        assert path == event_dir / "Summary.md"
        assert path.exists()

    def test_summary_contains_event_name(self, generator, sample_summary):
        path = generator.write_summary(sample_summary)
        text = path.read_text()
        assert "Vacation" in text

    def test_summary_contains_file_counts(self, generator, sample_summary):
        path = generator.write_summary(sample_summary)
        text = path.read_text()
        assert "10" in text  # total_files

    def test_summary_contains_camera_info(self, generator, sample_summary):
        path = generator.write_summary(sample_summary)
        text = path.read_text()
        assert "Canon EOS R5" in text
        assert "Nikon Z 6II" in text

    def test_summary_contains_lens_info(self, generator, sample_summary):
        path = generator.write_summary(sample_summary)
        text = path.read_text()
        assert "RF 50mm" in text
        assert "Z 24-70mm" in text

    def test_summary_contains_date_range(self, generator, sample_summary):
        path = generator.write_summary(sample_summary)
        text = path.read_text()
        assert "2024-07-15" in text
        assert "2024-07-22" in text

    def test_summary_contains_iso_range(self, generator, sample_summary):
        path = generator.write_summary(sample_summary)
        text = path.read_text()
        assert "100" in text
        assert "3200" in text

    def test_summary_dry_run_no_file_created(self, generator, sample_summary, event_dir):
        path = generator.write_summary(sample_summary, dry_run=True)
        assert path == event_dir / "Summary.md"
        assert not path.exists()

    def test_summary_is_valid_markdown(self, generator, sample_summary):
        path = generator.write_summary(sample_summary)
        text = path.read_text()
        assert text.startswith("# ")   # starts with h1 heading
        assert "##" in text             # has sub-sections


# ---------------------------------------------------------------------------
# Notes.md tests (Req 6.4)
# ---------------------------------------------------------------------------

class TestNotes:
    def test_notes_written_to_event_dir(self, generator, event_dir):
        path = generator.write_notes("This was a great shoot in the mountains.")
        assert path is not None
        assert path == event_dir / "Notes.md"
        assert path.exists()

    def test_notes_content_preserved(self, generator):
        content = "AI-generated notes about the session."
        path = generator.write_notes(content)
        assert path.read_text() == content

    def test_notes_dry_run_no_file_created(self, generator, event_dir):
        path = generator.write_notes("some notes", dry_run=True)
        assert path == event_dir / "Notes.md"
        assert not path.exists()


# ---------------------------------------------------------------------------
# generate_all tests (Req 6.4 – files in event root)
# ---------------------------------------------------------------------------

class TestGenerateAll:
    def test_all_three_files_created(self, generator, sample_records, sample_summary, event_dir):
        output = generator.generate_all(
            sample_records, sample_summary, notes_content="AI notes"
        )
        assert output.manifest_path is not None and output.manifest_path.exists()
        assert output.summary_path is not None and output.summary_path.exists()
        assert output.notes_path is not None and output.notes_path.exists()

    def test_files_are_in_event_dir(self, generator, sample_records, sample_summary, event_dir):
        output = generator.generate_all(
            sample_records, sample_summary, notes_content="AI notes"
        )
        assert output.manifest_path.parent == event_dir
        assert output.summary_path.parent == event_dir
        assert output.notes_path.parent == event_dir

    def test_no_notes_file_when_content_is_none(self, generator, sample_records, sample_summary):
        output = generator.generate_all(sample_records, sample_summary, notes_content=None)
        assert output.notes_path is None

    def test_dry_run_returns_paths_without_creating_files(
        self, generator, sample_records, sample_summary, event_dir
    ):
        output = generator.generate_all(
            sample_records, sample_summary, notes_content="notes", dry_run=True
        )
        assert output.manifest_path == event_dir / "manifest.csv"
        assert output.summary_path == event_dir / "Summary.md"
        assert output.notes_path == event_dir / "Notes.md"
        assert not output.manifest_path.exists()
        assert not output.summary_path.exists()
        assert not output.notes_path.exists()


# ---------------------------------------------------------------------------
# Error handling – Req 6.5 (log error, continue)
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_write_to_nonexistent_parent_creates_it(self, tmp_path):
        """OutputGenerator must create parent dirs if they don't exist."""
        deep_event_dir = tmp_path / "a" / "b" / "c" / "event"
        gen = OutputGenerator(deep_event_dir)
        path = gen.write_notes("content")
        assert path is not None
        assert path.exists()

    def test_failed_manifest_write_returns_none(self, generator, sample_records, monkeypatch):
        """When writing fails, return None (don't raise)."""
        import builtins
        real_open = builtins.open

        def broken_open(file, *args, **kwargs):
            if "manifest.csv" in str(file):
                raise OSError("disk full")
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", broken_open)
        path = generator.write_manifest(sample_records)
        assert path is None

    def test_failed_summary_write_returns_none(self, generator, sample_summary, monkeypatch):
        """When Summary.md write fails, return None (don't raise)."""
        monkeypatch.setattr(Path, "write_text", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
        path = generator.write_summary(sample_summary)
        assert path is None


# ---------------------------------------------------------------------------
# build_manifest_record helper
# ---------------------------------------------------------------------------

class TestBuildManifestRecord:
    def test_basic_fields_populated(self, tmp_path):
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"x" * 100)
        metadata = {
            "Make": "Nikon", "Model": "Z 6II", "LensModel": "50mm",
            "DateTime": "2024-07-15T10:30:00",
            "ISO": 400, "FNumber": 2.8, "ExposureTime": "1/500",
            "device_code": "Z6II",
        }
        rec = build_manifest_record(f, None, None, metadata, "organized", "new")
        assert rec.camera_make == "Nikon"
        assert rec.camera_model == "Z 6II"
        assert rec.capture_date == "2024-07-15"
        assert rec.iso == "400"
        assert "f/2.8" in rec.aperture
        assert rec.device_code == "Z6II"
        assert rec.file_size == 100

    def test_hash_computed_from_file(self, tmp_path):
        import hashlib
        content = b"test content"
        f = tmp_path / "img.nef"
        f.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        rec = build_manifest_record(f, None, None, {}, "raw_backup", "new")
        assert rec.sha256_hash == expected

    def test_provided_hash_used_without_recomputing(self, tmp_path):
        f = tmp_path / "img.nef"
        f.write_bytes(b"data")
        rec = build_manifest_record(f, None, None, {}, "both", "new", sha256_hash="precomputed")
        assert rec.sha256_hash == "precomputed"

    def test_exif_date_colon_format(self, tmp_path):
        f = tmp_path / "img.jpg"
        f.write_bytes(b"d")
        metadata = {"DateTime": "2024:07:15 10:30:00"}
        rec = build_manifest_record(f, None, None, metadata, "organized", "new")
        assert rec.capture_date == "2024-07-15"

    def test_missing_optional_fields_are_empty_strings(self, tmp_path):
        f = tmp_path / "img.jpg"
        f.write_bytes(b"d")
        rec = build_manifest_record(f, None, None, {}, "organized", "new")
        assert rec.camera_make == ""
        assert rec.lens_model == ""
        assert rec.iso == ""
