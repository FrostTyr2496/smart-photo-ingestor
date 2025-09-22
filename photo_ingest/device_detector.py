"""Device detection engine for camera/device identification from EXIF data."""

import re
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from .config import DeviceMapping


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
        """
        Initialize device detector with configuration.
        
        Args:
            device_config: Device mapping configuration
        """
        self.config = device_config
        self._build_detection_rules()
    
    def _build_detection_rules(self) -> None:
        """Build optimized detection rules from configuration."""
        # Create reverse mapping for priority lookup
        self._priority_map = {device: idx for idx, device in enumerate(self.config.priority_rules)}
        
        # Normalize EXIF identifiers for case-insensitive matching
        self._normalized_identifiers = {}
        for device_code, identifiers in self.config.exif_identifiers.items():
            self._normalized_identifiers[device_code] = {
                field.lower(): value.lower() for field, value in identifiers.items()
            }
    
    def detect_device(self, exif_data: Dict[str, Any]) -> str:
        """
        Detect device from EXIF and return folder code.
        
        Args:
            exif_data: EXIF metadata dictionary
            
        Returns:
            str: Device folder code
        """
        result = self.detect_device_detailed(exif_data)
        return result.device_code
    
    def detect_device_detailed(self, exif_data: Dict[str, Any]) -> DeviceDetectionResult:
        """
        Detect device from EXIF with detailed results.
        
        Args:
            exif_data: EXIF metadata dictionary
            
        Returns:
            DeviceDetectionResult: Detailed detection result
        """
        raw_camera_model = self._extract_camera_model(exif_data)
        
        # Try configured device identifiers first (highest confidence)
        identifier_result = self._try_exif_identifiers(exif_data, raw_camera_model)
        if identifier_result:
            return identifier_result
        
        # Fall back to direct camera model mapping (medium confidence)
        mapping_result = self._try_direct_mapping(raw_camera_model)
        if mapping_result:
            return mapping_result
        
        # Use sanitized raw camera model as fallback (low confidence)
        sanitized_model = self._sanitize_folder_name(raw_camera_model)
        return DeviceDetectionResult(
            device_code=sanitized_model or "Unknown",
            confidence=0.3 if sanitized_model else 0.1,
            matched_fields=[],
            raw_camera_model=raw_camera_model
        )
    
    def _extract_camera_model(self, exif_data: Dict[str, Any]) -> str:
        """Extract camera model from EXIF data with fallbacks."""
        # Try common EXIF fields for camera model
        model_fields = ['Model', 'Camera Model Name', 'Camera Model']
        
        for field in model_fields:
            if field in exif_data and exif_data[field]:
                return str(exif_data[field]).strip()
        
        return ""
    
    def _try_exif_identifiers(self, exif_data: Dict[str, Any], raw_camera_model: str) -> Optional[DeviceDetectionResult]:
        """Try to match using configured EXIF identifiers."""
        candidates = []
        
        for device_code, identifiers in self._normalized_identifiers.items():
            matched_fields = []
            total_fields = len(identifiers)
            
            for field, expected_value in identifiers.items():
                # Check both exact field name and case variations
                exif_value = None
                for exif_field, exif_val in exif_data.items():
                    if exif_field.lower() == field:
                        exif_value = str(exif_val).lower().strip()
                        break
                
                if exif_value and expected_value in exif_value:
                    matched_fields.append(field)
            
            if matched_fields:
                confidence = len(matched_fields) / total_fields
                candidates.append((device_code, confidence, matched_fields))
        
        if not candidates:
            return None
        
        # Sort by confidence, then by priority rules
        candidates.sort(key=lambda x: (x[1], -self._get_priority_score(x[0])), reverse=True)
        
        best_device, confidence, matched_fields = candidates[0]
        
        # Only accept if we have reasonable confidence (at least one field matched)
        if confidence > 0:
            return DeviceDetectionResult(
                device_code=best_device,
                confidence=min(0.9, confidence),  # Cap at 0.9 for identifier-based detection
                matched_fields=matched_fields,
                raw_camera_model=raw_camera_model
            )
        
        return None
    
    def _try_direct_mapping(self, raw_camera_model: str) -> Optional[DeviceDetectionResult]:
        """Try direct camera model to folder name mapping."""
        if not raw_camera_model:
            return None
        
        # Try exact match first
        if raw_camera_model in self.config.mappings:
            return DeviceDetectionResult(
                device_code=self.config.mappings[raw_camera_model],
                confidence=0.8,
                matched_fields=['Model'],
                raw_camera_model=raw_camera_model
            )
        
        # Try case-insensitive partial matching
        raw_lower = raw_camera_model.lower()
        for camera_model, device_code in self.config.mappings.items():
            if camera_model.lower() in raw_lower or raw_lower in camera_model.lower():
                return DeviceDetectionResult(
                    device_code=device_code,
                    confidence=0.6,
                    matched_fields=['Model'],
                    raw_camera_model=raw_camera_model
                )
        
        return None
    
    def _get_priority_score(self, device_code: str) -> int:
        """Get priority score for device (higher is better priority)."""
        return self._priority_map.get(device_code, -1)
    
    def _sanitize_folder_name(self, name: str) -> str:
        """
        Convert camera model to valid folder name.
        
        Args:
            name: Raw camera model name
            
        Returns:
            str: Sanitized folder name
        """
        if not name:
            return ""
        
        # Remove common prefixes and suffixes
        sanitized = name.strip()
        
        # Remove manufacturer names that are often redundant
        prefixes_to_remove = [
            'NIKON CORPORATION',
            'NIKON',
            'Canon',
            'CANON',
            'Sony',
            'SONY',
            'Fujifilm',
            'FUJIFILM',
            'Olympus',
            'OLYMPUS',
            'Panasonic',
            'PANASONIC',
            'Leica',
            'LEICA',
            'DJI'
        ]
        
        for prefix in prefixes_to_remove:
            if sanitized.upper().startswith(prefix.upper()):
                sanitized = sanitized[len(prefix):].strip()
                break
        
        # Replace invalid filesystem characters
        sanitized = re.sub(r'[<>:"/\\|?*]', '_', sanitized)
        
        # Replace multiple spaces/underscores with single underscore
        sanitized = re.sub(r'[\s_]+', '_', sanitized)
        
        # Remove leading/trailing underscores
        sanitized = sanitized.strip('_')
        
        # Limit length to reasonable folder name size
        if len(sanitized) > 50:
            sanitized = sanitized[:50].rstrip('_')
        
        return sanitized
    
    def get_supported_devices(self) -> List[str]:
        """Get list of all configured device codes."""
        configured_devices = set(self.config.exif_identifiers.keys())
        mapped_devices = set(self.config.mappings.values())
        return sorted(configured_devices | mapped_devices)
    
    def validate_configuration(self) -> List[str]:
        """
        Validate device configuration and return list of issues.
        
        Returns:
            List[str]: List of validation issues (empty if valid)
        """
        issues = []
        
        # Check for conflicts between identifiers and mappings
        identifier_devices = set(self.config.exif_identifiers.keys())
        mapping_devices = set(self.config.mappings.values())
        
        # Check for priority rules referencing non-existent devices
        all_devices = identifier_devices | mapping_devices
        for priority_device in self.config.priority_rules:
            if priority_device not in all_devices:
                issues.append(f"Priority rule references unknown device: {priority_device}")
        
        # Check for duplicate device codes
        if len(mapping_devices) != len(self.config.mappings):
            # Find duplicates
            seen = set()
            duplicates = set()
            for device_code in self.config.mappings.values():
                if device_code in seen:
                    duplicates.add(device_code)
                seen.add(device_code)
            issues.append(f"Duplicate device codes in mappings: {duplicates}")
        
        # Check for empty or invalid folder names
        for camera_model, device_code in self.config.mappings.items():
            sanitized = self._sanitize_folder_name(device_code)
            if not sanitized:
                issues.append(f"Device code '{device_code}' for camera '{camera_model}' results in empty folder name")
            elif sanitized != device_code:
                issues.append(f"Device code '{device_code}' contains invalid characters, would be sanitized to '{sanitized}'")
        
        return issues