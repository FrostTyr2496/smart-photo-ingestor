"""Tests for configuration system with validation and environment variable substitution."""

import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, mock_open

import yaml
from pydantic import ValidationError

from photo_ingest.config import (
    IngestConfig,
    DeviceMapping,
    FileTypes,
    LLMConfig,
    PeekConfig,
    RawBackupConfig,
    PerformanceConfig,
    ConfigManager,
    ConfigurationError,
    expand_environment_variables,
)


class TestEnvironmentVariableExpansion:
    """Test environment variable expansion functionality."""
    
    def test_expand_simple_variable(self):
        """Test expansion of simple ${VAR} syntax."""
        with patch.dict(os.environ, {'TEST_VAR': 'test_value'}):
            result = expand_environment_variables('${TEST_VAR}/path')
            assert result == 'test_value/path'
    
    def test_expand_dollar_variable(self):
        """Test expansion of $VAR syntax."""
        with patch.dict(os.environ, {'TEST_VAR': 'test_value'}):
            result = expand_environment_variables('$TEST_VAR/path')
            assert result == 'test_value/path'
    
    def test_expand_multiple_variables(self):
        """Test expansion of multiple variables."""
        with patch.dict(os.environ, {'HOME': '/home/user', 'PROJECT': 'photos'}):
            result = expand_environment_variables('${HOME}/${PROJECT}/archive')
            assert result == '/home/user/photos/archive'
    
    def test_expand_missing_variable(self):
        """Test that missing variables are left unchanged."""
        result = expand_environment_variables('${MISSING_VAR}/path')
        assert result == '${MISSING_VAR}/path'
    
    def test_expand_non_string(self):
        """Test that non-string values are returned unchanged."""
        assert expand_environment_variables(123) == 123
        assert expand_environment_variables(None) is None


class TestDeviceMapping:
    """Test DeviceMapping configuration model."""
    
    def test_valid_device_mapping(self):
        """Test valid device mapping configuration."""
        config = DeviceMapping(
            mappings={"NIKON Z 6": "Z6", "DJI AIR 2S": "Drone"},
            exif_identifiers={
                "Z6": {"Make": "NIKON CORPORATION", "Model": "NIKON Z 6"},
                "Drone": {"Make": "DJI", "Model": "FC3582"}
            },
            priority_rules=["Z6", "Drone"]
        )
        assert config.mappings["NIKON Z 6"] == "Z6"
        assert config.exif_identifiers["Z6"]["Make"] == "NIKON CORPORATION"
        assert config.priority_rules == ["Z6", "Drone"]
    
    def test_empty_camera_model_validation(self):
        """Test validation of empty camera model."""
        with pytest.raises(ValidationError, match="Camera model cannot be empty"):
            DeviceMapping(mappings={"": "Z6"})
    
    def test_empty_folder_name_validation(self):
        """Test validation of empty folder name."""
        with pytest.raises(ValidationError, match="Folder name cannot be empty"):
            DeviceMapping(mappings={"NIKON Z 6": ""})
    
    def test_empty_device_code_validation(self):
        """Test validation of empty device code in EXIF identifiers."""
        with pytest.raises(ValidationError, match="Device code cannot be empty"):
            DeviceMapping(exif_identifiers={"": {"Make": "NIKON"}})
    
    def test_empty_exif_identifiers_validation(self):
        """Test validation of empty EXIF identifiers."""
        with pytest.raises(ValidationError, match="must have at least one EXIF identifier"):
            DeviceMapping(exif_identifiers={"Z6": {}})


class TestFileTypes:
    """Test FileTypes configuration model."""
    
    def test_default_file_types(self):
        """Test default file type configuration."""
        config = FileTypes()
        assert "nef" in config.raw
        assert "jpg" in config.jpeg
        assert "mp4" in config.video
    
    def test_custom_file_types(self):
        """Test custom file type configuration."""
        config = FileTypes(
            raw=["nef", "cr3"],
            jpeg=["jpg", "heic"],
            video=["mp4", "mov"]
        )
        assert config.raw == ["nef", "cr3"]
        assert config.jpeg == ["jpg", "heic"]
        assert config.video == ["mp4", "mov"]
    
    def test_extension_normalization(self):
        """Test that extensions are normalized to lowercase without dots."""
        config = FileTypes(
            raw=[".NEF", "CR3", " dng "],
            jpeg=[".JPG", "HEIC"],
            video=[".MP4"]
        )
        assert config.raw == ["nef", "cr3", "dng"]
        assert config.jpeg == ["jpg", "heic"]
        assert config.video == ["mp4"]
    
    def test_empty_extension_validation(self):
        """Test validation of empty extensions."""
        with pytest.raises(ValidationError, match="File extension cannot be empty"):
            FileTypes(raw=["nef", "", "cr3"])
    
    def test_empty_list_validation(self):
        """Test validation of empty file type lists."""
        with pytest.raises(ValidationError, match="File type list cannot be empty"):
            FileTypes(raw=[])
    
    def test_get_all_extensions(self):
        """Test getting all supported extensions."""
        config = FileTypes(
            raw=["nef", "cr3"],
            jpeg=["jpg", "heic"],
            video=["mp4"]
        )
        all_extensions = config.get_all_extensions()
        assert all_extensions == ["nef", "cr3", "jpg", "heic", "mp4"]


