"""Configuration management with Pydantic validation and environment variable substitution."""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from datetime import datetime

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class DeviceMapping(BaseModel):
    """Device detection and mapping configuration."""
    
    mappings: Dict[str, str] = Field(
        default_factory=dict,
        description="EXIF camera model -> folder name mappings"
    )
    exif_identifiers: Dict[str, Dict[str, str]] = Field(
        default_factory=dict,
        description="Device -> EXIF field mappings for detection"
    )
    priority_rules: List[str] = Field(
        default_factory=list,
        description="Device priority for ambiguous cases"
    )
    
    @field_validator('mappings')
    @classmethod
    def validate_mappings(cls, v):
        """Ensure mappings are non-empty strings."""
        for camera_model, folder_name in v.items():
            if not camera_model.strip():
                raise ValueError("Camera model cannot be empty")
            if not folder_name.strip():
                raise ValueError("Folder name cannot be empty")
        return v
    
    @field_validator('exif_identifiers')
    @classmethod
    def validate_exif_identifiers(cls, v):
        """Ensure EXIF identifiers have required fields."""
        for device_code, identifiers in v.items():
            if not device_code.strip():
                raise ValueError("Device code cannot be empty")
            if not identifiers:
                raise ValueError(f"Device '{device_code}' must have at least one EXIF identifier")
            for field, value in identifiers.items():
                if not field.strip() or not value.strip():
                    raise ValueError(f"EXIF identifier field and value cannot be empty for device '{device_code}'")
        return v


class FileTypes(BaseModel):
    """Supported file type extensions."""
    
    raw: List[str] = Field(
        default=["nef", "cr3", "cr2", "arw", "dng", "orf", "raf", "rw2"],
        description="RAW file extensions"
    )
    jpeg: List[str] = Field(
        default=["jpg", "jpeg", "heic", "heif"],
        description="JPEG file extensions"
    )
    video: List[str] = Field(
        default=["mp4", "mov", "avi", "mkv"],
        description="Video file extensions"
    )
    
    @field_validator('raw', 'jpeg', 'video')
    @classmethod
    def validate_extensions(cls, v):
        """Ensure extensions are lowercase and non-empty."""
        if not v:
            raise ValueError("File type list cannot be empty")
        validated = []
        for ext in v:
            ext = ext.strip().lower()
            if not ext:
                raise ValueError("File extension cannot be empty")
            # Remove leading dot if present
            if ext.startswith('.'):
                ext = ext[1:]
            validated.append(ext)
        return validated
    
    def get_all_extensions(self) -> List[str]:
        """Get all supported file extensions."""
        return self.raw + self.jpeg + self.video


class LLMConfig(BaseModel):
    """LLM integration configuration."""
    
    enabled: bool = Field(default=False, description="Enable LLM integration")
    provider: str = Field(default="ollama", description="LLM provider")
    endpoint: str = Field(
        default="http://localhost:11434/api/generate",
        description="LLM API endpoint"
    )
    model: str = Field(default="llama3.1:8b", description="Text generation model")
    vision_model: str = Field(default="llava:7b", description="Vision-capable model for peek mode")
    prompt_template: Optional[str] = Field(
        default=None,
        description="Custom prompt template"
    )
    retry_attempts: int = Field(default=3, description="Number of retry attempts")
    retry_delay: float = Field(default=1.0, description="Retry delay in seconds")
    
    @field_validator('provider')
    @classmethod
    def validate_provider(cls, v):
        """Validate LLM provider."""
        supported_providers = ["ollama"]
        if v not in supported_providers:
            raise ValueError(f"Unsupported provider '{v}'. Supported: {supported_providers}")
        return v
    
    @field_validator('retry_attempts')
    @classmethod
    def validate_retry_attempts(cls, v):
        """Ensure retry attempts is positive."""
        if v < 0:
            raise ValueError("Retry attempts must be non-negative")
        return v
    
    @field_validator('retry_delay')
    @classmethod
    def validate_retry_delay(cls, v):
        """Ensure retry delay is positive."""
        if v < 0:
            raise ValueError("Retry delay must be non-negative")
        return v


