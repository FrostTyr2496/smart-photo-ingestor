"""Device detection engine for camera/device identification."""

import logging
import re
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from .config import DeviceMapping

logger = logging.getLogger(__name__)


@dataclass
class DeviceDetectionResult:
    """Result of device detection."""
    device_code: str
    confidence: float
    matched_fields: List[str]
    raw_camera_model: str


class DeviceDetector:
    """Detects device type from EXIF data and maps to folder codes."""
    
    def __init__(self, device_config: DeviceMapping):
        """Initialize device detector.
        
        Args:
            device_config: Device mapping configuration
        """
        self.config = device_config
        self._detection_rules = self._build_detection_rules()
        logger.info(f"Device detector initialized with {len(self.config.mappings)} mappings")
    
    def _build_detection_rules(self) -> Dict[str, Dict[str, Any]]:
        """Build optimized detection rules from configuration.
        
        Returns:
            Dictionary of detection rules for fast lookup
        """
        rules = {}
        
        # Build rules from EXIF identifiers (highest priority)
        for device_code, identifiers in self.config.exif_identifiers.items():
            rules[device_code] = {
                'type': 'exif_identifiers',
                'identifiers': identifiers,
                'priority': self._get_device_priority(device_code)
            }
        
        # Build rules from direct mappings (lower priority)
        for camera_model, device_code in self.config.mappings.items():
            if device_code not in rules:  # Don't override EXIF identifier rules
                rules[device_code] = {
                    'type': 'direct_mapping',
                    'camera_model': camera_model,
                    'priority': self._get_device_priority(device_code)
                }
        
        return rules
    
    def _get_device_priority(self, device_code: str) -> int:
        """Get priority for device code.
        
        Args:
            device_code: Device code to check
            
        Returns:
            Priority value (lower number = higher priority)
        """
        try:
            return self.config.priority_rules.index(device_code)
        except ValueError:
            return 999  # Low priority for devices not in priority rules
    
    def detect_device(self, exif_data: Dict[str, Any]) -> str:
        """Detect device from EXIF and return folder code.
        
        Args:
            exif_data: EXIF metadata dictionary
            
        Returns:
            Device folder code
        """
        raw_camera_model = exif_data.get('Model', '').strip()
        
        # Try configured device identifiers first (highest confidence)
        # Collect all matching devices and sort by priority
        matching_devices = []
        for device_code, identifiers in self.config.exif_identifiers.items():
            if self._matches_identifiers(exif_data, identifiers):
                priority = self._get_device_priority(device_code)
                matching_devices.append((priority, device_code))
        
        if matching_devices:
            # Sort by priority (lower number = higher priority) and return the best match
            matching_devices.sort(key=lambda x: x[0])
            device_code = matching_devices[0][1]
            logger.debug(f"Device detected via EXIF identifiers: {device_code} for {raw_camera_model}")
            return device_code
        
        # Fall back to direct camera model mapping
        if raw_camera_model in self.config.mappings:
            device_code = self.config.mappings[raw_camera_model]
            logger.debug(f"Device detected via direct mapping: {device_code} for {raw_camera_model}")
            return device_code
        
        # Use sanitized raw camera model if no mapping found
        sanitized = self._sanitize_folder_name(raw_camera_model)
        if sanitized:
            logger.debug(f"Using sanitized camera model as device code: {sanitized}")
            return sanitized
        
        # Final fallback
        logger.debug(f"No device mapping found for {raw_camera_model}, using 'Unknown'")
        return "Unknown"
    
    def detect_device_detailed(self, exif_data: Dict[str, Any]) -> DeviceDetectionResult:
        """Detect device with detailed result information.
        
        Args:
            exif_data: EXIF metadata dictionary
            
        Returns:
            Detailed detection result
        """
        raw_camera_model = exif_data.get('Model', '').strip()
        matched_fields = []
        confidence = 0.0
        
        # Try configured device identifiers first
        for device_code, identifiers in self.config.exif_identifiers.items():
            matches, matched = self._matches_identifiers_detailed(exif_data, identifiers)
            if matches:
                confidence = len(matched) / len(identifiers)  # Confidence based on match ratio
                return DeviceDetectionResult(
                    device_code=device_code,
                    confidence=confidence,
                    matched_fields=matched,
                    raw_camera_model=raw_camera_model
                )
        
        # Fall back to direct camera model mapping
        if raw_camera_model in self.config.mappings:
            device_code = self.config.mappings[raw_camera_model]
            return DeviceDetectionResult(
                device_code=device_code,
                confidence=1.0,  # Exact match
                matched_fields=['Model'],
                raw_camera_model=raw_camera_model
            )
        
        # Use sanitized raw camera model
        sanitized = self._sanitize_folder_name(raw_camera_model)
        device_code = sanitized if sanitized else "Unknown"
        confidence = 0.5 if sanitized else 0.0
        
        return DeviceDetectionResult(
            device_code=device_code,
            confidence=confidence,
            matched_fields=['Model'] if sanitized else [],
            raw_camera_model=raw_camera_model
        )
    
    def _matches_identifiers(self, exif_data: Dict[str, Any], identifiers: Dict[str, str]) -> bool:
        """Check if EXIF data matches device identifiers.
        
        Args:
            exif_data: EXIF metadata dictionary
            identifiers: Device identifier patterns
            
        Returns:
            True if all identifiers match, False otherwise
        """
        for field, expected_value in identifiers.items():
            actual_value = exif_data.get(field, '').strip()
            
            # Support both exact match and pattern matching
            if not self._value_matches(actual_value, expected_value):
                return False
        
        return True
    
    def _matches_identifiers_detailed(self, exif_data: Dict[str, Any], 
                                    identifiers: Dict[str, str]) -> tuple[bool, List[str]]:
        """Check identifier matches with detailed information.
        
        Args:
            exif_data: EXIF metadata dictionary
            identifiers: Device identifier patterns
            
        Returns:
            Tuple of (all_match, list_of_matched_fields)
        """
        matched_fields = []
        
        for field, expected_value in identifiers.items():
            actual_value = exif_data.get(field, '').strip()
            
            if self._value_matches(actual_value, expected_value):
                matched_fields.append(field)
        
        all_match = len(matched_fields) == len(identifiers)
        return all_match, matched_fields
    
    def _value_matches(self, actual: str, expected: str) -> bool:
        """Check if actual value matches expected pattern.
        
        Args:
            actual: Actual value from EXIF
            expected: Expected value or pattern
            
        Returns:
            True if values match, False otherwise
        """
        if not actual or not expected:
            return actual == expected
        
        # Exact match (case-insensitive)
        if actual.lower() == expected.lower():
            return True
        
        # Pattern matching with wildcards
        if '*' in expected or '?' in expected:
            # Convert shell-style wildcards to regex
            pattern = expected.replace('*', '.*').replace('?', '.')
            try:
                return bool(re.match(pattern, actual, re.IGNORECASE))
            except re.error:
                logger.warning(f"Invalid pattern in device identifier: {expected}")
                return False
        
        # Substring match for partial matches
        return expected.lower() in actual.lower()
    
    def _sanitize_folder_name(self, name: str) -> str:
        """Convert camera model to valid folder name.
        
        Args:
            name: Raw camera model name
            
        Returns:
            Sanitized folder name
        """
        if not name:
            return ""
        
        # Remove common prefixes and suffixes
        sanitized = name.strip()
        
        # Remove manufacturer names that are often redundant
        prefixes_to_remove = [
            'NIKON CORPORATION',
            'Canon',
            'SONY',
            'FUJIFILM',
            'OLYMPUS',
            'Panasonic',
            'Leica',
            'DJI'
        ]
        
        for prefix in prefixes_to_remove:
            if sanitized.upper().startswith(prefix.upper()):
                sanitized = sanitized[len(prefix):].strip()
                break
        
        # Replace problematic characters with safe alternatives
        char_replacements = {
            ' ': '_',
            '/': '_',
            '\\': '_',
            ':': '_',
            '*': '_',
            '?': '_',
            '"': '_',
            '<': '_',
            '>': '_',
            '|': '_',
            '.': '_',
            ',': '_',
            ';': '_',
            '=': '_',
            '+': '_',
            '[': '_',
            ']': '_',
            '(': '_',
            ')': '_'
        }
        
        for old_char, new_char in char_replacements.items():
            sanitized = sanitized.replace(old_char, new_char)
        
        # Remove multiple consecutive underscores
        while '__' in sanitized:
            sanitized = sanitized.replace('__', '_')
        
        # Remove leading/trailing underscores
        sanitized = sanitized.strip('_')
        
        # Limit length to reasonable folder name size
        if len(sanitized) > 50:
            sanitized = sanitized[:50].rstrip('_')
        
        return sanitized
    
    def get_all_device_codes(self) -> List[str]:
        """Get all configured device codes.
        
        Returns:
            List of all device codes
        """
        device_codes = set()
        
        # From EXIF identifiers
        device_codes.update(self.config.exif_identifiers.keys())
        
        # From direct mappings
        device_codes.update(self.config.mappings.values())
        
        return sorted(device_codes)
    
    def get_device_info(self, device_code: str) -> Optional[Dict[str, Any]]:
        """Get information about a specific device code.
        
        Args:
            device_code: Device code to look up
            
        Returns:
            Device information dictionary or None if not found
        """
        info = {
            'device_code': device_code,
            'detection_method': None,
            'identifiers': None,
            'camera_models': []
        }
        
        # Check EXIF identifiers
        if device_code in self.config.exif_identifiers:
            info['detection_method'] = 'exif_identifiers'
            info['identifiers'] = self.config.exif_identifiers[device_code]
        
        # Check direct mappings
        camera_models = [model for model, code in self.config.mappings.items() if code == device_code]
        if camera_models:
            if info['detection_method'] is None:
                info['detection_method'] = 'direct_mapping'
            info['camera_models'] = camera_models
        
        return info if info['detection_method'] else None
    
    def validate_configuration(self) -> List[str]:
        """Validate device configuration and return any issues.
        
        Returns:
            List of validation issues (empty if no issues)
        """
        issues = []
        
        # Check for empty device codes
        for device_code in self.config.exif_identifiers.keys():
            if not device_code.strip():
                issues.append("Empty device code in EXIF identifiers")
        
        for device_code in self.config.mappings.values():
            if not device_code.strip():
                issues.append("Empty device code in mappings")
        
        # Check for conflicting device codes
        exif_devices = set(self.config.exif_identifiers.keys())
        mapping_devices = set(self.config.mappings.values())
        
        # This is actually OK - devices can have both EXIF identifiers and direct mappings
        # But we should warn about potential conflicts
        overlapping = exif_devices.intersection(mapping_devices)
        if overlapping:
            logger.info(f"Device codes with both EXIF identifiers and direct mappings: {overlapping}")
        
        # Check priority rules reference valid devices
        all_devices = exif_devices.union(mapping_devices)
        for priority_device in self.config.priority_rules:
            if priority_device not in all_devices:
                issues.append(f"Priority rule references unknown device: {priority_device}")
        
        # Check for invalid characters in device codes
        invalid_chars = set('<>:"/\\|?*')
        for device_code in all_devices:
            if any(char in device_code for char in invalid_chars):
                issues.append(f"Device code contains invalid characters: {device_code}")
        
        return issues