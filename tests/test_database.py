"""Unit tests for database operations."""

import pytest
import tempfile
import json
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

from photo_ingest.database import (
    DatabaseManager, FileRecord, ExifCacheRecord, DirectoryCacheRecord,
    ProcessingStatus, OperationType
)


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    
    db_manager = DatabaseManager(db_path)
    yield db_manager
    
    # Cleanup
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def sample_file_record():
    """Create a sample file record for testing."""
    return FileRecord(
        source_path="/test/source/IMG_001.jpg",
        dest_path="/test/dest/2024/2024-01-15_Event/Z6II/IMG_001.jpg",
        raw_backup_path="/test/backup/2024-01-15_120000/IMG_001.jpg",
        sha256_hash="abc123def456",
        perceptual_hash="perceptual123",
        file_size=1024000,
        file_mtime=1705123456,
        created_date="2024-01-15T12:00:00",
        processed_date="2024-01-15T12:05:00",
        camera_model="NIKON Z 6_2",
        lens_model="NIKKOR Z 24-70mm f/4 S",
        device_code="Z6II",
        operation_type=OperationType.BOTH.value,
        processing_status=ProcessingStatus.COMPLETED.value
    )


@pytest.fixture
def sample_exif_data():
    """Create sample EXIF data for testing."""
    return {
        "Make": "NIKON CORPORATION",
        "Model": "NIKON Z 6_2",
        "LensModel": "NIKKOR Z 24-70mm f/4 S",
        "DateTime": "2024:01:15 12:00:00",
        "ISO": 400,
        "FNumber": 4.0,
        "ExposureTime": "1/125"
    }


