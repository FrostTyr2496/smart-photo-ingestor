"""Tests for deduplication engine."""

import tempfile
import pytest
from pathlib import Path
from unittest.mock import Mock, patch
import hashlib

from photo_ingest.deduplication import (
    DeduplicationEngine, DuplicateStatus, FileHashes, DuplicateCheckResult
)
from photo_ingest.config import PerformanceConfig
from photo_ingest.database import DatabaseManager


@pytest.fixture
def temp_db():
    """Create temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    db_manager = DatabaseManager(db_path)
    yield db_manager
    
    # Cleanup
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def dedup_engine(temp_db):
    """Create deduplication engine for testing."""
    config = PerformanceConfig(
        parallel_workers=1,
        batch_size=10,
        memory_mapped_hashing=False  # Disable for testing
    )
    return DeduplicationEngine(temp_db, config)


@pytest.fixture
def temp_files():
    """Create temporary files for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Create test files with different content
        files = []
        
        # File 1: "Hello World"
        file1 = temp_path / "file1.txt"
        file1.write_text("Hello World")
        files.append(file1)
        
        # File 2: Same content as file1 (duplicate)
        file2 = temp_path / "file2.txt"
        file2.write_text("Hello World")
        files.append(file2)
        
        # File 3: Different content
        file3 = temp_path / "file3.txt"
        file3.write_text("Different content")
        files.append(file3)
        
        # File 4: Large file (for mmap testing)
        file4 = temp_path / "file4.txt"
        file4.write_text("x" * (2 * 1024 * 1024))  # 2MB
        files.append(file4)
        
        yield files


class TestFileHashes:
    """Test FileHashes dataclass."""
    
    def test_valid_hashes(self):
        """Test creating valid FileHashes."""
        hashes = FileHashes(
            sha256="a" * 64,
            perceptual="1234567890abcdef",
            size=1024
        )
        
        assert hashes.sha256 == "a" * 64
        assert hashes.perceptual == "1234567890abcdef"
        assert hashes.size == 1024
    
    def test_invalid_sha256(self):
        """Test invalid SHA-256 hash."""
        with pytest.raises(ValueError, match="Invalid SHA-256 hash"):
            FileHashes(sha256="invalid", size=1024)
    
    def test_negative_size(self):
        """Test negative file size."""
        with pytest.raises(ValueError, match="File size cannot be negative"):
            FileHashes(sha256="a" * 64, size=-1)