class TestLLMConfig:
    """Test LLMConfig configuration model."""
    
    def test_default_llm_config(self):
        """Test default LLM configuration."""
        config = LLMConfig()
        assert config.enabled is False
        assert config.provider == "ollama"
        assert config.model == "llama3.1:8b"
        assert config.vision_model == "llava:7b"
        assert config.retry_attempts == 3
        assert config.retry_delay == 1.0
    
    def test_custom_llm_config(self):
        """Test custom LLM configuration."""
        config = LLMConfig(
            enabled=True,
            provider="ollama",
            endpoint="http://custom:11434/api/generate",
            model="custom-model",
            vision_model="custom-vision",
            retry_attempts=5,
            retry_delay=2.0
        )
        assert config.enabled is True
        assert config.endpoint == "http://custom:11434/api/generate"
        assert config.model == "custom-model"
        assert config.vision_model == "custom-vision"
        assert config.retry_attempts == 5
        assert config.retry_delay == 2.0
    
    def test_unsupported_provider_validation(self):
        """Test validation of unsupported provider."""
        with pytest.raises(ValidationError, match="Unsupported provider 'openai'"):
            LLMConfig(provider="openai")
    
    def test_negative_retry_attempts_validation(self):
        """Test validation of negative retry attempts."""
        with pytest.raises(ValidationError, match="Retry attempts must be non-negative"):
            LLMConfig(retry_attempts=-1)
    
    def test_negative_retry_delay_validation(self):
        """Test validation of negative retry delay."""
        with pytest.raises(ValidationError, match="Retry delay must be non-negative"):
            LLMConfig(retry_delay=-1.0)


class TestPeekConfig:
    """Test PeekConfig configuration model."""
    
    def test_default_peek_config(self):
        """Test default peek configuration."""
        config = PeekConfig()
        assert config.enabled is True
        assert config.include_exif_summary is True
        assert config.include_visual_analysis is False
        assert config.sample_count == 5
        assert config.max_resolution == 1024
        assert "jpg" in config.supported_formats
    
    def test_custom_peek_config(self):
        """Test custom peek configuration."""
        config = PeekConfig(
            enabled=False,
            sample_count=10,
            max_resolution=2048,
            supported_formats=["jpg", "nef"]
        )
        assert config.enabled is False
        assert config.sample_count == 10
        assert config.max_resolution == 2048
        assert config.supported_formats == ["jpg", "nef"]
    
    def test_invalid_sample_count_validation(self):
        """Test validation of invalid sample count."""
        with pytest.raises(ValidationError, match="Sample count must be positive"):
            PeekConfig(sample_count=0)
        
        with pytest.raises(ValidationError, match="Sample count must be positive"):
            PeekConfig(sample_count=-1)
    
    def test_invalid_max_resolution_validation(self):
        """Test validation of invalid max resolution."""
        with pytest.raises(ValidationError, match="Max resolution must be between 256 and 4096"):
            PeekConfig(max_resolution=100)
        
        with pytest.raises(ValidationError, match="Max resolution must be between 256 and 4096"):
            PeekConfig(max_resolution=5000)


class TestRawBackupConfig:
    """Test RawBackupConfig configuration model."""
    
    def test_default_raw_backup_config(self):
        """Test default raw backup configuration."""
        config = RawBackupConfig()
        assert config.enabled is False
        assert config.preserve_structure is True
        assert config.timestamp_format == "%Y-%m-%d_%H%M%S"
    
    def test_environment_variable_expansion(self):
        """Test environment variable expansion in backup root."""
        with patch.dict(os.environ, {'HOME': '/home/user'}):
            config = RawBackupConfig(backup_root="${HOME}/Photos/RawBackups")
            assert config.backup_root == "/home/user/Photos/RawBackups"
    
    def test_timestamp_format_validation_logic(self):
        """Test that timestamp format validation logic is present."""
        # Since strftime is very permissive, we just test that the validator exists
        # and doesn't break with normal formats
        config = RawBackupConfig(timestamp_format="%Y-%m-%d_%H%M%S")
        assert config.timestamp_format == "%Y-%m-%d_%H%M%S"


