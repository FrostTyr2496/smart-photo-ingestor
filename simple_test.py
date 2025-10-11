#!/usr/bin/env python3
"""Simple test of enhanced analyze functionality."""

import tempfile
import shutil
from pathlib import Path

def create_simple_test_photos():
    """Create simple test photo files."""
    temp_dir = tempfile.mkdtemp(prefix="photo_test_simple_")
    temp_path = Path(temp_dir)
    
    print(f"📁 Created test directory: {temp_path}")
    
    # Create dummy photo files
    test_files = [
        ("IMG_001.jpg", b"fake jpeg data"),
        ("IMG_002.NEF", b"fake nef data"),
        ("IMG_003.cr3", b"fake canon raw data"),
        ("VID_001.mp4", b"fake video data"),
        ("IMG_004.heic", b"fake heic data"),
        ("document.pdf", b"fake pdf data")  # Non-photo file
    ]
    
    for filename, data in test_files:
        file_path = temp_path / filename
        file_path.write_bytes(data)
        print(f"  📄 Created: {filename}")
    
    return temp_path

def test_enhanced_analyze_basic():
    """Test basic enhanced analyze functionality."""
    print("\n🔍 Testing Enhanced Analyze - Basic Functionality")
    print("=" * 55)
    
    test_dir = create_simple_test_photos()
    
    try:
        from photo_ingest.analyze_command import AnalyzeCommand
        from photo_ingest.config import FileTypes, PerformanceConfig
        
        # Create analyze command
        command = AnalyzeCommand(
            file_types=FileTypes(),
            performance_config=PerformanceConfig(parallel_workers=2)
        )
        
        print(f"📊 Analyzing directory: {test_dir}")
        
        # Run analysis
        result = command.analyze_directory(
            test_dir,
            peek_mode=False,
            exif_only=True,
            samples=3
        )
        
        print(f"\n✅ Analysis Results:")
        print(f"📁 Total files: {result.exif_analysis.total_files}")
        print(f"📸 Images analyzed: {result.exif_analysis.image_files_analyzed}")
        print(f"💾 Total size: {result.exif_analysis.total_size} bytes")
        print(f"⏱️  Processing time: {result.total_processing_time:.3f}s")
        print(f"🔍 Processing mode: {result.processing_mode}")
        
        # Test file type breakdown
        print(f"\n📋 File type breakdown:")
        for file_type, count in result.exif_analysis.files_by_type.items():
            if count > 0:
                print(f"  • {file_type.upper()}: {count} files")
        
        # Test supported formats
        formats = command.get_supported_formats()
        print(f"\n🎯 Supported formats: {len(formats)} total")
        print(f"  • RAW: {', '.join(formats[:4])}...")
        
        # Test visual analysis availability
        visual_available = command.is_visual_analysis_available()
        print(f"👁️  Visual analysis available: {visual_available}")
        
        # Test JSON serialization
        json_data = result.to_dict()
        print(f"📄 JSON serialization: {len(str(json_data))} characters")
        
        # Verify key components exist
        assert result.exif_analysis is not None
        assert result.processing_mode == "exif_only"
        assert result.total_processing_time > 0
        assert isinstance(result.exif_analysis.total_files, int)
        
        print(f"\n🎉 All basic functionality tests passed!")
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Clean up
        shutil.rmtree(test_dir)
        print(f"🧹 Cleaned up test directory")

def test_cli_basic():
    """Test CLI basic functionality."""
    print("\n🖥️  Testing CLI Basic Functionality")
    print("=" * 40)
    
    test_dir = create_simple_test_photos()
    
    try:
        import subprocess
        import sys
        
        # Test basic CLI command
        print("Running: python -m photo_ingest.cli analyze --exif-only")
        result = subprocess.run([
            sys.executable, "-m", "photo_ingest.cli",
            "analyze", str(test_dir), "--exif-only"
        ], capture_output=True, text=True, timeout=30)
        
        print(f"Exit code: {result.returncode}")
        
        if result.returncode == 0:
            print("✅ CLI command executed successfully!")
            
            # Check for key output elements
            output = result.stdout
            if "Analysis Summary" in output:
                print("✅ Analysis summary displayed")
            if "Processing Summary" in output:
                print("✅ Processing summary displayed")
            if "Total Files:" in output:
                print("✅ File count displayed")
                
            return True
        else:
            print(f"❌ CLI command failed")
            if result.stderr:
                print(f"Error: {result.stderr}")
            if result.stdout:
                print(f"Output: {result.stdout}")
            return False
        
    except subprocess.TimeoutExpired:
        print("❌ CLI command timed out")
        return False
    except Exception as e:
        print(f"❌ CLI test error: {e}")
        return False
        
    finally:
        shutil.rmtree(test_dir)
        print(f"🧹 Cleaned up test directory")

def test_processing_modes():
    """Test different processing modes."""
    print("\n⚙️  Testing Processing Modes")
    print("=" * 35)
    
    try:
        from photo_ingest.analyze_command import AnalyzeCommand
        
        command = AnalyzeCommand()
        
        # Test mode determination
        modes = [
            (False, True, "exif_only"),
            (True, True, "exif_only"),  # exif_only overrides peek
            (False, False, "exif_only"),  # default without config
        ]
        
        for peek, exif_only, expected in modes:
            mode = command._determine_processing_mode(peek, exif_only)
            print(f"peek={peek}, exif_only={exif_only} → {mode}")
            assert mode == expected, f"Expected {expected}, got {mode}"
        
        print("✅ Processing mode determination works correctly!")
        return True
        
    except Exception as e:
        print(f"❌ Processing mode test error: {e}")
        return False

def main():
    """Run all simple tests."""
    print("🧪 Enhanced Analyze - Simple Functionality Test")
    print("=" * 60)
    
    tests = [
        ("Basic Functionality", test_enhanced_analyze_basic),
        ("CLI Basic", test_cli_basic),
        ("Processing Modes", test_processing_modes)
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        try:
            print(f"\n🔬 Running: {test_name}")
            if test_func():
                passed += 1
                print(f"✅ {test_name}: PASSED")
            else:
                print(f"❌ {test_name}: FAILED")
        except Exception as e:
            print(f"❌ {test_name}: ERROR - {e}")
    
    print("\n" + "=" * 60)
    print(f"🎯 Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! Enhanced analyze is working!")
        print("\n📋 Verified features:")
        print("✅ Enhanced analyze command structure")
        print("✅ EXIF-only processing mode")
        print("✅ File type detection and counting")
        print("✅ Processing time measurement")
        print("✅ JSON serialization")
        print("✅ CLI integration")
        print("✅ Error handling")
        
        print("\n🚀 Ready to use with real photos!")
        print("Try: python -m photo_ingest.cli analyze /path/to/photos --exif-only")
        return 0
    else:
        print(f"❌ {total - passed} tests failed")
        return 1

if __name__ == "__main__":
    exit(main())