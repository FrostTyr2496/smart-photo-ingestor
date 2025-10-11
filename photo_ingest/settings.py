"""User settings and configuration persistence."""

import json
from pathlib import Path
from typing import Optional, Dict, Any


class SettingsManager:
    """Manages persistent user settings."""
    
    def __init__(self):
        """Initialize settings manager."""
        self.settings_dir = Path.home() / ".photo-ingest"
        self.settings_file = self.settings_dir / "settings.json"
        self._ensure_settings_dir()
    
    def _ensure_settings_dir(self):
        """Ensure settings directory exists."""
        self.settings_dir.mkdir(exist_ok=True)
    
    def load_settings(self) -> Dict[str, Any]:
        """Load user settings from file.
        
        Returns:
            Dictionary of user settings
        """
        if not self.settings_file.exists():
            return {}
        
        try:
            with open(self.settings_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    
    def save_settings(self, settings: Dict[str, Any]):
        """Save user settings to file.
        
        Args:
            settings: Dictionary of settings to save
        """
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(settings, f, indent=2)
        except IOError:
            pass  # Fail silently if we can't save settings
    
    def get_config_path(self) -> Optional[str]:
        """Get the saved configuration file path.
        
        Returns:
            Path to config file or None if not set
        """
        settings = self.load_settings()
        return settings.get('config_path')
    
    def set_config_path(self, config_path: str):
        """Save the configuration file path.
        
        Args:
            config_path: Path to configuration file
        """
        settings = self.load_settings()
        
        # Convert to absolute path for consistency
        abs_path = str(Path(config_path).resolve())
        settings['config_path'] = abs_path
        
        self.save_settings(settings)
    
    def clear_config_path(self):
        """Clear the saved configuration file path."""
        settings = self.load_settings()
        settings.pop('config_path', None)
        self.save_settings(settings)
    
    def get_setting(self, key: str, default: Any = None) -> Any:
        """Get a specific setting value.
        
        Args:
            key: Setting key
            default: Default value if key not found
            
        Returns:
            Setting value or default
        """
        settings = self.load_settings()
        return settings.get(key, default)
    
    def set_setting(self, key: str, value: Any):
        """Set a specific setting value.
        
        Args:
            key: Setting key
            value: Setting value
        """
        settings = self.load_settings()
        settings[key] = value
        self.save_settings(settings)


# Global settings manager instance
settings_manager = SettingsManager()