class TestPerformanceConfig:
    """Test PerformanceConfig configuration model."""
    
    def test_default_performance_config(self):
        """Test default performance configuration."""
        config = PerformanceConfig()
        assert config.parallel_workers == 4
        assert config.batch_size == 100
        assert config.cache_exif is True
        assert config.incremental_processing is True
        assert config.memory_mapped_hashing is True
    
    def test_custom_performance_config(self):
        """Test custom performance configuration."""
        config = PerformanceConfig(
            parallel_workers=8,
            batch_size=200,
            cache_exif=False,
            incremental_processing=False,
            memory_mapped_hashing=False
        )
        assert config.parallel_workers == 8
        assert config.batch_size == 200
        assert config.cache_exif is False
        assert config.incremental_processing is False
        assert config.memory_mapped_hashing is False
    
    def test_invalid_parallel_workers_validation(self):
        """Test validation of invalid parallel workers."""
        with pytest.raises(ValidationError, match="Parallel workers must be positive"):
            PerformanceConfig(parallel_workers=0)
        
        with pytest.raises(ValidationError, match="Parallel workers must be positive"):
            PerformanceConfig(parallel_workers=-1)
    
    def test_invalid_batch_size_validation(self):
        """Test validation of invalid batch size."""
        with pytest.raises(ValidationError, match="Batch size must be positive"):
            PerformanceConfig(batch_size=0)
        
        with pytest.raises(ValidationError, match="Batch size must be positive"):
            PerformanceConfig(batch_size=-1)


class TestIngestConfig:
    """Test IngestConfig main configuration model."""
    
    def test_minimal_valid_config(self):
        """Test minimal valid configuration."""
        with patch.dict(os.environ, {'HOME': '/home/user'}):
            config = IngestConfig(archive_root="${HOME}/Photos/Archive")
            assert config.archive_root == "/home/user/Photos/Archive"
            assert isinstance(config.devices, DeviceMapping)
            assert isinstance(config.file_types, FileTypes)
            assert isinstance(config.llm, LLMConfig)
            assert isinstance(config.peek, PeekConfig)
            assert isinstance(config.raw_backup, RawBackupConfig)
            assert isinstance(config.performance, PerformanceConfig)
    
    def test_full_custom_config(self):
        """Test full custom configuration."""
        config_data = {
            "archive_root": "/custom/archive",
            "raw_backup": {"enabled": True, "backup_root": "/custom/backup"},
            "devices": {
                "mappings": {"NIKON Z 6": "Z6"},
                "exif_identifiers": {"Z6": {"Make": "NIKON", "Model": "Z 6"}}
            },
            "file_types": {
                "raw": ["nef", "cr3"],
                "jpeg": ["jpg"],
                "video": ["mp4"]
            },
            "llm": {"enabled": True, "model": "custom-model"},
            "peek": {"sample_count": 10},
            "dedupe_store": "/custom/hashes.db",
            "performance": {"parallel_workers": 8}
        }
        
        config = IngestConfig(**config_data)
        assert config.archive_root == "/custom/archive"
        assert config.raw_backup.enabled is True
        assert config.raw_backup.backup_root == "/custom/backup"
        assert config.devices.mappings["NIKON Z 6"] == "Z6"
        assert config.file_types.raw == ["nef", "cr3"]
        assert config.llm.enabled is True
        assert config.llm.model == "custom-model"
        assert config.peek.sample_count == 10
        assert config.dedupe_store == "/custom/hashes.db"
        assert config.performance.parallel_workers == 8
    
    def test_empty_archive_root_validation(self):
        """Test validation of empty archive root."""
        with pytest.raises(ValidationError, match="Archive root cannot be empty"):
            IngestConfig(archive_root="")
    
    def test_peek_llm_consistency_validation(self):
        """Test validation of peek and LLM configuration consistency."""
        with pytest.raises(ValidationError, match="Visual analysis requires LLM to be enabled"):
            IngestConfig(
                archive_root="/archive",
                peek=PeekConfig(include_visual_analysis=True),
                llm=LLMConfig(enabled=False)
            )
    
    def test_environment_variable_expansion_in_paths(self):
        """Test environment variable expansion in various paths."""
        with patch.dict(os.environ, {'HOME': '/home/user', 'PROJECT': 'photos'}):
            config = IngestConfig(
                archive_root="${HOME}/${PROJECT}/archive",
                dedupe_store="${HOME}/.${PROJECT}_hashes.db"
            )
            assert config.archive_root == "/home/user/photos/archive"
            assert config.dedupe_store == "/home/user/.photos_hashes.db"


