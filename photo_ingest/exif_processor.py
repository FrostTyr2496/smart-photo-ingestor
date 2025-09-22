"""EXIF processing engine with fallback support and caching."""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from exiftool import ExifToolHelper
    EXIFTOOL_AVAILABLE = True
except ImportError:
    EXIFTOOL_AVAILABLE = False

from PIL import Image
from PIL.ExifTags import TAGS

from .config import IngestConfig, PerformanceConfig
from .database import DatabaseManager
from .device_detector import DeviceDetector

logger = logging.getLogger(__name__)


class EXIFProcessingError(Exception):
    """EXIF extraction errors."""
    pass


class EXIFProcessor:
    """Handles EXIF extraction with caching and device detection."""
    
    def __init__(self, config: IngestConfig, db_manager: DatabaseManager):
        """Initialize EXIF processor.
        
        Args:
            config: Application configuration
            db_manager: Database manager for caching
        """
        self.config = config
        self.db_manager = db_manager
        self.performance_config = config.performance
        self.device_detector = DeviceDetector(config.devices)
        
        # Check tool availability
        self.exiftool_available = self._check_exiftool()
        self.pillow_available = True  # Pillow is always available as it's a dependency
        
        logger.info(f"EXIF processor initialized - ExifTool: {self.exiftool_available}, Pillow: {self.pillow_available}")
    
    def _check_exiftool(self) -> bool:
        """Check if exiftool is available on the system."""
        if not EXIFTOOL_AVAILABLE:
            logger.warning("PyExifTool not available - falling back to Pillow")
            return False
        
        try:
            # Check if exiftool binary is available
            result = subprocess.run(['exiftool', '-ver'], 
                                  capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                logger.info(f"ExifTool version: {result.stdout.strip()}")
                return True
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
            pass
        
        logger.warning("ExifTool binary not found - falling back to Pillow")
        return False
    
    def extract_metadata(self, file_path: Path) -> Dict[str, Any]:
        """Extract EXIF data with caching and fallback chain.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Dictionary containing extracted metadata
            
        Raises:
            EXIFProcessingError: If all extraction methods fail
        """
        # Check cache first if enabled
        if self.performance_config.cache_exif:
            cached = self.db_manager.get_cached_exif(file_path)
            if cached:
                logger.debug(f"Using cached EXIF data for {file_path}")
                return cached
        
        # Extract metadata using fallback chain
        metadata = self._extract_with_fallback(file_path)
        
        # Cache the result if enabled
        if self.performance_config.cache_exif and metadata:
            self.db_manager.cache_exif(file_path, metadata)
        
        return metadata
    
    def _extract_with_fallback(self, file_path: Path) -> Dict[str, Any]:
        """Extract metadata using fallback chain.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Dictionary containing extracted metadata
        """
        errors = []
        
        # Try ExifTool first (most comprehensive)
        if self.exiftool_available:
            try:
                metadata = self._extract_with_exiftool(file_path)
                if metadata:
                    logger.debug(f"Extracted EXIF with ExifTool: {file_path}")
                    return metadata
            except Exception as e:
                error_msg = f"ExifTool extraction failed: {e}"
                logger.debug(error_msg)
                errors.append(error_msg)
        
        # Fall back to Pillow for basic EXIF
        if self.pillow_available:
            try:
                metadata = self._extract_with_pillow(file_path)
                if metadata:
                    logger.debug(f"Extracted EXIF with Pillow: {file_path}")
                    return metadata
            except Exception as e:
                error_msg = f"Pillow extraction failed: {e}"
                logger.debug(error_msg)
                errors.append(error_msg)
        
        # Final fallback to file system metadata
        try:
            metadata = self._extract_filesystem_metadata(file_path)
            logger.debug(f"Using filesystem metadata: {file_path}")
            return metadata
        except Exception as e:
            error_msg = f"Filesystem metadata extraction failed: {e}"
            logger.debug(error_msg)
            errors.append(error_msg)
        
        # If all methods fail, log errors and return minimal metadata
        logger.warning(f"All EXIF extraction methods failed for {file_path}: {'; '.join(errors)}")
        return self._create_minimal_metadata(file_path)
    
    def _extract_with_exiftool(self, file_path: Path) -> Dict[str, Any]:
        """Extract metadata using ExifTool.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Dictionary containing extracted metadata
        """
        with ExifToolHelper() as et:
            metadata_list = et.get_metadata([str(file_path)])
            if not metadata_list:
                return {}
            
            raw_metadata = metadata_list[0]
            return self._normalize_exiftool_metadata(raw_metadata)
    
    def _normalize_exiftool_metadata(self, raw_metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize ExifTool metadata to standard format.
        
        Args:
            raw_metadata: Raw metadata from ExifTool
            
        Returns:
            Normalized metadata dictionary
        """
        normalized = {}
        
        # Camera information
        normalized['Make'] = raw_metadata.get('EXIF:Make') or raw_metadata.get('Make', '')
        normalized['Model'] = raw_metadata.get('EXIF:Model') or raw_metadata.get('Model', '')
        
        # Lens information
        normalized['LensModel'] = (
            raw_metadata.get('EXIF:LensModel') or 
            raw_metadata.get('LensModel') or
            raw_metadata.get('EXIF:Lens') or
            ''
        )
        
        # Date/time information
        date_fields = [
            'EXIF:DateTimeOriginal',
            'EXIF:CreateDate', 
            'EXIF:DateTime',
            'DateTimeOriginal',
            'CreateDate',
            'DateTime'
        ]
        
        for field in date_fields:
            if field in raw_metadata:
                try:
                    # ExifTool returns dates in various formats
                    date_str = str(raw_metadata[field])
                    normalized['DateTime'] = self._parse_datetime(date_str)
                    break
                except Exception as e:
                    logger.debug(f"Failed to parse date {raw_metadata[field]}: {e}")
        
        # Technical settings
        normalized['ISO'] = self._safe_int(raw_metadata.get('EXIF:ISO') or raw_metadata.get('ISO'))
        normalized['FNumber'] = self._safe_float(raw_metadata.get('EXIF:FNumber') or raw_metadata.get('FNumber'))
        normalized['ExposureTime'] = raw_metadata.get('EXIF:ExposureTime') or raw_metadata.get('ExposureTime', '')
        normalized['FocalLength'] = self._safe_float(raw_metadata.get('EXIF:FocalLength') or raw_metadata.get('FocalLength'))
        
        # GPS information
        gps_lat = raw_metadata.get('EXIF:GPSLatitude') or raw_metadata.get('GPSLatitude')
        gps_lon = raw_metadata.get('EXIF:GPSLongitude') or raw_metadata.get('GPSLongitude')
        
        if gps_lat is not None and gps_lon is not None:
            normalized['GPSLatitude'] = self._safe_float(gps_lat)
            normalized['GPSLongitude'] = self._safe_float(gps_lon)
        
        # File information
        normalized['FileSize'] = self._safe_int(raw_metadata.get('File:FileSize') or raw_metadata.get('FileSize'))
        normalized['FileType'] = raw_metadata.get('File:FileType') or raw_metadata.get('FileType', '')
        
        # Image dimensions
        normalized['ImageWidth'] = self._safe_int(raw_metadata.get('EXIF:ImageWidth') or raw_metadata.get('ImageWidth'))
        normalized['ImageHeight'] = self._safe_int(raw_metadata.get('EXIF:ImageHeight') or raw_metadata.get('ImageHeight'))
        
        return normalized
    
    def _extract_with_pillow(self, file_path: Path) -> Dict[str, Any]:
        """Extract basic EXIF data using Pillow.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Dictionary containing extracted metadata
        """
        try:
            with Image.open(file_path) as img:
                exif_dict = img.getexif()
                if not exif_dict:
                    return {}
                
                normalized = {}
                
                # Convert numeric tags to names and extract common fields
                for tag_id, value in exif_dict.items():
                    tag_name = TAGS.get(tag_id, tag_id)
                    
                    if tag_name == 'Make':
                        normalized['Make'] = str(value).strip()
                    elif tag_name == 'Model':
                        normalized['Model'] = str(value).strip()
                    elif tag_name == 'LensModel':
                        normalized['LensModel'] = str(value).strip()
                    elif tag_name == 'DateTime':
                        try:
                            normalized['DateTime'] = self._parse_datetime(str(value))
                        except Exception:
                            pass
                    elif tag_name == 'DateTimeOriginal':
                        try:
                            normalized['DateTime'] = self._parse_datetime(str(value))
                        except Exception:
                            pass
                    elif tag_name == 'ISOSpeedRatings':
                        normalized['ISO'] = self._safe_int(value)
                    elif tag_name == 'FNumber':
                        normalized['FNumber'] = self._safe_float(value)
                    elif tag_name == 'ExposureTime':
                        normalized['ExposureTime'] = str(value)
                    elif tag_name == 'FocalLength':
                        normalized['FocalLength'] = self._safe_float(value)
                
                # Add image dimensions
                normalized['ImageWidth'] = img.width
                normalized['ImageHeight'] = img.height
                
                return normalized
                
        except Exception as e:
            logger.debug(f"Pillow EXIF extraction failed for {file_path}: {e}")
            return {}
    
    def _extract_filesystem_metadata(self, file_path: Path) -> Dict[str, Any]:
        """Extract basic metadata from file system.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Dictionary containing filesystem metadata
        """
        stat = file_path.stat()
        
        return {
            'Make': '',
            'Model': '',
            'LensModel': '',
            'DateTime': datetime.fromtimestamp(stat.st_mtime).isoformat(),
            'ISO': None,
            'FNumber': None,
            'ExposureTime': '',
            'FocalLength': None,
            'GPSLatitude': None,
            'GPSLongitude': None,
            'FileSize': stat.st_size,
            'FileType': file_path.suffix.lower().lstrip('.'),
            'ImageWidth': None,
            'ImageHeight': None,
        }
    
    def _create_minimal_metadata(self, file_path: Path) -> Dict[str, Any]:
        """Create minimal metadata when all extraction methods fail.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Dictionary containing minimal metadata
        """
        try:
            stat = file_path.stat()
            file_size = stat.st_size
            mtime = datetime.fromtimestamp(stat.st_mtime).isoformat()
        except Exception:
            file_size = 0
            mtime = datetime.now().isoformat()
        
        return {
            'Make': '',
            'Model': '',
            'LensModel': '',
            'DateTime': mtime,
            'ISO': None,
            'FNumber': None,
            'ExposureTime': '',
            'FocalLength': None,
            'GPSLatitude': None,
            'GPSLongitude': None,
            'FileSize': file_size,
            'FileType': file_path.suffix.lower().lstrip('.'),
            'ImageWidth': None,
            'ImageHeight': None,
        }
    
    def detect_device(self, metadata: Dict[str, Any]) -> str:
        """Detect device and return folder code.
        
        Args:
            metadata: Extracted metadata
            
        Returns:
            Device folder code
        """
        return self.device_detector.detect_device(metadata)
    
    def batch_extract_metadata(self, file_paths: List[Path]) -> Dict[Path, Dict[str, Any]]:
        """Extract metadata for multiple files in parallel.
        
        Args:
            file_paths: List of file paths to process
            
        Returns:
            Dictionary mapping file paths to their metadata
        """
        results = {}
        
        if not file_paths:
            return results
        
        # Use parallel processing if enabled and we have multiple files
        if self.performance_config.parallel_workers > 1 and len(file_paths) > 1:
            with ThreadPoolExecutor(max_workers=self.performance_config.parallel_workers) as executor:
                # Submit all tasks
                future_to_path = {
                    executor.submit(self.extract_metadata, path): path 
                    for path in file_paths
                }
                
                # Collect results
                for future in as_completed(future_to_path):
                    path = future_to_path[future]
                    try:
                        metadata = future.result()
                        results[path] = metadata
                    except Exception as e:
                        logger.error(f"Failed to extract metadata for {path}: {e}")
                        results[path] = self._create_minimal_metadata(path)
        else:
            # Sequential processing
            for path in file_paths:
                try:
                    results[path] = self.extract_metadata(path)
                except Exception as e:
                    logger.error(f"Failed to extract metadata for {path}: {e}")
                    results[path] = self._create_minimal_metadata(path)
        
        return results
    
    def _parse_datetime(self, date_str: str) -> str:
        """Parse datetime string from EXIF data.
        
        Args:
            date_str: Date string from EXIF
            
        Returns:
            ISO format datetime string
        """
        # Common EXIF datetime formats
        formats = [
            '%Y:%m:%d %H:%M:%S',  # Standard EXIF format
            '%Y-%m-%d %H:%M:%S',  # ISO-like format
            '%Y:%m:%d',           # Date only
            '%Y-%m-%d',           # ISO date only
        ]
        
        for fmt in formats:
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                return dt.isoformat()
            except ValueError:
                continue
        
        # If parsing fails, return the original string
        return date_str.strip()
    
    def _safe_int(self, value: Any) -> Optional[int]:
        """Safely convert value to integer.
        
        Args:
            value: Value to convert
            
        Returns:
            Integer value or None if conversion fails
        """
        if value is None:
            return None
        
        try:
            if isinstance(value, (int, float)):
                return int(value)
            elif isinstance(value, str):
                # Handle fractional strings like "100/1"
                if '/' in value:
                    parts = value.split('/')
                    if len(parts) == 2:
                        return int(float(parts[0]) / float(parts[1]))
                return int(float(value))
        except (ValueError, ZeroDivisionError):
            pass
        
        return None
    
    def _safe_float(self, value: Any) -> Optional[float]:
        """Safely convert value to float.
        
        Args:
            value: Value to convert
            
        Returns:
            Float value or None if conversion fails
        """
        if value is None:
            return None
        
        try:
            if isinstance(value, (int, float)):
                return float(value)
            elif isinstance(value, str):
                # Handle fractional strings like "2.8/1" or "28/1"
                if '/' in value:
                    parts = value.split('/')
                    if len(parts) == 2:
                        return float(parts[0]) / float(parts[1])
                return float(value)
        except (ValueError, ZeroDivisionError):
            pass
        
        return None
    
    def get_supported_extensions(self) -> List[str]:
        """Get list of supported file extensions for EXIF extraction.
        
        Returns:
            List of supported file extensions
        """
        return self.config.file_types.get_all_extensions()
    
    def is_supported_file(self, file_path: Path) -> bool:
        """Check if file is supported for EXIF extraction.
        
        Args:
            file_path: Path to check
            
        Returns:
            True if file is supported, False otherwise
        """
        extension = file_path.suffix.lower().lstrip('.')
        return extension in self.get_supported_extensions()