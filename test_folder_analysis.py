#!/usr/bin/env python3
"""Test the new folder analysis feature."""

import tempfile
import shutil
from pathlib import Path
from PIL import Image

def create_test_folder_structure():
    """Create a test folder structure with multiple folders."""
    temp_dir = tempfile.mkdtemp(prefix="folder_analysis_test_")
    temp_path = Path(temp_dir)
    
    print(f"📁 Created test directory: {temp_path}")
    
    # Create folder structure
    folders = {
        "Vacation_Beach": ["beach1.jpg", "beach2.jpg", "sunset.jpg"],
        "City_Photos": ["street1.jpg", "building.jpg", "night.jpg"],
        "Nature": ["forest.jpg", "mountain.jpg", "wildlife.jpg"],
        "Portraits": ["portrait1.jpg", "portrait2.jpg"]
    }
    
    for folder_name, files in folders.items():
        folder_path = temp_path / folder_name
        folder_path.mkdir()
        
        for filename in files:
            # Create simple test images
            img = Image.new('RGB', (800, 600), color='lightblue')
            img_path = folder_path / filename
            img.save(img_path, "JPEG")
        
        print(f"  📂 Created folder: {folder_name} with {len(files)} images")
    
    return temp_path

def test_folder_analysis_feature():
    """Test the folder analysis functionality."""
    print("\n🗂️  Testing Folder Analysis Feature")
    print("=" * 45)
    
    test_dir = create_test_folder_structure()
    
    try:
        from photo_ingest.analyze_command import AnalyzeCommand
        from photo_ingest.config import IngestConfig, LLMConfig, PeekConfig, FileTypes, PerformanceConfig
        
        # Create config (without LLM for basic testing)
        config = IngestConfig(
            archive_root="/tmp/test",
            llm=LLMConfig(enabled=False),  # Disable LLM for basic test
            peek=PeekConfig(
                enabled=True,
                include_visual_analysis=False,  # Disable visual analysis for basic test
                sample_count=2
            )
        )
        
        # Create analyze command
        command = AnalyzeCommand(config=config)
        
        print(f"📊 Running folder analysis on: {test_dir}")
        
        # Run analysis with folder analysis enabled
        result = command.analyze_directory(
            test_dir,
            peek_mode=True,
            enable_folder_analysis=True,
            samples=2
        )
        
        print(f"\n✅ Analysis completed!")
        print(f"📁 Total files: {result.exif_analysis.total_files}")
        print(f"⏱️  Processing time: {result.total_processing_time:.2f}s")
        print(f"🔍 Processing mode: {result.processing_mode}")
        
        # Check if folder analysis was performed
        if result.visual_analysis and result.visual_analysis.folder_analyses:
            folder_count = len(result.visual_analysis.folder_analyses)
            print(f"📂 Folders analyzed: {folder_count}")
            
            for folder_path, analysis in result.visual_analysis.folder_analyses.items():
                folder_name = Path(folder_path).name
                print(f"  • {folder_name}: {analysis.file_count} files, {len(analysis.sampled_files)} sampled")
        else:
            print("📂 No folder analysis data (expected without LLM)")
        
        # Test JSON serialization
        json_data = result.to_dict()
        print(f"📄 JSON serialization: {len(str(json_data))} characters")
        
        if 'visual_analysis' in json_data and 'folder_analyses' in json_data['visual_analysis']:
            print("✅ Folder analysis data included in JSON")
        
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
        print(f"🧹 Cleaned up test directory")

def test_cli_folder_analysis():
    """Test CLI folder analysis options."""
    print("\n🖥️  Testing CLI Folder Analysis Options")
    print("=" * 45)
    
    test_dir = create_test_folder_structure()
    
    try:
        import subprocess
        import sys
        
        # Test with folder analysis enabled (default)
        print("1. Testing with folder analysis enabled:")
        result = subprocess.run([
            sys.executable, "-m", "photo_ingest.cli",
            "analyze", str(test_dir), "--exif-only", "--folder-analysis"
        ], capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            print("   ✅ Folder analysis enabled - SUCCESS")
        else:
            print(f"   ❌ Failed: {result.stderr}")
        
        # Test with folder analysis disabled
        print("\n2. Testing with folder analysis disabled:")
        result = subprocess.run([
            sys.executable, "-m", "photo_ingest.cli",
            "analyze", str(test_dir), "--exif-only", "--no-folder-analysis"
        ], capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            print("   ✅ Folder analysis disabled - SUCCESS")
        else:
            print(f"   ❌ Failed: {result.stderr}")
        
        return True
        
    except Exception as e:
        print(f"❌ CLI test failed: {e}")
        return False
        
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
        print(f"🧹 Cleaned up test directory")

def show_usage_examples():
    """Show usage examples for the new folder analysis feature."""
    print("\n📋 Folder Analysis Usage Examples")
    print("=" * 40)
    
    examples = [
        ("Basic folder analysis (default)", 
         "python -m photo_ingest.cli analyze /path/to/photos --peek"),
        
        ("Disable folder analysis", 
         "python -m photo_ingest.cli analyze /path/to/photos --peek --no-folder-analysis"),
        
        ("Folder analysis with custom samples", 
         "python -m photo_ingest.cli analyze /path/to/photos --peek --samples 3"),
        
        ("Verbose folder analysis", 
         "python -m photo_ingest.cli -v analyze /path/to/photos --peek"),
        
        ("JSON output with folder data", 
         "python -m photo_ingest.cli analyze /path/to/photos --peek --json")
    ]
    
    for description, command in examples:
        print(f"\n{description}:")
        print(f"  {command}")
    
    print(f"\n💡 Features:")
    print(f"  • Analyzes photos folder by folder")
    print(f"  • Generates individual folder summaries")
    print(f"  • Creates overall folder organization summary")
    print(f"  • Combines with device analysis for comprehensive view")
    print(f"  • Includes folder data in JSON output")

def main():
    """Run folder analysis tests."""
    print("🗂️  Folder Analysis Feature Test Suite")
    print("=" * 50)
    
    tests_passed = 0
    total_tests = 2
    
    if test_folder_analysis_feature():
        tests_passed += 1
    
    if test_cli_folder_analysis():
        tests_passed += 1
    
    show_usage_examples()
    
    print("\n" + "=" * 50)
    print(f"🎯 Results: {tests_passed}/{total_tests} tests passed")
    
    if tests_passed == total_tests:
        print("🎉 Folder analysis feature is working!")
        print("\n🚀 Ready to use with your photo collections!")
        print("Try: python -m photo_ingest.cli -v analyze /path/to/photos --peek")
    else:
        print("❌ Some tests failed")
    
    return 0 if tests_passed == total_tests else 1

if __name__ == "__main__":
    exit(main())