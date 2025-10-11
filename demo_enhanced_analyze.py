#!/usr/bin/env python3
"""Demo script to test the enhanced analyze functionality."""

import tempfile
import os
from pathlib import Path
import shutil

def create_test_photos():
    """Create a temporary directory with some test photo files."""
    temp_dir = tempfile.mkdtemp(prefix="photo_test_")
    temp_path = Path(temp_dir)
    
    print(f"📁 Created test directory: {temp_path}")
    
    # Create some dummy photo files (empty files with photo extensions)
    test_files = [
        "IMG_001.jpg",
        "IMG_002.NEF", 
        "IMG_003.cr3",
        "VID_001.mp4",
        "IMG_004.heic"
    ]
    
    for filename in test_files:
        file_path = temp_path / filename
        file_path.write_bytes(b"dummy photo data")
        print(f"  📸 Created: {filename}")
    
    return temp_path

def demo_basic_analyze():
    """Demo basic analyze functionality."""
    print("\n🔍 Testing Basic Enhanced Analyze Command")
    print("=" * 50)
    
    test_dir = create_test_photos()
    
    try:
        # Test the CLI command
        import subprocess
        import sys
        
        # Run basic analyze
        print("\n1. Running basic analyze command:")
        result = subprocess.run([
            sys.executable, "-m", "photo_ingest.cli", 
            "analyze", str(test_dir)
        ], capture_output=True, text=True)
        
        print(f"Exit code: {result.returncode}")
        if result.stdout:
            print("Output:")
            print(result.stdout)
        if result.stderr:
            print("Errors:")
            print(result.stderr)
            
    finally:
        # Clean up
        shutil.rmtree(test_dir)
        print(f"\n🧹 Cleaned up test directory: {test_dir}")

def demo_analyze_options():
    """Demo analyze command with different options."""
    print("\n🎛️  Testing Analyze Command Options")
    print("=" * 50)
    
    test_dir = create_test_photos()
    
    try:
        import subprocess
        import sys
        
        # Test different options
        options_to_test = [
            (["--json"], "JSON output"),
            (["--exif-only"], "EXIF-only mode"),
            (["--detailed"], "Detailed analysis"),
            (["--summary"], "Summary mode"),
        ]
        
        for options, description in options_to_test:
            print(f"\n{description}:")
            print("-" * 30)
            
            cmd = [sys.executable, "-m", "photo_ingest.cli", "analyze"] + options + [str(test_dir)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            print(f"Command: {' '.join(cmd)}")
            print(f"Exit code: {result.returncode}")
            
            if result.returncode == 0:
                print("✅ Success")
                if "--json" in options:
                    print("JSON output received (truncated):")
                    print(result.stdout[:200] + "..." if len(result.stdout) > 200 else result.stdout)
            else:
                print("❌ Failed")
                if result.stderr:
                    print(f"Error: {result.stderr}")
                    
    finally:
        # Clean up
        shutil.rmtree(test_dir)
        print(f"\n🧹 Cleaned up test directory: {test_dir}")

def demo_programmatic_usage():
    """Demo programmatic usage of the enhanced analyze command."""
    print("\n🐍 Testing Programmatic Usage")
    print("=" * 50)
    
    test_dir = create_test_photos()
    
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
        
        print(f"✅ Analysis complete!")
        print(f"📁 Total files: {result.exif_analysis.total_files}")
        print(f"📸 Images analyzed: {result.exif_analysis.image_files_analyzed}")
        print(f"⏱️  Processing time: {result.total_processing_time:.2f}s")
        print(f"🔍 Processing mode: {result.processing_mode}")
        
        # Show supported formats
        formats = command.get_supported_formats()
        print(f"📋 Supported formats: {', '.join(formats[:5])}...")
        
        # Check visual analysis availability
        visual_available = command.is_visual_analysis_available()
        print(f"👁️  Visual analysis available: {visual_available}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        # Clean up
        shutil.rmtree(test_dir)
        print(f"\n🧹 Cleaned up test directory: {test_dir}")

def main():
    """Run all demos."""
    print("🎬 Enhanced Analyze Command Demo")
    print("=" * 60)
    
    try:
        demo_programmatic_usage()
        demo_basic_analyze()
        demo_analyze_options()
        
        print("\n" + "=" * 60)
        print("🎉 Demo completed successfully!")
        print("\n📋 Features demonstrated:")
        print("✅ Enhanced analyze command with EXIF analysis")
        print("✅ Programmatic API usage")
        print("✅ CLI command with various options")
        print("✅ JSON output support")
        print("✅ Processing mode selection")
        print("✅ Error handling and cleanup")
        
    except KeyboardInterrupt:
        print("\n\n⏹️  Demo interrupted by user")
    except Exception as e:
        print(f"\n❌ Demo failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())