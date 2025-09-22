"""Tests for device detection engine."""

import pytest
from photo_ingest.device_detector import DeviceDetector, DeviceDetectionResult
from photo_ingest.config import DeviceMapping


@pytest.fixture
def sample_device_config():
    """Create sample device configuration for testing."""
    return DeviceMapping(
        mappings={
            "NIKON Z 6": "Z6",
            "NIKON Z 6_2": "Z6II",
            "Canon EOS R5": "R5",
            "DJI AIR 2S": "Drone",
            "iPhone 14 Pro": "iPhone"
        },
        exif_identifiers={
            "Z6": {
                "Make": "NIKON CORPORATION",
                "Model": "NIKON Z 6"
            },
            "Z6II": {
                "Make": "NIKON CORPORATION", 
                "Model": "NIKON Z 6_2"
            },
            "Drone": {
                "Make": "DJI",
                "Model": "FC3582"
            },
            "iPhone": {
                "Make": "Apple",
                "Model": "iPhone 14 Pro"
            }
        },
        priority_rules=["Z6II", "Z6", "R5", "Drone", "iPhone"]
    )


@pytest.fixture
def device_detector(sample_device_config):
    """Create device detector for testing."""
    return DeviceDetector(sample_device_config)


class TestDeviceDetector:
    """Test cases for DeviceDetector class."""
    
    def test_init(self, device_detector, sample_device_config):
        """Test device detector initialization."""
        assert device_detector.config == sample_device_config
        assert len(device_detector._detection_rules) > 0
    
    def test_build_detection_rules(self, device_detector):
        """Test detection rules building."""
        rules = device_detector._detection_rules
        
        # Should have rules for all configured devices
        assert "Z6" in rules
        assert "Z6II" in rules
        assert "Drone" in rules
        assert "iPhone" in rules
        assert "R5" in rules  # From direct mapping
        
        # Check rule types
        assert rules["Z6"]["type"] == "exif_identifiers"
        assert rules["R5"]["type"] == "direct_mapping"
    
    def test_get_device_priority(self, device_detector):
        """Test device priority calculation."""
        # Devices in priority rules should have low numbers (high priority)
        assert device_detector._get_device_priority("Z6II") == 0
        assert device_detector._get_device_priority("Z6") == 1
        assert device_detector._get_device_priority("R5") == 2
        
        # Devices not in priority rules should have high numbers (low priority)
        assert device_detector._get_device_priority("Unknown") == 999
    
    def test_detect_device_via_exif_identifiers(self, device_detector):
        """Test device detection via EXIF identifiers."""
        exif_data = {
            "Make": "NIKON CORPORATION",
            "Model": "NIKON Z 6"
        }
        
        result = device_detector.detect_device(exif_data)
        assert result == "Z6"
    
    def test_detect_device_via_direct_mapping(self, device_detector):
        """Test device detection via direct camera model mapping."""
        exif_data = {
            "Make": "Canon",
            "Model": "Canon EOS R5"
        }
        
        result = device_detector.detect_device(exif_data)
        assert result == "R5"
    
    def test_detect_device_sanitized_model(self, device_detector):
        """Test device detection with sanitized camera model."""
        exif_data = {
            "Make": "Unknown Manufacturer",
            "Model": "Some Camera Model/2023"
        }
        
        result = device_detector.detect_device(exif_data)
        assert result == "Some_Camera_Model_2023"
    
    def test_detect_device_unknown(self, device_detector):
        """Test device detection fallback to Unknown."""
        exif_data = {
            "Make": "",
            "Model": ""
        }
        
        result = device_detector.detect_device(exif_data)
        assert result == "Unknown"
    
    def test_detect_device_detailed_exif_match(self, device_detector):
        """Test detailed device detection with EXIF match."""
        exif_data = {
            "Make": "DJI",
            "Model": "FC3582"
        }
        
        result = device_detector.detect_device_detailed(exif_data)
        
        assert isinstance(result, DeviceDetectionResult)
        assert result.device_code == "Drone"
        assert result.confidence == 1.0  # Perfect match
        assert result.matched_fields == ["Make", "Model"]
        assert result.raw_camera_model == "FC3582"
    
    def test_detect_device_detailed_partial_match(self, device_detector):
        """Test detailed device detection with partial EXIF match."""
        # Add a device with multiple identifiers for testing
        device_detector.config.exif_identifiers["TestDevice"] = {
            "Make": "Test Manufacturer",
            "Model": "Test Model",
            "SerialNumber": "12345"
        }
        device_detector._detection_rules = device_detector._build_detection_rules()
        
        exif_data = {
            "Make": "Test Manufacturer",
            "Model": "Test Model"
            # Missing SerialNumber
        }
        
        result = device_detector.detect_device_detailed(exif_data)
        
        # Should not match because not all identifiers match
        assert result.device_code != "TestDevice"
    
    def test_detect_device_detailed_direct_mapping(self, device_detector):
        """Test detailed device detection with direct mapping."""
        exif_data = {
            "Make": "Canon",
            "Model": "Canon EOS R5"
        }
        
        result = device_detector.detect_device_detailed(exif_data)
        
        assert result.device_code == "R5"
        assert result.confidence == 1.0
        assert result.matched_fields == ["Model"]
        assert result.raw_camera_model == "Canon EOS R5"
    
    def test_matches_identifiers_exact_match(self, device_detector):
        """Test identifier matching with exact match."""
        exif_data = {
            "Make": "NIKON CORPORATION",
            "Model": "NIKON Z 6"
        }
        
        identifiers = {
            "Make": "NIKON CORPORATION",
            "Model": "NIKON Z 6"
        }
        
        result = device_detector._matches_identifiers(exif_data, identifiers)
        assert result is True
    
    def test_matches_identifiers_case_insensitive(self, device_detector):
        """Test identifier matching is case insensitive."""
        exif_data = {
            "Make": "nikon corporation",
            "Model": "nikon z 6"
        }
        
        identifiers = {
            "Make": "NIKON CORPORATION",
            "Model": "NIKON Z 6"
        }
        
        result = device_detector._matches_identifiers(exif_data, identifiers)
        assert result is True
    
    def test_matches_identifiers_partial_match(self, device_detector):
        """Test identifier matching with missing field."""
        exif_data = {
            "Make": "NIKON CORPORATION"
            # Missing Model
        }
        
        identifiers = {
            "Make": "NIKON CORPORATION",
            "Model": "NIKON Z 6"
        }
        
        result = device_detector._matches_identifiers(exif_data, identifiers)
        assert result is False
    
    def test_matches_identifiers_detailed(self, device_detector):
        """Test detailed identifier matching."""
        exif_data = {
            "Make": "NIKON CORPORATION",
            "Model": "NIKON Z 6",
            "SerialNumber": "12345"
        }
        
        identifiers = {
            "Make": "NIKON CORPORATION",
            "Model": "NIKON Z 6",
            "SerialNumber": "12345",
            "LensModel": "Missing"  # This field is missing in EXIF
        }
        
        all_match, matched_fields = device_detector._matches_identifiers_detailed(exif_data, identifiers)
        
        assert all_match is False
        assert len(matched_fields) == 3
        assert "Make" in matched_fields
        assert "Model" in matched_fields
        assert "SerialNumber" in matched_fields
        assert "LensModel" not in matched_fields
    
    def test_value_matches_exact(self, device_detector):
        """Test exact value matching."""
        assert device_detector._value_matches("Canon", "Canon") is True
        assert device_detector._value_matches("Canon", "canon") is True
        assert device_detector._value_matches("Canon", "Nikon") is False
    
    def test_value_matches_wildcard(self, device_detector):
        """Test wildcard value matching."""
        assert device_detector._value_matches("NIKON Z 6", "NIKON*") is True
        assert device_detector._value_matches("NIKON Z 6", "NIKON Z ?") is True
        assert device_detector._value_matches("Canon EOS R5", "NIKON*") is False
    
    def test_value_matches_substring(self, device_detector):
        """Test substring value matching."""
        assert device_detector._value_matches("NIKON CORPORATION", "NIKON") is True
        assert device_detector._value_matches("Canon EOS R5", "EOS") is True
        assert device_detector._value_matches("Sony A7R", "Canon") is False
    
    def test_value_matches_empty_values(self, device_detector):
        """Test value matching with empty values."""
        assert device_detector._value_matches("", "") is True
        assert device_detector._value_matches("Canon", "") is False
        assert device_detector._value_matches("", "Canon") is False
    
    def test_sanitize_folder_name_basic(self, device_detector):
        """Test basic folder name sanitization."""
        result = device_detector._sanitize_folder_name("Canon EOS R5")
        assert result == "EOS_R5"
    
    def test_sanitize_folder_name_manufacturer_removal(self, device_detector):
        """Test manufacturer name removal."""
        test_cases = [
            ("NIKON CORPORATION Z 6", "Z_6"),
            ("Canon EOS R5", "EOS_R5"),
            ("SONY A7R IV", "A7R_IV"),
            ("DJI AIR 2S", "AIR_2S"),
            ("FUJIFILM X-T4", "X-T4")
        ]
        
        for input_name, expected in test_cases:
            result = device_detector._sanitize_folder_name(input_name)
            assert result == expected
    
    def test_sanitize_folder_name_special_characters(self, device_detector):
        """Test special character replacement."""
        result = device_detector._sanitize_folder_name("Camera/Model:2023*Test?")
        assert result == "Camera_Model_2023_Test"
    
    def test_sanitize_folder_name_multiple_underscores(self, device_detector):
        """Test multiple underscore cleanup."""
        result = device_detector._sanitize_folder_name("Camera   Model///Test")
        assert result == "Camera_Model_Test"
    
    def test_sanitize_folder_name_length_limit(self, device_detector):
        """Test folder name length limiting."""
        long_name = "A" * 60
        result = device_detector._sanitize_folder_name(long_name)
        assert len(result) <= 50
    
    def test_sanitize_folder_name_empty(self, device_detector):
        """Test empty folder name sanitization."""
        result = device_detector._sanitize_folder_name("")
        assert result == ""
        
        result = device_detector._sanitize_folder_name("   ")
        assert result == ""
    
    def test_get_all_device_codes(self, device_detector):
        """Test getting all device codes."""
        device_codes = device_detector.get_all_device_codes()
        
        expected_codes = {"Z6", "Z6II", "R5", "Drone", "iPhone"}
        assert set(device_codes) == expected_codes
        assert device_codes == sorted(device_codes)  # Should be sorted
    
    def test_get_device_info_exif_identifiers(self, device_detector):
        """Test getting device info for EXIF identifier device."""
        info = device_detector.get_device_info("Z6")
        
        assert info is not None
        assert info["device_code"] == "Z6"
        assert info["detection_method"] == "exif_identifiers"
        assert info["identifiers"] == {"Make": "NIKON CORPORATION", "Model": "NIKON Z 6"}
    
    def test_get_device_info_direct_mapping(self, device_detector):
        """Test getting device info for direct mapping device."""
        info = device_detector.get_device_info("R5")
        
        assert info is not None
        assert info["device_code"] == "R5"
        assert info["detection_method"] == "direct_mapping"
        assert "Canon EOS R5" in info["camera_models"]
    
    def test_get_device_info_both_methods(self, device_detector):
        """Test getting device info for device with both methods."""
        # Add a direct mapping for a device that also has EXIF identifiers
        device_detector.config.mappings["NIKON Z 6"] = "Z6"
        
        info = device_detector.get_device_info("Z6")
        
        assert info is not None
        assert info["device_code"] == "Z6"
        assert info["detection_method"] == "exif_identifiers"  # EXIF identifiers take precedence
        assert "NIKON Z 6" in info["camera_models"]
    
    def test_get_device_info_unknown(self, device_detector):
        """Test getting device info for unknown device."""
        info = device_detector.get_device_info("UnknownDevice")
        assert info is None
    
    def test_validate_configuration_valid(self, device_detector):
        """Test configuration validation with valid config."""
        issues = device_detector.validate_configuration()
        assert len(issues) == 0
    
    def test_validate_configuration_empty_device_code(self, device_detector):
        """Test configuration validation with empty device code."""
        device_detector.config.exif_identifiers[""] = {"Make": "Test"}
        
        issues = device_detector.validate_configuration()
        assert len(issues) > 0
        assert any("Empty device code" in issue for issue in issues)
    
    def test_validate_configuration_invalid_priority_rule(self, device_detector):
        """Test configuration validation with invalid priority rule."""
        device_detector.config.priority_rules.append("NonExistentDevice")
        
        issues = device_detector.validate_configuration()
        assert len(issues) > 0
        assert any("Priority rule references unknown device" in issue for issue in issues)
    
    def test_validate_configuration_invalid_characters(self, device_detector):
        """Test configuration validation with invalid characters."""
        device_detector.config.mappings["Test Camera"] = "Test<Device>"
        
        issues = device_detector.validate_configuration()
        assert len(issues) > 0
        assert any("contains invalid characters" in issue for issue in issues)


