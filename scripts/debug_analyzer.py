#!/usr/bin/env python3
"""Debug script to investigate analyzer issues."""

import sys
import tempfile
from pathlib import Path
from datetime import datetime
from PIL import Image

# Add the photo_ingest module to the path
sys.path.insert(0, str(Path(__file__).parent))

from photo_ingest.analyzer import PhotoAnalyzer
from photo_ingest.config import FileTypes, PerformanceConfig
from photo_ingest.file_scanner import FileInfo


def test_basic_image_extraction():
    """Test basic image extraction with created test images."""
    print("=== Testing Basic Image Extraction ===")
    
    analyzer = PhotoAnalyzer(FileTypes(), PerformanceConfig(parallel_workers=1), detailed=False)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Create test images
        test_images = []
        
        # Create a 100x200 image
        img1 = Image.new('RGB', (100, 200), color='red')
        img1_path = temp_path / "test1.jpg"
        img1.save(img1_path)
        test_images.append(img1_path)
        
        # Create a 300x400 image
        img2 = Image.new('RGB', (300, 400), color='blue')
        img2_path = temp_path / "test2.jpg"
        img2.save(img2_path)
        test_images.append(img2_path)
        
        print(f"Created {len(test_images)} test images")
        
        # Test extraction
        for img_path in test_images:
            print(f"\nTesting {img_path.name}:")
            metadata = analyzer._extract_basic_exif(img_path)
            print(f"  Metadata: {metadata}")
            
            if 'ImageWidth' in metadata and 'ImageHeight' in metadata:
                print(f"  ✅ Dimensions: {metadata['ImageWidth']}x{metadata['ImageHeight']}")
            else:
                print(f"  ❌ Missing dimensions!")


def test_batch_extraction():
    """Test batch metadata extraction."""
    print("\n=== Testing Batch Extraction ===")
    
    analyzer = PhotoAnalyzer(FileTypes(), PerformanceConfig(parallel_workers=1), detailed=False)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Create test images
        test_images = []
        file_infos = []
        
        for i in range(3):
            img = Image.new('RGB', (100 * (i+1), 200 * (i+1)), color=['red', 'green', 'blue'][i])
            img_path = temp_path / f"test{i+1}.jpg"
            img.save(img_path)
            test_images.append(img_path)
            
            # Create FileInfo
            stat = img_path.stat()
            file_info = FileInfo(
                path=img_path,
                size=stat.st_size,
                modified_time=datetime.fromtimestamp(stat.st_mtime),
                file_type='jpeg',
                extension='jpg'
            )
            file_infos.append(file_info)
        
        print(f"Created {len(test_images)} test images")
        
        # Test batch extraction
        results = analyzer._extract_metadata_batch(file_infos)
        
        print(f"Batch extraction results: {len(results)} files processed")
        for path, metadata in results.items():
            print(f"  {path.name}: {metadata}")
        
        if len(results) != len(test_images):
            print(f"❌ Expected {len(test_images)} results, got {len(results)}")
        else:
            print(f"✅ Got expected number of results")


def test_real_files():
    """Test with real system files if available."""
    print("\n=== Testing Real Files ===")
    
    analyzer = PhotoAnalyzer(FileTypes(), PerformanceConfig(parallel_workers=1), detailed=False)
    
    # Try some common macOS image locations
    test_files = [
        "/System/Library/Desktop Pictures/Monterey.heic",
        "/System/Library/Desktop Pictures/Big Sur.heic",
        "/System/Library/Desktop Pictures/Catalina.heic",
        "/System/Library/Desktop Pictures/Mojave.heic",
    ]
    
    found_files = []
    for test_file in test_files:
        test_path = Path(test_file)
        if test_path.exists():
            found_files.append(test_path)
    
    if not found_files:
        print("No system test files found")
        return
    
    print(f"Found {len(found_files)} system files to test")
    
    for test_path in found_files:
        print(f"\nTesting real file: {test_path.name}")
        try:
            metadata = analyzer._extract_basic_exif(test_path)
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
            else:
                if 'ImageWidth' in metadata and 'ImageHeight' in metadata:
                    print(f"  ✅ Dimensions: {metadata['ImageWidth']}x{metadata['ImageHeight']}")
                else:
                    print(f"  ❌ Missing dimensions in metadata")
            
        except Exception as e:
            print(f"  Exception during extraction: {e}")


def test_analyze_metadata_function():
    """Test the _analyze_metadata function directly."""
    print("\n=== Testing Analyze Metadata Function ===")
    
    analyzer = PhotoAnalyzer(FileTypes(), PerformanceConfig(parallel_workers=1), detailed=False)
    
    # Create mock metadata results
    mock_metadata = {
        Path("/test/img1.jpg"): {'ImageWidth': 100, 'ImageHeight': 200},
        Path("/test/img2.jpg"): {'ImageWidth': 100, 'ImageHeight': 200},  # Same resolution
        Path("/test/img3.jpg"): {'ImageWidth': 300, 'ImageHeight': 400},
        Path("/test/img4.jpg"): {'ImageWidth': 500, 'ImageHeight': 600},
    }
    
    print(f"Mock metadata has {len(mock_metadata)} entries")
    for path, metadata in mock_metadata.items():
        print(f"  {path.name}: {metadata}")
    
    # Create mock scan result
    class MockScanResult:
        def __init__(self):
            self.files_by_type = {'raw': [], 'jpeg': [], 'video': []}
            self.size_by_type = {'raw': 0, 'jpeg': 0, 'video': 0}
            # Create mock file infos for the files we're testing
            self.files = [
                type('MockFileInfo', (), {'extension': 'jpg'})(),
                type('MockFileInfo', (), {'extension': 'jpg'})(),
                type('MockFileInfo', (), {'extension': 'jpg'})(),
                type('MockFileInfo', (), {'extension': 'jpg'})(),
            ]
    
    mock_scan_result = MockScanResult()
    
    # Analyze metadata
    analysis_data = analyzer._analyze_metadata(mock_metadata, mock_scan_result)
    
    print(f"Analysis data resolutions: {analysis_data['resolutions']}")
    
    # Check resolution counts
    expected_resolutions = {
        '100x200': 2,  # Two files with same resolution
        '300x400': 1,
        '500x600': 1
    }
    
    if analysis_data['resolutions'] == expected_resolutions:
        print("✅ Resolution counting works correctly")
    else:
        print(f"❌ Resolution counting failed")
        print(f"  Expected: {expected_resolutions}")
        print(f"  Got: {analysis_data['resolutions']}")


def main():
    """Run all debug tests."""
    print("ANALYZER DEBUG TESTS")
    print("=" * 50)
    
    try:
        test_basic_image_extraction()
        test_batch_extraction()
        test_real_files()
        test_analyze_metadata_function()
        
        print("\n" + "=" * 50)
        print("Debug tests completed!")
        
    except Exception as e:
        print(f"Error during testing: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()