class TestDeduplicationEngine:
    """Test DeduplicationEngine class."""
    
    def test_init(self, dedup_engine):
        """Test deduplication engine initialization."""
        assert dedup_engine.db_manager is not None
        assert dedup_engine.config is not None
        assert dedup_engine._pending_records == []
    
    def test_calculate_hashes_small_file(self, dedup_engine, temp_files):
        """Test hash calculation for small files."""
        file_path = temp_files[0]  # "Hello World"
        
        hashes = dedup_engine.calculate_hashes(file_path)
        
        assert isinstance(hashes, FileHashes)
        assert len(hashes.sha256) == 64
        assert hashes.size == file_path.stat().st_size
        
        # Verify SHA-256 is correct
        expected_hash = hashlib.sha256(b"Hello World").hexdigest()
        assert hashes.sha256 == expected_hash
    
    def test_calculate_hashes_large_file(self, dedup_engine, temp_files):
        """Test hash calculation for large files."""
        file_path = temp_files[3]  # 2MB file
        
        hashes = dedup_engine.calculate_hashes(file_path)
        
        assert isinstance(hashes, FileHashes)
        assert len(hashes.sha256) == 64
        assert hashes.size == file_path.stat().st_size
    
    def test_calculate_hashes_mmap(self, temp_db, temp_files):
        """Test memory-mapped hash calculation."""
        config = PerformanceConfig(memory_mapped_hashing=True)
        engine = DeduplicationEngine(temp_db, config)
        
        file_path = temp_files[3]  # Large file
        
        hashes = engine.calculate_hashes(file_path)
        
        assert isinstance(hashes, FileHashes)
        assert len(hashes.sha256) == 64
    
    def test_batch_calculate_hashes(self, dedup_engine, temp_files):
        """Test batch hash calculation."""
        file_paths = temp_files[:3]
        
        results = dedup_engine.batch_calculate_hashes(file_paths)
        
        assert len(results) == 3
        for file_path in file_paths:
            assert file_path in results
            assert isinstance(results[file_path], FileHashes)
    
    def test_check_duplicate_new_file(self, dedup_engine, temp_files):
        """Test duplicate check for new file."""
        file_path = temp_files[0]
        
        result = dedup_engine.check_duplicate(file_path)
        
        assert isinstance(result, DuplicateCheckResult)
        assert result.status == DuplicateStatus.NEW
        assert result.hashes is not None
        assert result.existing_file is None
    
    def test_check_duplicate_exact_duplicate(self, dedup_engine, temp_files):
        """Test duplicate check for exact duplicate."""
        file1 = temp_files[0]  # "Hello World"
        file2 = temp_files[1]  # Same content
        
        # First file should be new
        result1 = dedup_engine.check_duplicate(file1)
        assert result1.status == DuplicateStatus.NEW
        
        # Store the first file
        dedup_engine.store_file_record(file1, result1.hashes)
        dedup_engine.flush_pending_records()
        
        # Second file should be duplicate
        result2 = dedup_engine.check_duplicate(file2)
        assert result2.status == DuplicateStatus.DUPLICATE
        assert result2.existing_file is not None
    
    def test_store_file_record(self, dedup_engine, temp_files):
        """Test storing file record."""
        file_path = temp_files[0]
        hashes = dedup_engine.calculate_hashes(file_path)
        
        # Store record
        dedup_engine.store_file_record(file_path, hashes)
        dedup_engine.flush_pending_records()
        
        # Verify it was stored
        stored_record = dedup_engine.db_manager.get_file_by_hash(hashes.sha256)
        assert stored_record is not None
        assert stored_record['source_path'] == str(file_path)
        assert stored_record['sha256_hash'] == hashes.sha256
    
    def test_store_file_record_with_metadata(self, dedup_engine, temp_files):
        """Test storing file record with metadata."""
        file_path = temp_files[0]
        hashes = dedup_engine.calculate_hashes(file_path)
        
        metadata = {
            'Model': 'Test Camera',
            'LensModel': 'Test Lens',
            'device_code': 'TestDevice'
        }
        
        # Store record with metadata
        dedup_engine.store_file_record(file_path, hashes, metadata=metadata)
        dedup_engine.flush_pending_records()
        
        # Verify metadata was stored
        stored_record = dedup_engine.db_manager.get_file_by_hash(hashes.sha256)
        assert stored_record['camera_model'] == 'Test Camera'
        assert stored_record['lens_model'] == 'Test Lens'
        assert stored_record['device_code'] == 'TestDevice'
    
    def test_batch_processing(self, dedup_engine, temp_files):
        """Test batch processing of records."""
        # Configure small batch size
        dedup_engine.config.batch_size = 2
        
        file1, file2, file3 = temp_files[:3]
        
        # Store files - should trigger batch flush
        hashes1 = dedup_engine.calculate_hashes(file1)
        dedup_engine.store_file_record(file1, hashes1)
        
        hashes2 = dedup_engine.calculate_hashes(file2)
        dedup_engine.store_file_record(file2, hashes2)
        
        # Batch should be flushed automatically
        assert len(dedup_engine._pending_records) == 0
        
        # Verify records were stored
        stored1 = dedup_engine.db_manager.get_file_by_hash(hashes1.sha256)
        stored2 = dedup_engine.db_manager.get_file_by_hash(hashes2.sha256)
        
        assert stored1 is not None
        assert stored2 is not None
    
    def test_get_duplicate_statistics(self, dedup_engine, temp_files):
        """Test getting duplicate statistics."""
        # Store some files
        for file_path in temp_files[:3]:
            hashes = dedup_engine.calculate_hashes(file_path)
            dedup_engine.store_file_record(file_path, hashes)
        
        dedup_engine.flush_pending_records()
        
        # Get statistics
        stats = dedup_engine.get_duplicate_statistics()
        
        assert 'file_records' in stats
        assert 'duplicate_groups' in stats
        assert stats['file_records'] >= 3
    
    def test_is_image_file(self, dedup_engine):
        """Test image file detection."""
        assert dedup_engine._is_image_file(Path("test.jpg")) is True
        assert dedup_engine._is_image_file(Path("test.jpeg")) is True
        assert dedup_engine._is_image_file(Path("test.png")) is True
        assert dedup_engine._is_image_file(Path("test.heic")) is True
        assert dedup_engine._is_image_file(Path("test.txt")) is False
        assert dedup_engine._is_image_file(Path("test.mp4")) is False
    
    @patch('photo_ingest.deduplication.IMAGEHASH_AVAILABLE', True)
    @patch('photo_ingest.deduplication.imagehash')
    @patch('photo_ingest.deduplication.Image')
    def test_calculate_perceptual_hash(self, mock_image, mock_imagehash, dedup_engine):
        """Test perceptual hash calculation."""
        # Mock PIL Image and imagehash
        mock_img = Mock()
        mock_image.open.return_value.__enter__.return_value = mock_img
        mock_imagehash.average_hash.return_value = "1234567890abcdef"
        
        file_path = Path("test.jpg")
        
        result = dedup_engine._calculate_perceptual_hash(file_path)
        
        assert result == "1234567890abcdef"
        mock_image.open.assert_called_once_with(file_path)
        mock_imagehash.average_hash.assert_called_once_with(mock_img)
    
    @patch('photo_ingest.deduplication.IMAGEHASH_AVAILABLE', False)
    def test_calculate_perceptual_hash_unavailable(self, dedup_engine):
        """Test perceptual hash when imagehash is unavailable."""
        result = dedup_engine._calculate_perceptual_hash(Path("test.jpg"))
        assert result is None
    
    def test_file_unchanged_check(self, dedup_engine, temp_files):
        """Test file unchanged detection."""
        file_path = temp_files[0]
        file_mtime = int(file_path.stat().st_mtime)
        
        # Initially should not be unchanged (not in database)
        assert not dedup_engine._is_file_unchanged(file_path, file_mtime)
        
        # Store file
        hashes = dedup_engine.calculate_hashes(file_path)
        dedup_engine.store_file_record(file_path, hashes)
        dedup_engine.flush_pending_records()
        
        # Now should be unchanged
        assert dedup_engine._is_file_unchanged(file_path, file_mtime)
        
        # Different mtime should not be unchanged
        assert not dedup_engine._is_file_unchanged(file_path, file_mtime + 1)