class TestDatabaseManager:
    """Test cases for DatabaseManager class."""
    
    def test_database_initialization(self, temp_db):
        """Test database initialization creates all tables and indexes."""
        # Check that tables exist by querying them
        with temp_db._get_connection() as conn:
            # Check file_records table
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='file_records'")
            assert cursor.fetchone() is not None
            
            # Check exif_cache table
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='exif_cache'")
            assert cursor.fetchone() is not None
            
            # Check directory_cache table
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='directory_cache'")
            assert cursor.fetchone() is not None
            
            # Check indexes exist
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_sha256'")
            assert cursor.fetchone() is not None
    
    def test_insert_file_record(self, temp_db, sample_file_record):
        """Test inserting a single file record."""
        temp_db.insert_file_record(sample_file_record)
        
        # Verify record was inserted
        record = temp_db.get_file_by_hash(sample_file_record.sha256_hash)
        assert record is not None
        assert record['source_path'] == sample_file_record.source_path
        assert record['device_code'] == sample_file_record.device_code
    
    def test_batch_insert_records(self, temp_db):
        """Test batch inserting multiple file records."""
        records = []
        for i in range(3):
            record = FileRecord(
                source_path=f"/test/IMG_{i:03d}.jpg",
                dest_path=f"/dest/IMG_{i:03d}.jpg",
                raw_backup_path=None,
                sha256_hash=f"hash{i:03d}",
                perceptual_hash=None,
                file_size=1024 * (i + 1),
                file_mtime=1705123456 + i,
                created_date="2024-01-15T12:00:00",
                processed_date="2024-01-15T12:05:00",
                camera_model="Test Camera",
                lens_model="Test Lens",
                device_code="TEST",
                operation_type=OperationType.ORGANIZED.value
            )
            records.append(record)
        
        temp_db.batch_insert_records(records)
        
        # Verify all records were inserted
        stats = temp_db.get_database_stats()
        assert stats['file_records'] == 3
    
    def test_get_file_by_hash(self, temp_db, sample_file_record):
        """Test retrieving file by SHA-256 hash."""
        temp_db.insert_file_record(sample_file_record)
        
        record = temp_db.get_file_by_hash(sample_file_record.sha256_hash)
        assert record is not None
        assert record['source_path'] == sample_file_record.source_path
        
        # Test non-existent hash
        record = temp_db.get_file_by_hash("nonexistent")
        assert record is None
    
    def test_get_files_by_size_range(self, temp_db):
        """Test retrieving files by size range."""
        # Insert records with different sizes
        sizes = [1000, 2000, 3000, 4000]
        for i, size in enumerate(sizes):
            record = FileRecord(
                source_path=f"/test/IMG_{i}.jpg",
                dest_path=f"/dest/IMG_{i}.jpg",
                raw_backup_path=None,
                sha256_hash=f"hash{i}",
                perceptual_hash=None,
                file_size=size,
                file_mtime=1705123456,
                created_date="2024-01-15T12:00:00",
                processed_date="2024-01-15T12:05:00",
                camera_model="Test Camera",
                lens_model="Test Lens",
                device_code="TEST",
                operation_type=OperationType.ORGANIZED.value
            )
            temp_db.insert_file_record(record)
        
        # Test size range query
        results = temp_db.get_files_by_size_range(1500, 3500)
        assert len(results) == 2  # Should match 2000 and 3000 byte files
        
        sizes_found = [r['file_size'] for r in results]
        assert 2000 in sizes_found
        assert 3000 in sizes_found
    
    @patch('photo_ingest.database.Path.stat')
    def test_cache_exif(self, mock_stat, temp_db, sample_exif_data):
        """Test EXIF data caching."""
        # Mock file stat
        mock_stat.return_value = MagicMock(st_mtime=1705123456)
        
        file_path = Path("/test/IMG_001.jpg")
        temp_db.cache_exif(file_path, sample_exif_data)
        
        # Retrieve cached data
        cached_data = temp_db.get_cached_exif(file_path)
        assert cached_data is not None
        assert cached_data['Make'] == sample_exif_data['Make']
        assert cached_data['Model'] == sample_exif_data['Model']
    
    @patch('photo_ingest.database.Path.stat')
    def test_get_cached_exif_file_changed(self, mock_stat, temp_db, sample_exif_data):
        """Test that cached EXIF is not returned when file has changed."""
        # Mock initial file stat
        mock_stat.return_value = MagicMock(st_mtime=1705123456)
        
        file_path = Path("/test/IMG_001.jpg")
        temp_db.cache_exif(file_path, sample_exif_data)
        
        # Mock changed file stat
        mock_stat.return_value = MagicMock(st_mtime=1705123500)  # Different mtime
        
        # Should return None since file has changed
        cached_data = temp_db.get_cached_exif(file_path)
        assert cached_data is None
    
    @patch('photo_ingest.database.Path.stat')
    def test_directory_cache(self, mock_stat, temp_db):
        """Test directory caching functionality."""
        # Mock directory stat
        mock_stat.return_value = MagicMock(st_mtime=1705123456)
        
        dir_path = Path("/test/photos")
        
        # Initially, directory should be considered changed (not cached)
        assert temp_db.is_directory_changed(dir_path) is True
        
        # Update cache
        temp_db.update_directory_cache(dir_path, 10)
        
        # Now directory should not be considered changed
        assert temp_db.is_directory_changed(dir_path) is False
        
        # Mock changed directory
        mock_stat.return_value = MagicMock(st_mtime=1705123500)  # Different mtime
        
        # Should be considered changed now
        assert temp_db.is_directory_changed(dir_path) is True
    
    @patch('photo_ingest.database.Path.stat')
    def test_is_file_unchanged(self, mock_stat, temp_db, sample_file_record):
        """Test checking if file is unchanged."""
        # Insert a file record
        temp_db.insert_file_record(sample_file_record)
        
        file_path = Path(sample_file_record.source_path)
        
        # File should be considered unchanged with same mtime
        assert temp_db.is_file_unchanged(file_path, sample_file_record.file_mtime) is True
        
        # File should be considered changed with different mtime
        assert temp_db.is_file_unchanged(file_path, sample_file_record.file_mtime + 100) is False
    
    def test_get_files_by_perceptual_hash(self, temp_db):
        """Test retrieving files by perceptual hash."""
        # Insert records with same perceptual hash
        perceptual_hash = "perceptual123"
        for i in range(2):
            record = FileRecord(
                source_path=f"/test/IMG_{i}.jpg",
                dest_path=f"/dest/IMG_{i}.jpg",
                raw_backup_path=None,
                sha256_hash=f"hash{i}",
                perceptual_hash=perceptual_hash,
                file_size=1024,
                file_mtime=1705123456,
                created_date="2024-01-15T12:00:00",
                processed_date="2024-01-15T12:05:00",
                camera_model="Test Camera",
                lens_model="Test Lens",
                device_code="TEST",
                operation_type=OperationType.ORGANIZED.value
            )
            temp_db.insert_file_record(record)
        
        results = temp_db.get_files_by_perceptual_hash(perceptual_hash)
        assert len(results) == 2
    
    def test_get_database_stats(self, temp_db, sample_file_record):
        """Test getting database statistics."""
        # Initially empty
        stats = temp_db.get_database_stats()
        assert stats['file_records'] == 0
        assert stats['exif_cache'] == 0
        assert stats['directory_cache'] == 0
        assert stats['unique_devices'] == 0
        
        # Insert a record
        temp_db.insert_file_record(sample_file_record)
        
        stats = temp_db.get_database_stats()
        assert stats['file_records'] == 1
        assert stats['unique_devices'] == 1
    
    @patch('photo_ingest.database.Path.stat')
    def test_cleanup_old_cache_entries(self, mock_stat, temp_db, sample_exif_data):
        """Test cleaning up old cache entries."""
        # Mock file stat
        mock_stat.return_value = MagicMock(st_mtime=1705123456)
        
        file_path = Path("/test/IMG_001.jpg")
        
        # Cache some EXIF data
        temp_db.cache_exif(file_path, sample_exif_data)
        
        # Update directory cache
        temp_db.update_directory_cache(Path("/test"), 1)
        
        # Verify cache entries exist
        stats = temp_db.get_database_stats()
        assert stats['exif_cache'] == 1
        assert stats['directory_cache'] == 1
        
        # Clean up old entries (using 0 days to clean everything)
        temp_db.cleanup_old_cache_entries(days_old=0)
        
        # Verify entries were cleaned up
        stats = temp_db.get_database_stats()
        assert stats['exif_cache'] == 0
        assert stats['directory_cache'] == 0
    
    def test_database_path_creation(self):
        """Test that database creates parent directories if needed."""
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "subdir" / "test.db"
            
            # Parent directory doesn't exist initially
            assert not db_path.parent.exists()
            
            # Creating database should create parent directory
            db_manager = DatabaseManager(str(db_path))
            assert db_path.parent.exists()
            assert db_path.exists()
    
    def test_empty_batch_insert(self, temp_db):
        """Test that empty batch insert doesn't cause errors."""
        temp_db.batch_insert_records([])
        
        stats = temp_db.get_database_stats()
        assert stats['file_records'] == 0
    
    @patch('photo_ingest.database.Path.stat')
    def test_cache_exif_file_stat_error(self, mock_stat, temp_db, sample_exif_data):
        """Test EXIF caching when file stat fails."""
        # Mock stat to raise an exception
        mock_stat.side_effect = OSError("File not found")
        
        file_path = Path("/test/nonexistent.jpg")
        
        # Should not raise exception, just log warning and skip
        temp_db.cache_exif(file_path, sample_exif_data)
        
        # Should return None when trying to get cached data
        cached_data = temp_db.get_cached_exif(file_path)
        assert cached_data is None
    
    def test_invalid_json_in_cache(self, temp_db):
        """Test handling of invalid JSON in EXIF cache."""
        # Manually insert invalid JSON into cache
        with temp_db._get_connection() as conn:
            conn.execute(
                "INSERT INTO exif_cache (file_path, file_mtime, exif_data, created_date) VALUES (?, ?, ?, ?)",
                ("/test/invalid.jpg", 1705123456, "invalid json", "2024-01-15T12:00:00")
            )
            conn.commit()
        
        # Should return None and log warning
        with patch('photo_ingest.database.Path.stat') as mock_stat:
            mock_stat.return_value = MagicMock(st_mtime=1705123456)
            cached_data = temp_db.get_cached_exif(Path("/test/invalid.jpg"))
            assert cached_data is None


