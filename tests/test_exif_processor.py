"""Tests for EXIF processing engine."""

import pytest
import tempfile
import json
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from photo_ingest.exif_processor import EXIFProcessor, EXIFProcessingError
from photo_ingest.config import IngestConfig, DeviceMapping, FileTypes, PerformanceConfig
from photo_ingest.database import DatabaseManager


@pytest.fixture
def temp_db():
    """Create temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False) as f:
        db_path = f.name
    
    db_manager = DatabaseManager(db_path)
    yield db_manager
    
    # Cleanup
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def sample_config():
    """Create sample configuration for testing."""
    return IngestConfig(
        archive_root="/test/archive",
        devices=DeviceMapping(
            mappings={
                "NIKON Z 6": "Z6",
                "Canon EOS R5": "R5",
                "DJI AIR 2S": "Drone"
            },
            exif_identifiers={
                "Z6": {
                    "Make": "NIKON CORPORATION",
                    "Model": "NIKON Z 6"
                },
                "Drone": {
                    "Make": "DJI",
                    "Model": "FC3582"
                }
            }
        ),
        file_types=FileTypes(),
        performance=PerformanceConfig(
            cache_exif=True,
            parallel_workers=2
        )
    )


@pytest.fixture
def exif_processor(sample_config, temp_db):
    """Create EXIF processor for testing."""
    return EXIFProcessor(sample_config, temp_db)


@pytest.fixture
def sample_exif_data():
    """Sample EXIF data for testing."""
    return {
        'EXIF:Make': 'NIKON CORPORATION',
        'EXIF:Model': 'NIKON Z 6',
        'EXIF:LensModel': 'NIKKOR Z 24-70mm f/4 S',
        'EXIF:DateTimeOriginal': '2023:12:15 14:30:45',
        'EXIF:ISO': 400,
        'EXIF:FNumber': 4.0,
        'EXIF:ExposureTime': '1/125',
        'EXIF:FocalLength': 50.0,
        'EXIF:GPSLatitude': 40.7128,
        'EXIF:GPSLongitude': -74.0060,
        'File:FileSize': 25000000,
        'File:FileType': 'NEF',
        'EXIF:ImageWidth': 6048,
        'EXIF:ImageHeight': 4024
    }


class TestEXIFProcessor:
    """Test cases for EXIFProcessor class."""
    
    def test_init_with_exiftool_available(self, sample_config, temp_db):
        """Test initialization when ExifTool is available."""
        with patch('photo_ingest.exif_processor.EXIFTOOL_AVAILABLE', True), \
             patch('subprocess.run') as mock_run:
            
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "12.40"
            
            processor = EXIFProcessor(sample_config, temp_db)
            
            assert processor.exiftool_available is True
            assert processor.pillow_available is True
    
    def test_init_with_exiftool_unavailable(self, sample_config, temp_db):
        """Test initialization when ExifTool is not available."""
        with patch('photo_ingest.exif_processor.EXIFTOOL_AVAILABLE', False):
            processor = EXIFProcessor(sample_config, temp_db)
            
            assert processor.exiftool_available is False
            assert processor.pillow_available is True
    
    def test_check_exiftool_binary_not_found(self, sample_config, temp_db):
        """Test ExifTool check when binary is not found."""
        with patch('photo_ingest.exif_processor.EXIFTOOL_AVAILABLE', True), \
             patch('subprocess.run', side_effect=FileNotFoundError):
            
            processor = EXIFProcessor(sample_config, temp_db)
            assert processor.exiftool_available is False
    
    def test_extract_metadata_with_cache_hit(self, exif_processor, temp_db):
        """Test metadata extraction with cache hit."""
        test_file = Path("/test/image.jpg")
        cached_data = {"Make": "Canon", "Model": "EOS R5"}
        
        # Mock cache hit
        temp_db.get_cached_exif = Mock(return_value=cached_data)
        
        result = exif_processor.extract_metadata(test_file)
        
        assert result == cached_data
        temp_db.get_cached_exif.assert_called_once_with(test_file)
    
    def test_extract_metadata_with_cache_miss(self, exif_processor, temp_db):
        """Test metadata extraction with cache miss."""
        test_file = Path("/test/image.jpg")
        extracted_data = {"Make": "Nikon", "Model": "Z6"}
        
        # Mock cache miss and extraction
        temp_db.get_cached_exif = Mock(return_value=None)
        temp_db.cache_exif = Mock()
        exif_processor._extract_with_fallback = Mock(return_value=extracted_data)
        
        result = exif_processor.extract_metadata(test_file)
        
        assert result == extracted_data
        temp_db.cache_exif.assert_called_once_with(test_file, extracted_data)
    
    def test_extract_with_fallback_exiftool_success(self, exif_processor, sample_exif_data):
        """Test fallback chain with ExifTool success."""
        test_file = Path("/test/image.nef")
        
        exif_processor.exiftool_available = True
        exif_processor._extract_with_exiftool = Mock(return_value=sample_exif_data)
        
        result = exif_processor._extract_with_fallback(test_file)
        
        assert result == sample_exif_data
        exif_processor._extract_with_exiftool.assert_called_once_with(test_file)
    
    def test_extract_with_fallback_pillow_fallback(self, exif_processor):
        """Test fallback to Pillow when ExifTool fails."""
        test_file = Path("/test/image.jpg")
        pillow_data = {"Make": "Canon", "Model": "EOS R5"}
        
        exif_processor.exiftool_available = True
        exif_processor._extract_with_exiftool = Mock(side_effect=Exception("ExifTool failed"))
        exif_processor._extract_with_pillow = Mock(return_value=pillow_data)
        
        result = exif_processor._extract_with_fallback(test_file)
        
        assert result == pillow_data
        exif_processor._extract_with_pillow.assert_called_once_with(test_file)
    
    def test_extract_with_fallback_filesystem_fallback(self, exif_processor):
        """Test fallback to filesystem metadata when all else fails."""
        test_file = Path("/test/image.jpg")
        fs_data = {"DateTime": "2023-12-15T14:30:45", "FileSize": 1000}
        
        exif_processor.exiftool_available = False
        exif_processor._extract_with_pillow = Mock(side_effect=Exception("Pillow failed"))
        exif_processor._extract_filesystem_metadata = Mock(return_value=fs_data)
        
        result = exif_processor._extract_with_fallback(test_file)
        
        assert result == fs_data
    
    def test_extract_with_fallback_all_fail(self, exif_processor):
        """Test fallback when all extraction methods fail."""
        test_file = Path("/test/image.jpg")
        minimal_data = {"Make": "", "Model": "", "DateTime": "2023-12-15T14:30:45"}
        
        exif_processor.exiftool_available = False
        exif_processor._extract_with_pillow = Mock(side_effect=Exception("Pillow failed"))
        exif_processor._extract_filesystem_metadata = Mock(side_effect=Exception("FS failed"))
        exif_processor._create_minimal_metadata = Mock(return_value=minimal_data)
        
        result = exif_processor._extract_with_fallback(test_file)
        
        assert result == minimal_data
    
    @patch('photo_ingest.exif_processor.ExifToolHelper')
    def test_extract_with_exiftool(self, mock_exiftool_helper, exif_processor, sample_exif_data):
        """Test ExifTool extraction."""
        test_file = Path("/test/image.nef")
        
        # Mock ExifTool response
        mock_et = MagicMock()
        mock_et.get_metadata.return_value = [sample_exif_data]
        mock_exiftool_helper.return_value.__enter__.return_value = mock_et
        
        result = exif_processor._extract_with_exiftool(test_file)
        
        assert result['Make'] == 'NIKON CORPORATION'
        assert result['Model'] == 'NIKON Z 6'
        assert result['LensModel'] == 'NIKKOR Z 24-70mm f/4 S'
        assert result['ISO'] == 400
        assert result['FNumber'] == 4.0
    
    def test_normalize_exiftool_metadata(self, exif_processor, sample_exif_data):
        """Test ExifTool metadata normalization."""
        result = exif_processor._normalize_exiftool_metadata(sample_exif_data)
        
        assert result['Make'] == 'NIKON CORPORATION'
        assert result['Model'] == 'NIKON Z 6'
        assert result['LensModel'] == 'NIKKOR Z 24-70mm f/4 S'
        assert result['DateTime'] == '2023-12-15T14:30:45'
        assert result['ISO'] == 400
        assert result['FNumber'] == 4.0
        assert result['ExposureTime'] == '1/125'
        assert result['FocalLength'] == 50.0
        assert result['GPSLatitude'] == 40.7128
        assert result['GPSLongitude'] == -74.0060
        assert result['FileSize'] == 25000000
        assert result['FileType'] == 'NEF'
        assert result['ImageWidth'] == 6048
        assert result['ImageHeight'] == 4024
    
    @patch('photo_ingest.exif_processor.Image')
    def test_extract_with_pillow(self, mock_image, exif_processor):
        """Test Pillow EXIF extraction."""
        test_file = Path("/test/image.jpg")
        
        # Mock Pillow image and EXIF data
        mock_img = MagicMock()
        mock_img.width = 4000
        mock_img.height = 3000
        mock_img.getexif.return_value = {
            271: 'Canon',  # Make
            272: 'EOS R5',  # Model
            42036: 'RF24-70mm F2.8 L IS USM',  # LensModel
            306: '2023:12:15 14:30:45',  # DateTime
            34855: 800,  # ISO
            33437: 2.8,  # FNumber
            33434: 0.008,  # ExposureTime (1/125)
            37386: 50.0,  # FocalLength
        }
        
        mock_image.open.return_value.__enter__.return_value = mock_img
        
        # Mock TAGS mapping
        with patch('photo_ingest.exif_processor.TAGS', {
            271: 'Make',
            272: 'Model', 
            42036: 'LensModel',
            306: 'DateTime',
            34855: 'ISOSpeedRatings',
            33437: 'FNumber',
            33434: 'ExposureTime',
            37386: 'FocalLength'
        }):
            result = exif_processor._extract_with_pillow(test_file)
        
        assert result['Make'] == 'Canon'
        assert result['Model'] == 'EOS R5'
        assert result['LensModel'] == 'RF24-70mm F2.8 L IS USM'
        assert result['ISO'] == 800
        assert result['FNumber'] == 2.8
        assert result['ImageWidth'] == 4000
        assert result['ImageHeight'] == 3000
    
    def test_extract_filesystem_metadata(self, exif_processor, tmp_path):
        """Test filesystem metadata extraction."""
        test_file = tmp_path / "test.jpg"
        test_file.write_text("dummy content")
        
        result = exif_processor._extract_filesystem_metadata(test_file)
        
        assert result['Make'] == ''
        assert result['Model'] == ''
        assert result['LensModel'] == ''
        assert 'DateTime' in result
        assert result['FileSize'] > 0
        assert result['FileType'] == 'jpg'
        assert result['ImageWidth'] is None
        assert result['ImageHeight'] is None
    
    def test_create_minimal_metadata(self, exif_processor, tmp_path):
        """Test minimal metadata creation."""
        test_file = tmp_path / "test.nef"
        test_file.write_text("dummy content")
        
        result = exif_processor._create_minimal_metadata(test_file)
        
        assert result['Make'] == ''
        assert result['Model'] == ''
        assert result['LensModel'] == ''
        assert 'DateTime' in result
        assert result['FileSize'] > 0
        assert result['FileType'] == 'nef'
    
    def test_detect_device(self, exif_processor):
        """Test device detection."""
        metadata = {
            'Make': 'NIKON CORPORATION',
            'Model': 'NIKON Z 6'
        }
        
        exif_processor.device_detector.detect_device = Mock(return_value="Z6")
        
        result = exif_processor.detect_device(metadata)
        
        assert result == "Z6"
        exif_processor.device_detector.detect_device.assert_called_once_with(metadata)
    
    def test_batch_extract_metadata_parallel(self, exif_processor):
        """Test parallel batch metadata extraction."""
        test_files = [Path(f"/test/image{i}.jpg") for i in range(3)]
        expected_results = {
            test_files[0]: {"Make": "Canon", "Model": "R5"},
            test_files[1]: {"Make": "Nikon", "Model": "Z6"},
            test_files[2]: {"Make": "Sony", "Model": "A7R"}
        }
        
        def mock_extract(path):
            return expected_results[path]
        
        exif_processor.extract_metadata = Mock(side_effect=mock_extract)
        
        result = exif_processor.batch_extract_metadata(test_files)
        
        assert len(result) == 3
        assert result == expected_results
    
    def test_batch_extract_metadata_sequential(self, exif_processor):
        """Test sequential batch metadata extraction."""
        # Force sequential processing
        exif_processor.performance_config.parallel_workers = 1
        
        test_files = [Path("/test/image.jpg")]
        expected_data = {"Make": "Canon", "Model": "R5"}
        
        exif_processor.extract_metadata = Mock(return_value=expected_data)
        
        result = exif_processor.batch_extract_metadata(test_files)
        
        assert result == {test_files[0]: expected_data}
    
    def test_batch_extract_metadata_with_errors(self, exif_processor):
        """Test batch extraction with some files failing."""
        test_files = [Path("/test/good.jpg"), Path("/test/bad.jpg")]
        good_data = {"Make": "Canon", "Model": "R5"}
        minimal_data = {"Make": "", "Model": ""}
        
        def mock_extract(path):
            if "bad" in str(path):
                raise Exception("Extraction failed")
            return good_data
        
        exif_processor.extract_metadata = Mock(side_effect=mock_extract)
        exif_processor._create_minimal_metadata = Mock(return_value=minimal_data)
        
        result = exif_processor.batch_extract_metadata(test_files)
        
        assert len(result) == 2
        assert result[test_files[0]] == good_data
        assert result[test_files[1]] == minimal_data
    
    def test_parse_datetime_standard_format(self, exif_processor):
        """Test datetime parsing with standard EXIF format."""
        result = exif_processor._parse_datetime("2023:12:15 14:30:45")
        assert result == "2023-12-15T14:30:45"
    
    def test_parse_datetime_iso_format(self, exif_processor):
        """Test datetime parsing with ISO format."""
        result = exif_processor._parse_datetime("2023-12-15 14:30:45")
        assert result == "2023-12-15T14:30:45"
    
    def test_parse_datetime_date_only(self, exif_processor):
        """Test datetime parsing with date only."""
        result = exif_processor._parse_datetime("2023:12:15")
        assert result == "2023-12-15T00:00:00"
    
    def test_parse_datetime_invalid_format(self, exif_processor):
        """Test datetime parsing with invalid format."""
        invalid_date = "not a date"
        result = exif_processor._parse_datetime(invalid_date)
        assert result == invalid_date
    
    def test_safe_int_conversion(self, exif_processor):
        """Test safe integer conversion."""
        assert exif_processor._safe_int(42) == 42
        assert exif_processor._safe_int(42.7) == 42
        assert exif_processor._safe_int("42") == 42
        assert exif_processor._safe_int("100/1") == 100
        assert exif_processor._safe_int("invalid") is None
        assert exif_processor._safe_int(None) is None
    
    def test_safe_float_conversion(self, exif_processor):
        """Test safe float conversion."""
        assert exif_processor._safe_float(42.5) == 42.5
        assert exif_processor._safe_float(42) == 42.0
        assert exif_processor._safe_float("42.5") == 42.5
        assert exif_processor._safe_float("28/1") == 28.0
        assert exif_processor._safe_float("2.8/1") == 2.8
        assert exif_processor._safe_float("invalid") is None
        assert exif_processor._safe_float(None) is None
    
    def test_get_supported_extensions(self, exif_processor):
        """Test getting supported file extensions."""
        extensions = exif_processor.get_supported_extensions()
        
        assert isinstance(extensions, list)
        assert 'jpg' in extensions
        assert 'nef' in extensions
        assert 'mp4' in extensions
    
    def test_is_supported_file(self, exif_processor):
        """Test file support checking."""
        assert exif_processor.is_supported_file(Path("test.jpg")) is True
        assert exif_processor.is_supported_file(Path("test.NEF")) is True
        assert exif_processor.is_supported_file(Path("test.mp4")) is True
        assert exif_processor.is_supported_file(Path("test.txt")) is False
        assert exif_processor.is_supported_file(Path("test.xyz")) is False


class TestEXIFProcessorIntegration:
    """Integration tests for EXIF processor."""
    
    def test_full_extraction_workflow(self, sample_config, temp_db, tmp_path):
        """Test complete extraction workflow."""
        # Create a test image file
        test_file = tmp_path / "test.jpg"
        test_file.write_text("dummy image content")
        
        processor = EXIFProcessor(sample_config, temp_db)
        
        # Mock the extraction methods since we don't have real images
        processor._extract_with_exiftool = Mock(side_effect=Exception("No ExifTool"))
        processor._extract_with_pillow = Mock(side_effect=Exception("No Pillow"))
        
        # This should fall back to filesystem metadata
        result = processor.extract_metadata(test_file)
        
        assert 'DateTime' in result
        assert result['FileSize'] > 0
        assert result['FileType'] == 'jpg'
    
    def test_caching_workflow(self, sample_config, temp_db, tmp_path):
        """Test EXIF caching workflow."""
        test_file = tmp_path / "test.jpg"
        test_file.write_text("dummy content")
        
        processor = EXIFProcessor(sample_config, temp_db)
        
        # Mock extraction to return consistent data
        mock_data = {"Make": "Canon", "Model": "R5", "DateTime": "2023-12-15T14:30:45"}
        processor._extract_with_fallback = Mock(return_value=mock_data)
        
        # First extraction should cache the data
        result1 = processor.extract_metadata(test_file)
        assert result1 == mock_data
        
        # Second extraction should use cache
        result2 = processor.extract_metadata(test_file)
        assert result2 == mock_data
        
        # Verify extraction was only called once (first time)
        assert processor._extract_with_fallback.call_count == 1