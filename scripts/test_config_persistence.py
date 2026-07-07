#!/usr/bin/env python3
"""Test config persistence functionality."""

import tempfile
import shutil
from pathlib import Path

def test_config_persistence():
    """Test the config persistence feature."""
    print("🧪 Testing Config Persistence Feature")
    print("=" * 45)
    
    try:
        from photo_ingest.settings import SettingsManager
        
        # Create a temporary settings manager to avoid affecting real settings
        temp_dir = Path(tempfile.mkdtemp())
        settings_manager = SettingsManager()
        settings_manager.settings_dir = temp_dir
        settings_manager.settings_file = temp_dir / "settings.json"
        
        print(f"📁 Using temp settings dir: {temp_dir}")
        
        # Test 1: No config initially
        config_path = settings_manager.get_config_path()
        assert config_path is None, "Should have no config initially"
        print("✅ Test 1: No initial config - PASSED")
        
        # Test 2: Set config path
        test_config_path = "/path/to/test/config.yaml"
        settings_manager.set_config_path(test_config_path)
        
        saved_path = settings_manager.get_config_path()
        assert saved_path is not None, "Config path should be saved"
        assert Path(saved_path).name == "config.yaml", "Config filename should match"
        print("✅ Test 2: Set config path - PASSED")
        
        # Test 3: Persistence across instances
        new_settings_manager = SettingsManager()
        new_settings_manager.settings_dir = temp_dir
        new_settings_manager.settings_file = temp_dir / "settings.json"
        
        loaded_path = new_settings_manager.get_config_path()
        assert loaded_path == saved_path, "Config should persist across instances"
        print("✅ Test 3: Persistence across instances - PASSED")
        
        # Test 4: Clear config
        settings_manager.clear_config_path()
        cleared_path = settings_manager.get_config_path()
        assert cleared_path is None, "Config should be cleared"
        print("✅ Test 4: Clear config - PASSED")
        
        # Test 5: General settings
        settings_manager.set_setting("test_key", "test_value")
        value = settings_manager.get_setting("test_key")
        assert value == "test_value", "General settings should work"
        
        default_value = settings_manager.get_setting("nonexistent", "default")
        assert default_value == "default", "Default values should work"
        print("✅ Test 5: General settings - PASSED")
        
        print("\n🎉 All config persistence tests passed!")
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Clean up
        if 'temp_dir' in locals():
            shutil.rmtree(temp_dir, ignore_errors=True)
            print(f"🧹 Cleaned up temp directory")

def test_cli_config_commands():
    """Test CLI config commands."""
    print("\n🖥️  Testing CLI Config Commands")
    print("=" * 35)
    
    try:
        import subprocess
        import sys
        
        # Test config help
        result = subprocess.run([
            sys.executable, "-m", "photo_ingest.cli", "config", "--help"
        ], capture_output=True, text=True)
        
        if result.returncode == 0:
            print("✅ Config command help - PASSED")
            if "set" in result.stdout and "show" in result.stdout:
                print("✅ Config subcommands available - PASSED")
        else:
            print(f"❌ Config help failed: {result.stderr}")
            return False
        
        # Test config show (should show no config initially)
        result = subprocess.run([
            sys.executable, "-m", "photo_ingest.cli", "config", "show"
        ], capture_output=True, text=True)
        
        if result.returncode == 0:
            print("✅ Config show command - PASSED")
            if "No default config set" in result.stdout:
                print("✅ Shows no config when none set - PASSED")
        else:
            print(f"❌ Config show failed: {result.stderr}")
            return False
        
        print("\n🎉 CLI config commands working!")
        return True
        
    except Exception as e:
        print(f"❌ CLI test failed: {e}")
        return False

def main():
    """Run all tests."""
    print("🔧 Config Persistence Feature Tests")
    print("=" * 50)
    
    tests_passed = 0
    total_tests = 2
    
    if test_config_persistence():
        tests_passed += 1
    
    if test_cli_config_commands():
        tests_passed += 1
    
    print("\n" + "=" * 50)
    print(f"🎯 Results: {tests_passed}/{total_tests} test suites passed")
    
    if tests_passed == total_tests:
        print("🎉 Config persistence feature is working!")
        print("\n📋 New features available:")
        print("✅ Automatic config path saving")
        print("✅ Config management commands")
        print("✅ Persistent settings storage")
        
        print("\n🚀 Usage examples:")
        print("# Set default config (only need to do once)")
        print("python -m photo_ingest.cli config set example_config.yaml")
        print("")
        print("# Now peek mode works without --config flag")
        print("python -m photo_ingest.cli analyze /path/to/photos --peek")
        print("")
        print("# Show current config")
        print("python -m photo_ingest.cli config show")
        
        return 0
    else:
        print("❌ Some tests failed")
        return 1

if __name__ == "__main__":
    exit(main())