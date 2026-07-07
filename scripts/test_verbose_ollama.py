#!/usr/bin/env python3
"""Test verbose Ollama integration."""

import tempfile
import shutil
from pathlib import Path
from PIL import Image
import logging

def create_test_image():
    """Create a simple test image."""
    temp_dir = tempfile.mkdtemp(prefix="ollama_test_")
    temp_path = Path(temp_dir)
    
    # Create a simple test image
    img = Image.new('RGB', (800, 600), color='lightblue')
    img_path = temp_path / "test_image.jpg"
    img.save(img_path, "JPEG")
    
    print(f"📸 Created test image: {img_path}")
    return temp_path, img_path

def test_ollama_connection():
    """Test basic Ollama connection."""
    print("🔗 Testing Ollama Connection")
    print("=" * 35)
    
    try:
        import requests
        
        # Test if Ollama is running
        response = requests.get("http://localhost:11434/api/tags", timeout=5)
        if response.status_code == 200:
            models = response.json()
            print("✅ Ollama is running")
            
            available_models = [model['name'] for model in models.get('models', [])]
            print(f"📋 Available models: {len(available_models)}")
            
            # Check for required models
            required_models = ['llama3.1:8b', 'llava:7b']
            for model in required_models:
                if any(model in available for available in available_models):
                    print(f"✅ {model}: Available")
                else:
                    print(f"❌ {model}: Not found")
                    print(f"   Run: ollama pull {model}")
            
            return True
        else:
            print(f"❌ Ollama responded with status {response.status_code}")
            return False
            
    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to Ollama")
        print("💡 Make sure Ollama is running: ollama serve")
        return False
    except Exception as e:
        print(f"❌ Error testing Ollama: {e}")
        return False

def test_verbose_analyze():
    """Test analyze command with verbose logging."""
    print("\n🔍 Testing Verbose Analyze")
    print("=" * 30)
    
    # Enable verbose logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    test_dir, img_path = create_test_image()
    
    try:
        from photo_ingest.analyze_command import AnalyzeCommand
        from photo_ingest.config import IngestConfig, LLMConfig, PeekConfig
        
        # Create config with LLM enabled
        config = IngestConfig(
            archive_root="/tmp/test",
            llm=LLMConfig(
                enabled=True,
                provider="ollama",
                vision_model="llava:7b",
                retry_attempts=1,  # Reduce retries for testing
                retry_delay=2.0
            ),
            peek=PeekConfig(
                enabled=True,
                include_visual_analysis=True,
                sample_count=1
            )
        )
        
        # Create analyze command
        command = AnalyzeCommand(config=config)
        command.set_verbose(True)  # Enable verbose mode
        
        print(f"📊 Running verbose analysis on: {test_dir}")
        print("🔍 Watch for detailed Ollama request logs below...")
        print("-" * 50)
        
        # Run analysis
        result = command.analyze_directory(
            test_dir,
            peek_mode=True,
            samples=1
        )
        
        print("-" * 50)
        print(f"✅ Analysis completed!")
        print(f"📁 Total files: {result.exif_analysis.total_files}")
        print(f"⏱️  Processing time: {result.total_processing_time:.2f}s")
        
        if result.visual_analysis:
            print(f"👁️  Visual analysis: {len(result.visual_analysis.device_analyses)} devices")
            if result.visual_analysis.processing_stats:
                stats = result.visual_analysis.processing_stats
                print(f"🤖 LLM calls made: {stats.llm_calls_made}")
                print(f"❌ Errors: {stats.errors_encountered}")
        
        return True
        
    except Exception as e:
        print(f"❌ Verbose analyze test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
        print(f"🧹 Cleaned up test directory")

def show_timeout_info():
    """Show timeout information."""
    print("\n⏱️  Timeout Information")
    print("=" * 25)
    print("Current timeout settings:")
    print("• Text generation: 60 seconds")
    print("• Vision analysis: 120 seconds (2 minutes)")
    print("")
    print("Reasonable timeouts for 1-2MP photos:")
    print("• First-time model load: 30-60 seconds")
    print("• Subsequent requests: 10-30 seconds")
    print("• Complex images: 30-60 seconds")
    print("")
    print("💡 Tips for better performance:")
    print("• Keep Ollama running (ollama serve)")
    print("• Use smaller models if available (llava:7b vs llava:13b)")
    print("• Ensure sufficient RAM (8GB+ recommended)")
    print("• Use --verbose flag to see detailed progress")

def main():
    """Run Ollama debugging tests."""
    print("🦙 Ollama Integration Debug Tool")
    print("=" * 40)
    
    tests_passed = 0
    total_tests = 2
    
    # Test 1: Ollama connection
    if test_ollama_connection():
        tests_passed += 1
    
    # Test 2: Verbose analyze (only if Ollama is available)
    if tests_passed > 0:
        if test_verbose_analyze():
            tests_passed += 1
    else:
        print("\n⏭️  Skipping verbose analyze test (Ollama not available)")
    
    # Show timeout info
    show_timeout_info()
    
    print("\n" + "=" * 40)
    print(f"🎯 Results: {tests_passed}/{total_tests} tests passed")
    
    if tests_passed == total_tests:
        print("🎉 Ollama integration is working!")
        print("\n🚀 Usage with verbose logging:")
        print("python -m photo_ingest.cli analyze /path/to/photos --config example_config.yaml --peek -v")
        print("python -m photo_ingest.cli analyze /path/to/photos --config example_config.yaml --peek -vv  # Extra verbose")
    else:
        print("❌ Some tests failed. Check Ollama setup.")
        print("\n🔧 Troubleshooting:")
        print("1. Start Ollama: ollama serve")
        print("2. Pull models: ollama pull llava:7b")
        print("3. Test manually: ollama run llava:7b")
    
    return 0 if tests_passed == total_tests else 1

if __name__ == "__main__":
    exit(main())