class TestDeviceDetectorIntegration:
    """Integration tests for device detector."""
    
    def test_real_world_camera_detection(self):
        """Test detection with real-world camera data."""
        config = DeviceMapping(
            mappings={
                "NIKON Z 6": "Z6",
                "Canon EOS R5": "R5"
            },
            exif_identifiers={
                "Z6": {
                    "Make": "NIKON CORPORATION",
                    "Model": "NIKON Z 6"
                }
            }
        )
        
        detector = DeviceDetector(config)
        
        # Test Nikon Z6 detection via EXIF identifiers
        nikon_exif = {
            "Make": "NIKON CORPORATION",
            "Model": "NIKON Z 6",
            "LensModel": "NIKKOR Z 24-70mm f/4 S"
        }
        
        result = detector.detect_device(nikon_exif)
        assert result == "Z6"
        
        # Test Canon R5 detection via direct mapping
        canon_exif = {
            "Make": "Canon",
            "Model": "Canon EOS R5",
            "LensModel": "RF24-70mm F2.8 L IS USM"
        }
        
        result = detector.detect_device(canon_exif)
        assert result == "R5"
        
        # Test unknown camera
        unknown_exif = {
            "Make": "Unknown Manufacturer",
            "Model": "Unknown Camera"
        }
        
        result = detector.detect_device(unknown_exif)
        assert result == "Unknown_Camera"
    
    def test_priority_rules_application(self):
        """Test that priority rules are applied correctly."""
        config = DeviceMapping(
            mappings={
                "Generic Camera": "Generic",
                "Specific Camera": "Specific"
            },
            exif_identifiers={
                "Generic": {
                    "Make": "Test Manufacturer"
                },
                "Specific": {
                    "Make": "Test Manufacturer",
                    "Model": "Specific Camera"
                }
            },
            priority_rules=["Specific", "Generic"]
        )
        
        detector = DeviceDetector(config)
        
        # EXIF data that could match both devices
        exif_data = {
            "Make": "Test Manufacturer",
            "Model": "Specific Camera"
        }
        
        # Should match the more specific device due to exact match
        result = detector.detect_device(exif_data)
        assert result == "Specific"