class PeekConfig(BaseModel):
    """Visual content analysis configuration."""
    
    enabled: bool = Field(default=True, description="Enable peek functionality")
    include_exif_summary: bool = Field(default=True, description="Include EXIF-based analysis")
    include_visual_analysis: bool = Field(default=False, description="Include LLM vision analysis")
    sample_count: int = Field(default=5, description="Number of images to sample per device")
    max_resolution: int = Field(default=1024, description="Max dimension for downscaled images")
    supported_formats: List[str] = Field(
        default=["jpg", "jpeg", "heic", "nef", "cr3", "dng"],
        description="Supported formats for visual analysis"
    )
    vision_prompt: str = Field(
        default="""Analyze this photo and describe:
1. Main subject/content
2. Photography style (portrait, landscape, macro, etc.)
3. Setting/location if identifiable
4. Notable visual elements
Keep response concise, 2-3 sentences.""",
        description="Prompt for vision analysis"
    )
    
    @field_validator('sample_count')
    @classmethod
    def validate_sample_count(cls, v):
        """Ensure sample count is positive."""
        if v <= 0:
            raise ValueError("Sample count must be positive")
        return v
    
    @field_validator('max_resolution')
    @classmethod
    def validate_max_resolution(cls, v):
        """Ensure max resolution is reasonable."""
        if v < 256 or v > 4096:
            raise ValueError("Max resolution must be between 256 and 4096")
        return v


class RawBackupConfig(BaseModel):
    """Raw backup configuration."""
    
    enabled: bool = Field(default=False, description="Enable raw backup")
    backup_root: str = Field(
        default="${HOME}/Photos/RawBackups",
        description="Root directory for raw backups"
    )
    preserve_structure: bool = Field(default=True, description="Preserve original directory structure")
    timestamp_format: str = Field(default="%Y-%m-%d_%H%M%S", description="Timestamp format for backup directories")
    
    @field_validator('backup_root')
    @classmethod
    def expand_env_vars(cls, v):
        """Expand environment variables in backup root path."""
        return expand_environment_variables(v)
    
    @field_validator('timestamp_format')
    @classmethod
    def validate_timestamp_format(cls, v):
        """Validate timestamp format string."""
        try:
            # Test the format with current time
            datetime.now().strftime(v)
        except ValueError as e:
            raise ValueError(f"Invalid timestamp format: {e}")
        return v


class PerformanceConfig(BaseModel):
    """Performance optimization settings."""
    
    parallel_workers: int = Field(default=4, description="Number of parallel workers")
    batch_size: int = Field(default=100, description="Batch size for database operations")
    cache_exif: bool = Field(default=True, description="Enable EXIF caching")
    incremental_processing: bool = Field(default=True, description="Enable incremental processing")
    memory_mapped_hashing: bool = Field(default=True, description="Use memory-mapped file hashing")
    
    @field_validator('parallel_workers')
    @classmethod
    def validate_parallel_workers(cls, v):
        """Ensure parallel workers is positive."""
        if v <= 0:
            raise ValueError("Parallel workers must be positive")
        return v
    
    @field_validator('batch_size')
    @classmethod
    def validate_batch_size(cls, v):
        """Ensure batch size is positive."""
        if v <= 0:
            raise ValueError("Batch size must be positive")
        return v


class IngestConfig(BaseModel):
    """Main configuration model."""
    
    archive_root: str = Field(description="Root directory for organized photo archive")
    raw_backup: RawBackupConfig = Field(default_factory=RawBackupConfig)
    devices: DeviceMapping = Field(default_factory=DeviceMapping)
    file_types: FileTypes = Field(default_factory=FileTypes)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    peek: PeekConfig = Field(default_factory=PeekConfig)
    dedupe_store: str = Field(default=".hashes.sqlite", description="Path to deduplication database")
    performance: PerformanceConfig = Field(default_factory=PerformanceConfig)
    
    @field_validator('archive_root')
    @classmethod
    def expand_archive_root(cls, v):
        """Expand environment variables in archive root path."""
        expanded = expand_environment_variables(v)
        if not expanded:
            raise ValueError("Archive root cannot be empty")
        return expanded
    
    @field_validator('dedupe_store')
    @classmethod
    def expand_dedupe_store(cls, v):
        """Expand environment variables in dedupe store path."""
        return expand_environment_variables(v)
    
    @model_validator(mode='after')
    def validate_backup_and_peek_consistency(self):
        """Ensure peek and LLM configurations are consistent."""
        if self.peek.include_visual_analysis and not self.llm.enabled:
            raise ValueError("Visual analysis requires LLM to be enabled")
        return self


def expand_environment_variables(value: str) -> str:
    """Expand environment variables in a string using ${VAR} or $VAR syntax."""
    if not isinstance(value, str):
        return value
    
    # Pattern to match ${VAR} or $VAR
    pattern = re.compile(r'\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)')
    
    def replace_var(match):
        var_name = match.group(1) or match.group(2)
        return os.environ.get(var_name, match.group(0))
    
    return pattern.sub(replace_var, value)


class ConfigurationError(Exception):
    """Configuration validation or loading errors."""
    pass