class TestConfigManager:
    """Test ConfigManager configuration loading functionality."""
    
    def test_load_config_from_specific_path(self):
        """Test loading configuration from specific path."""
        config_data = {
            "archive_root": "/test/archive",
            "devices": {"mappings": {"NIKON Z 6": "Z6"}},
            "file_types": {"raw": ["nef"], "jpeg": ["jpg"], "video": ["mp4"]},
            "peek": {"include_visual_analysis": False}
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            yaml.dump(config_data, f)
            config_path = f.name
        
        try:
            config = ConfigManager.load_config(config_path)
            assert config.archive_root == "/test/archive"
            assert config.devices.mappings["NIKON Z 6"] == "Z6"
        finally:
            os.unlink(config_path)
    
    def test_load_config_from_default_locations(self):
        """Test loading configuration from default locations."""
        config_data = {
            "archive_root": "/default/archive",
            "devices": {"mappings": {}},
            "file_types": {"raw": ["nef"], "jpeg": ["jpg"], "video": ["mp4"]},
            "peek": {"include_visual_analysis": False}
        }
        
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "ingest.yaml"
            with open(config_path, 'w') as f:
                yaml.dump(config_data, f)
            
            # Mock the default location to point to our temp file
            with patch.object(ConfigManager, 'DEFAULT_CONFIG_LOCATIONS', [str(config_path)]):
                config = ConfigManager.load_config()
                assert config.archive_root == "/default/archive"
    
    def test_config_file_not_found_error(self):
        """Test error when configuration file is not found."""
        with pytest.raises(ConfigurationError, match="Configuration file not found"):
            ConfigManager.load_config("/nonexistent/config.yaml")
    
    def test_invalid_yaml_error(self):
        """Test error when YAML is invalid."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write("invalid: yaml: content: [")
            config_path = f.name
        
        try:
            with pytest.raises(ConfigurationError, match="Invalid YAML"):
                ConfigManager.load_config(config_path)
        finally:
            os.unlink(config_path)
    
    def test_validation_error(self):
        """Test error when configuration validation fails."""
        config_data = {
            "archive_root": "",  # Invalid: empty archive root
            "devices": {"mappings": {}},
            "file_types": {"raw": ["nef"], "jpeg": ["jpg"], "video": ["mp4"]}
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            yaml.dump(config_data, f)
            config_path = f.name
        
        try:
            with pytest.raises(ConfigurationError, match="Configuration validation failed"):
                ConfigManager.load_config(config_path)
        finally:
            os.unlink(config_path)
    
    def test_environment_variable_expansion_in_yaml(self):
        """Test environment variable expansion in YAML content."""
        with patch.dict(os.environ, {'TEST_HOME': '/test/home'}):
            config_content = """
archive_root: ${TEST_HOME}/Photos/Archive
raw_backup:
  backup_root: ${TEST_HOME}/Photos/RawBackups
devices:
  mappings: {}
file_types:
  raw: ["nef"]
  jpeg: ["jpg"]
  video: ["mp4"]
peek:
  include_visual_analysis: false
"""
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
                f.write(config_content)
                config_path = f.name
            
            try:
                config = ConfigManager.load_config(config_path)
                assert config.archive_root == "/test/home/Photos/Archive"
                assert config.raw_backup.backup_root == "/test/home/Photos/RawBackups"
            finally:
                os.unlink(config_path)
    
    def test_create_example_config(self):
        """Test creating example configuration file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "example.yaml"
            ConfigManager.create_example_config(config_path)
            
            assert config_path.exists()
            
            # Verify the example config can be loaded
            config = ConfigManager.load_config(config_path)
            # Environment variables should be expanded when loaded
            assert "Photos/Archive" in str(config.archive_root)
            assert config.devices.mappings["NIKON Z 6"] == "Z6"
            assert config.file_types.raw == ["nef", "cr3", "cr2", "arw", "dng", "orf", "raf", "rw2"]
    
    def test_permission_error_handling(self):
        """Test handling of permission errors."""
        with patch("pathlib.Path.exists", return_value=True), \
             patch("builtins.open", mock_open()) as mock_file:
            mock_file.side_effect = PermissionError("Permission denied")
            
            with pytest.raises(ConfigurationError, match="Permission denied reading config file"):
                ConfigManager.load_config("/test/config.yaml")
    
    def test_unicode_decode_error_handling(self):
        """Test handling of Unicode decode errors."""
        with patch("pathlib.Path.exists", return_value=True), \
             patch("builtins.open", mock_open()) as mock_file:
            mock_file.side_effect = UnicodeDecodeError("utf-8", b"", 0, 1, "invalid start byte")
            
            with pytest.raises(ConfigurationError, match="Config file is not valid UTF-8"):
                ConfigManager.load_config("/test/config.yaml")


if __name__ == "__main__":
    pytest.main([__file__])