class TestDeduplicationIntegration:
    """Integration tests for deduplication engine."""
    
    def test_full_deduplication_workflow(self, dedup_engine, temp_files):
        """Test complete deduplication workflow."""
        file1, file2, file3 = temp_files[:3]  # file1 and file2 have same content
        
        # Process first file
        result1 = dedup_engine.check_duplicate(file1)
        assert result1.status == DuplicateStatus.NEW
        
        dedup_engine.store_file_record(file1, result1.hashes)
        dedup_engine.flush_pending_records()
        
        # Process duplicate file
        result2 = dedup_engine.check_duplicate(file2)
        assert result2.status == DuplicateStatus.DUPLICATE
        assert result2.existing_file == str(file1)
        
        # Process different file
        result3 = dedup_engine.check_duplicate(file3)
        assert result3.status == DuplicateStatus.NEW
        
        dedup_engine.store_file_record(file3, result3.hashes)
        dedup_engine.flush_pending_records()
        
        # Verify statistics
        stats = dedup_engine.get_duplicate_statistics()
        assert stats['file_records'] == 2  # Only unique files stored
    
    def test_parallel_hash_calculation(self, temp_db, temp_files):
        """Test parallel hash calculation."""
        config = PerformanceConfig(parallel_workers=2)
        engine = DeduplicationEngine(temp_db, config)
        
        file_paths = temp_files[:3]
        
        results = engine.batch_calculate_hashes(file_paths)
        
        assert len(results) == 3
        for file_path in file_paths:
            assert file_path in results
            assert isinstance(results[file_path], FileHashes)
    
    def test_size_based_prefiltering(self, dedup_engine, temp_files):
        """Test size-based pre-filtering optimization."""
        file1 = temp_files[0]  # Small file
        file4 = temp_files[3]  # Large file (different size)
        
        # Store small file
        result1 = dedup_engine.check_duplicate(file1)
        dedup_engine.store_file_record(file1, result1.hashes)
        dedup_engine.flush_pending_records()
        
        # Check large file - should be NEW due to size difference
        result4 = dedup_engine.check_duplicate(file4)
        assert result4.status == DuplicateStatus.NEW
        
        # The engine should have skipped expensive hash comparison
        # due to size pre-filtering
    
    def test_error_handling_missing_file(self, dedup_engine):
        """Test error handling for missing files."""
        missing_file = Path("/nonexistent/file.txt")
        
        with pytest.raises((OSError, IOError)):
            dedup_engine.check_duplicate(missing_file)
    
    def test_error_handling_permission_denied(self, dedup_engine, temp_files):
        """Test error handling for permission denied."""
        file_path = temp_files[0]
        
        # Mock stat to raise PermissionError
        with patch.object(Path, 'stat', side_effect=PermissionError("Permission denied")):
            with pytest.raises(PermissionError):
                dedup_engine.check_duplicate(file_path)