class ConfigManager:
    """Manages configuration loading with multiple file location support."""
    
    DEFAULT_CONFIG_LOCATIONS = [
        "./ingest.yaml",
        "~/.photo-ingest.yaml"
    ]
    
    @classmethod
    def load_config(cls, config_path: Optional[Union[str, Path]] = None) -> IngestConfig:
        """
        Load configuration from file with multiple location support.
        
        Args:
            config_path: Specific config file path, or None to use default locations
            
        Returns:
            IngestConfig: Validated configuration object
            
        Raises:
            ConfigurationError: If config file is missing, invalid, or fails validation
        """
        config_file = cls._resolve_config_path(config_path)
        
        if not config_file.exists():
            raise ConfigurationError(
                f"Configuration file not found: {config_file}\n"
                f"Searched locations: {cls._get_search_locations(config_path)}"
            )
        
        try:
            config_data = cls._load_yaml_file(config_file)
            return IngestConfig(**config_data)
        except yaml.YAMLError as e:
            raise ConfigurationError(f"Invalid YAML in config file {config_file}: {e}")
        except Exception as e:
            raise ConfigurationError(f"Configuration validation failed: {e}")
    
    @classmethod
    def _resolve_config_path(cls, config_path: Optional[Union[str, Path]]) -> Path:
        """Resolve configuration file path."""
        if config_path:
            return Path(config_path).expanduser().resolve()
        
        # Try default locations
        for location in cls.DEFAULT_CONFIG_LOCATIONS:
            path = Path(location).expanduser().resolve()
            if path.exists():
                return path
        
        # Return first default location for error reporting
        return Path(cls.DEFAULT_CONFIG_LOCATIONS[0]).expanduser().resolve()
    
    @classmethod
    def _get_search_locations(cls, config_path: Optional[Union[str, Path]]) -> List[str]:
        """Get list of searched locations for error reporting."""
        if config_path:
            return [str(Path(config_path).expanduser().resolve())]
        return [str(Path(loc).expanduser().resolve()) for loc in cls.DEFAULT_CONFIG_LOCATIONS]
    
    @classmethod
    def _load_yaml_file(cls, config_file: Path) -> Dict[str, Any]:
        """Load and parse YAML configuration file."""
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Expand environment variables in the raw content before parsing
            expanded_content = expand_environment_variables(content)
            
            return yaml.safe_load(expanded_content) or {}
        except FileNotFoundError:
            raise ConfigurationError(f"Configuration file not found: {config_file}")
        except PermissionError:
            raise ConfigurationError(f"Permission denied reading config file: {config_file}")
        except UnicodeDecodeError:
            raise ConfigurationError(f"Config file is not valid UTF-8: {config_file}")
    
    @classmethod
    def create_example_config(cls, output_path: Union[str, Path]) -> None:
        """Create an example configuration file."""
        example_config = {
            "archive_root": "${HOME}/Photos/Archive",
            "raw_backup": {
                "enabled": False,
                "backup_root": "${HOME}/Photos/RawBackups",
                "preserve_structure": True,
                "timestamp_format": "%Y-%m-%d_%H%M%S"
            },
            "devices": {
                "mappings": {
                    "NIKON Z 6": "Z6",
                    "NIKON Z 6_2": "Z6II",
                    "DJI AIR 2S": "Drone"
                },
                "exif_identifiers": {
                    "Z6": {
                        "Make": "NIKON CORPORATION",
                        "Model": "NIKON Z 6"
                    },
                    "Drone": {
                        "Make": "DJI",
                        "Model": "FC3582"
                    }
                },
                "priority_rules": ["Z6II", "Z6", "Drone"]
            },
            "file_types": {
                "raw": ["nef", "cr3", "cr2", "arw", "dng", "orf", "raf", "rw2"],
                "jpeg": ["jpg", "jpeg", "heic", "heif"],
                "video": ["mp4", "mov", "avi", "mkv"]
            },
            "llm": {
                "enabled": False,
                "provider": "ollama",
                "endpoint": "http://localhost:11434/api/generate",
                "model": "llama3.1:8b",
                "vision_model": "llava:7b",
                "retry_attempts": 3,
                "retry_delay": 1.0
            },
            "peek": {
                "enabled": True,
                "include_exif_summary": True,
                "include_visual_analysis": False,
                "sample_count": 5,
                "max_resolution": 1024,
                "supported_formats": ["jpg", "jpeg", "heic", "nef", "cr3", "dng"]
            },
            "dedupe_store": ".hashes.sqlite",
            "performance": {
                "parallel_workers": 4,
                "batch_size": 100,
                "cache_exif": True,
                "incremental_processing": True,
                "memory_mapped_hashing": True
            }
        }
        
        output_file = Path(output_path)
        with open(output_file, 'w', encoding='utf-8') as f:
            yaml.dump(example_config, f, default_flow_style=False, indent=2, sort_keys=False)