"""Tests for the photo analyzer functionality."""

import tempfile
import pytest
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock
from PIL import Image

from photo_ingest.analyzer import PhotoAnalyzer, AnalysisResult
from photo_ingest.config import FileTypes, PerformanceConfig
from photo_ingest.file_scanner import FileInfo


class TestPhotoAnalyzer:
    """Test PhotoAnalyzer class."""
    
    @pytest.fixture
    def analyzer(self):
        """Create analyzer instance for testing."""
        file_types = FileTypes()
        performance_config = PerformanceConfig(parallel_workers=1)
        return PhotoAnalyzer(file_types, performance_config, detailed=True)
    
    @pytest.fixture
    def basic_analyzer(self):
        """Create basic analyzer instance for testing."""
        file_types = FileTypes()
        performance_config = PerformanceConfig(parallel_workers=1)
        return PhotoAnalyzer(file_types, performance_config, detailed=False)
    
    @pytest.fixture
    def temp_image_files(self):
        """Create temporary image files for testing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # Create test images with different sizes
            images = []
            
            # Create a 100x200 image
            img1 = Image.new('RGB', (100, 200), color='red')
            img1_path = temp_path / "test1.jpg"
            img1.save(img1_path)
            images.append(img1_path)
            
            # Create a 300x400 image
            img2 = Image.new('RGB', (300, 400), color='blue')
            img2_path = temp_path / "test2.jpg"
            img2.save(img2_path)
            images.append(img2_path)
            
            # Create a 500x600 image
            img3 = Image.new('RGB', (500, 600), color='green')
            img3_path = temp_path / "test3.jpg"
            img3.save(img3_path)
            images.append(img3_path)
            
            yield images
    
    def test_extract_basic_exif_dimensions(self, basic_analyzer, temp_image_files):
        """Test that basic EXIF extraction gets image dimensions."""
        for img_path in temp_image_files:
            metadata = basic_analyzer._extract_basic_exif(img_path)
            
            print(f"Testing {img_path.name}: {metadata}")
            
            # Should always have image dimensions
            assert 'ImageWidth' in metadata, f"Missing ImageWidth for {img_path.name}"
            assert 'ImageHeight' in metadata, f"Missing ImageHeight for {img_path.name}"
            assert metadata['ImageWidth'] > 0, f"Invalid ImageWidth for {img_path.name}"
            assert metadata['ImageHeight'] > 0, f"Invalid ImageHeight for {img_path.name}"
    
    def test_extract_detailed_exif_dimensions(self, analyzer, temp_image_files):
        """Test that detailed EXIF extraction gets image dimensions."""
        for img_path in temp_image_files:
            metadata = analyzer._extract_detailed_exif(img_path)
            
            print(f"Testing {img_path.name}: {metadata}")
            
            # Should always have image dimensions
            assert 'ImageWidth' in metadata, f"Missing ImageWidth for {img_path.name}"
            assert 'ImageHeight' in metadata, f"Missing ImageHeight for {img_path.name}"
            assert metadata['ImageWidth'] > 0, f"Invalid ImageWidth for {img_path.name}"
            assert metadata['ImageHeight'] > 0, f"Invalid ImageHeight for {img_path.name}"
    
    def test_metadata_batch_extraction(self, basic_analyzer, temp_image_files):
        """Test batch metadata extraction."""
        # Create FileInfo objects
        file_infos = []
        for img_path in temp_image_files:
            stat = img_path.stat()
            file_info = FileInfo(
                path=img_path,
                size=stat.st_size,
                modified_time=datetime.fromtimestamp(stat.st_mtime),
                file_type='jpeg',
                extension='jpg'
            )
            file_infos.append(file_info)
        
        # Extract metadata
        results = basic_analyzer._extract_metadata_batch(file_infos)
        
        print(f"Batch extraction results: {len(results)} files processed")
        for path, metadata in results.items():
            print(f"  {path.name}: {metadata}")
        
        # Should have results for all files
        assert len(results) == len(temp_image_files), f"Expected {len(temp_image_files)} results, got {len(results)}"
        
        # Each result should have dimensions
        for path, metadata in results.items():
            assert 'ImageWidth' in metadata, f"Missing ImageWidth for {path.name}"
            assert 'ImageHeight' in metadata, f"Missing ImageHeight for {path.name}"
    
    def test_analyze_metadata_resolution_counting(self, basic_analyzer):
        """Test that resolution analysis counts files correctly."""
        # Mock metadata results
        mock_metadata = {
            Path("/test/img1.jpg"): {'ImageWidth': 100, 'ImageHeight': 200},
            Path("/test/img2.jpg"): {'ImageWidth': 100, 'ImageHeight': 200},  # Same resolution
            Path("/test/img3.jpg"): {'ImageWidth': 300, 'ImageHeight': 400},
            Path("/test/img4.jpg"): {'ImageWidth': 500, 'ImageHeight': 600},
        }
        
        # Mock scan result
        mock_scan_result = Mock()
        mock_scan_result.files_by_type = {'raw': [], 'jpeg': [], 'video': []}
        mock_scan_result.size_by_type = {'raw': 0, 'jpeg': 0, 'video': 0}
        
        # Analyze metadata
        analysis_data = basic_analyzer._analyze_metadata(mock_metadata, mock_scan_result)
        
        print(f"Analysis data resolutions: {analysis_data['resolutions']}")
        
        # Check resolution counts
        expected_resolutions = {
            '100x200': 2,  # Two files with same resolution
            '300x400': 1,
            '500x600': 1
        }
        
        assert analysis_data['resolutions'] == expected_resolutions
    
    @patch('photo_ingest.analyzer.FileScanner')
    def test_analyze_directory_integration(self, mock_scanner_class, basic_analyzer, temp_image_files):
        """Test full directory analysis integration."""
        # Mock scanner
        mock_scanner = Mock()
        mock_scanner_class.return_value = mock_scanner
        
        # Create mock file infos
        mock_files = []
        for i, img_path in enumerate(temp_image_files):
            stat = img_path.stat()
            file_info = FileInfo(
                path=img_path,
                size=stat.st_size,
                modified_time=datetime.fromtimestamp(stat.st_mtime),
                file_type='jpeg',
                extension='jpg'
            )
            mock_files.append(file_info)
        
        # Mock scan result
        mock_scan_result = Mock()
        mock_scan_result.total_files = len(temp_image_files)
        mock_scan_result.total_size = sum(f.size for f in mock_files)
        mock_scan_result.files = mock_files
        mock_scan_result.files_by_type = {
            'raw': [],
            'jpeg': mock_files,
            'video': []
        }
        mock_scan_result.size_by_type = {
            'raw': 0,
            'jpeg': sum(f.size for f in mock_files),
            'video': 0
        }
        
        mock_scanner.scan_directory.return_value = mock_scan_result
        
        # Run analysis
        result = basic_analyzer.analyze_directory(Path("/test"))
        
        print(f"Analysis result:")
        print(f"  Total files: {result.total_files}")
        print(f"  Image files analyzed: {result.image_files_analyzed}")
        print(f"  Resolutions: {result.resolutions}")
        
        # Verify results
        assert result.total_files == len(temp_image_files)
        assert result.image_files_analyzed == len(temp_image_files)
        assert len(result.resolutions) > 0, "Should have resolution data"
        
        # Check that we have the expected resolutions
        expected_resolutions = ['100x200', '300x400', '500x600']
        for resolution in expected_resolutions:
            assert resolution in result.resolutions, f"Missing resolution {resolution}"
    
    def test_real_file_extraction_debug(self, basic_analyzer):
        """Debug test to see what happens with a real file."""
        # This test will help debug the real issue
        test_files = [
            "/System/Library/Desktop Pictures/Monterey.heic",  # macOS default image
            "/System/Library/Desktop Pictures/Big Sur.heic",   # Another macOS image
        ]
        
        for test_file in test_files:
            test_path = Path(test_file)
            if test_path.exists():
                print(f"\nTesting real file: {test_file}")
                try:
                    metadata = basic_analyzer._extract_basic_exif(test_path)
                    print(f"  Metadata extracted: {metadata}")
                    
                    if not metadata:
                        print("  No metadata extracted - investigating...")
                        
                        # Try to open with PIL directly
                        try:
                            with Image.open(test_path) as img:
                                print(f"  PIL can open: {img.size}, format: {img.format}")
                                print(f"  EXIF available: {bool(img.getexif())}")
                        except Exception as e:
                            print(f"  PIL failed: {e}")
                    
                except Exception as e:
                    print(f"  Exception during extraction: {e}")
            else:
                print(f"Test file not found: {test_file}")


if __name__ == "__main__":
    # Run specific debug test
    analyzer = PhotoAnalyzer(FileTypes(), PerformanceConfig(parallel_workers=1), detailed=False)
    test = TestPhotoAnalyzer()
    test.test_real_file_extraction_debug(analyzer)