class TestDataClasses:
    """Test cases for data classes."""
    
    def test_file_record_creation(self):
        """Test FileRecord creation with all fields."""
        record = FileRecord(
            source_path="/test/source.jpg",
            dest_path="/test/dest.jpg",
            raw_backup_path="/test/backup.jpg",
            sha256_hash="abc123",
            perceptual_hash="perceptual123",
            file_size=1024,
            file_mtime=1705123456,
            created_date="2024-01-15T12:00:00",
            processed_date="2024-01-15T12:05:00",
            camera_model="Test Camera",
            lens_model="Test Lens",
            device_code="TEST",
            operation_type=OperationType.ORGANIZED.value
        )
        
        assert record.source_path == "/test/source.jpg"
        assert record.processing_status == ProcessingStatus.COMPLETED.value
    
    def test_exif_cache_record_creation(self):
        """Test ExifCacheRecord creation."""
        exif_data = {"Make": "Test", "Model": "Camera"}
        record = ExifCacheRecord(
            file_path="/test/file.jpg",
            file_mtime=1705123456,
            exif_data=exif_data,
            created_date="2024-01-15T12:00:00"
        )
        
        assert record.file_path == "/test/file.jpg"
        assert record.exif_data == exif_data
    
    def test_directory_cache_record_creation(self):
        """Test DirectoryCacheRecord creation."""
        record = DirectoryCacheRecord(
            directory_path="/test/dir",
            scan_timestamp=1705123456,
            file_count=10,
            last_modified=1705123400
        )
        
        assert record.directory_path == "/test/dir"
        assert record.file_count == 10


class TestEnums:
    """Test cases for enum classes."""
    
    def test_processing_status_enum(self):
        """Test ProcessingStatus enum values."""
        assert ProcessingStatus.COMPLETED.value == "completed"
        assert ProcessingStatus.FAILED.value == "failed"
        assert ProcessingStatus.SKIPPED.value == "skipped"
    
    def test_operation_type_enum(self):
        """Test OperationType enum values."""
        assert OperationType.ORGANIZED.value == "organized"
        assert OperationType.RAW_BACKUP.value == "raw_backup"
        assert OperationType.BOTH.value == "both"