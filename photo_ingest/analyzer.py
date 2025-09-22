"""Photo analysis functionality with comprehensive EXIF extraction."""

import json
import time
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict

try:
    from PIL import Image
    from PIL.ExifTags import TAGS, GPSTAGS
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

from .file_scanner import FileScanner, FileInfo, ScanResult
from .config import FileTypes, PerformanceConfig


@dataclass
class AnalysisResult:
    """Complete analysis result with all metadata."""
    # Basic statistics
    total_files: int
    total_size: int
    image_files_analyzed: int
    scan_time: float
    exif_time: float
    date_range: Optional[Tuple[str, str]]
    
    # File breakdown
    files_by_type: Dict[str, int]
    size_by_type: Dict[str, int]
    files_by_extension: Dict[str, int]
    
    # Equipment
    cameras: Dict[str, int]
    lenses: Dict[str, int]
    lens_makes: Dict[str, int]
    
    # Camera settings
    aperture_range: Optional[Tuple[float, float]]
    iso_range: Optional[Tuple[int, int]]
    focal_length_range: Optional[Tuple[float, float]]
    most_used_apertures: List[Tuple[str, int]]
    most_used_isos: List[Tuple[str, int]]
    most_used_focal_lengths: List[Tuple[str, int]]
    common_shutter_speeds: List[Tuple[str, int]]
    
    # Advanced settings
    exposure_programs: Dict[str, int]
    metering_modes: Dict[str, int]
    flash_usage: Dict[str, int]
    white_balance: Dict[str, int]
    exposure_compensation: List[str]
    
    # Image properties
    resolutions: Dict[str, int]
    color_spaces: Dict[str, int]
    
    # Metadata
    files_with_gps: int
    files_with_artist: int
    files_with_copyright: int
    software_used: Dict[str, int]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


class PhotoAnalyzer:
    """Comprehensive photo analysis with EXIF extraction."""
    
    def __init__(self, 
                 file_types: Optional[FileTypes] = None,
                 performance_config: Optional[PerformanceConfig] = None,
                 detailed: bool = True):
        """
        Initialize photo analyzer.
        
        Args:
            file_types: File type configuration
            performance_config: Performance settings
            detailed: Whether to extract detailed EXIF data
        """
        self.file_types = file_types or FileTypes()
        self.performance_config = performance_config or PerformanceConfig()
        self.detailed = detailed
        
        if not PILLOW_AVAILABLE:
            raise ImportError("Pillow is required for photo analysis. Install with: pip install Pillow")
        
        self.scanner = FileScanner(self.file_types, self.performance_config)
    
    def analyze_directory(self, directory: Path, progress_callback=None) -> AnalysisResult:
        """
        Analyze directory with comprehensive EXIF extraction.
        
        Args:
            directory: Directory to analyze
            progress_callback: Optional progress callback function (phase, current, total, message)
            
        Returns:
            AnalysisResult with comprehensive analysis data
        """
        # Scan for files with progress
        start_time = time.time()
        
        # Create a scanner progress callback
        def scan_progress(current, total, message):
            if progress_callback:
                progress_callback("scan", current, total, message)
        
        scan_result = self.scanner.scan_directory(directory, recursive=True)
        scan_time = time.time() - start_time
        
        if progress_callback:
            progress_callback("scan", scan_result.total_files, scan_result.total_files, "Complete")
        
        if scan_result.total_files == 0:
            return self._create_empty_result(scan_time, 0)
        
        # Extract EXIF data for image files
        image_files = [f for f in scan_result.files if f.file_type in ['raw', 'jpeg']]
        
        if not image_files:
            return self._create_empty_result(scan_time, 0)
        
        start_time = time.time()
        metadata_results = self._extract_metadata_batch(image_files, progress_callback)
        exif_time = time.time() - start_time
        
        # Analyze all the extracted metadata
        analysis_data = self._analyze_metadata(metadata_results, scan_result)
        
        return AnalysisResult(
            total_files=scan_result.total_files,
            total_size=scan_result.total_size,
            image_files_analyzed=len(metadata_results),
            scan_time=scan_time,
            exif_time=exif_time,
            **analysis_data
        )
    
    def _extract_metadata_batch(self, image_files: List[FileInfo], progress_callback=None) -> Dict[Path, Dict[str, Any]]:
        """Extract metadata from batch of image files."""
        metadata_results = {}
        processed = 0
        failed_count = 0
        
        for file_info in image_files:
            if self.detailed:
                metadata = self._extract_detailed_exif(file_info.path)
            else:
                metadata = self._extract_basic_exif(file_info.path)
            
            if metadata:
                metadata_results[file_info.path] = metadata
            else:
                failed_count += 1
                # Debug: Print first few failures
                if failed_count <= 5:
                    print(f"DEBUG: Failed to extract metadata from {file_info.path.name} (type: {file_info.file_type})")
            
            processed += 1
            
            if progress_callback:
                # Show progress every file for better feedback
                progress_callback("exif", processed, len(image_files), file_info.path.name)
        
        # Debug: Print summary
        success_rate = (len(metadata_results) / len(image_files)) * 100 if image_files else 0
        print(f"DEBUG: EXIF extraction summary: {len(metadata_results)}/{len(image_files)} files ({success_rate:.1f}% success rate)")
        
        return metadata_results
    
    def _extract_basic_exif(self, file_path: Path) -> Dict[str, Any]:
        """Extract basic EXIF data using Pillow."""
        try:
            with Image.open(file_path) as img:
                metadata = {}
                
                # Always get image dimensions from PIL (most reliable)
                metadata['ImageWidth'] = img.width
                metadata['ImageHeight'] = img.height
                
                # Try to get EXIF data
                exif_dict = img.getexif()
                if exif_dict:
                    for tag_id, value in exif_dict.items():
                        tag_name = TAGS.get(tag_id, tag_id)
                        
                        if tag_name == 'Make':
                            metadata['Make'] = str(value).strip()
                        elif tag_name == 'Model':
                            metadata['Model'] = str(value).strip()
                        elif tag_name == 'LensModel':
                            metadata['LensModel'] = str(value).strip()
                        elif tag_name in ['DateTime', 'DateTimeOriginal']:
                            try:
                                dt = datetime.strptime(str(value), '%Y:%m:%d %H:%M:%S')
                                metadata['DateTime'] = dt
                            except Exception:
                                pass
                        elif tag_name == 'ISOSpeedRatings':
                            try:
                                metadata['ISO'] = int(value)
                            except Exception:
                                pass
                        elif tag_name == 'FNumber':
                            try:
                                if isinstance(value, tuple) and len(value) == 2:
                                    metadata['FNumber'] = float(value[0]) / float(value[1])
                                else:
                                    metadata['FNumber'] = float(value)
                            except Exception:
                                pass
                        elif tag_name == 'ExposureTime':
                            metadata['ExposureTime'] = str(value)
                        elif tag_name == 'FocalLength':
                            try:
                                if isinstance(value, tuple) and len(value) == 2:
                                    metadata['FocalLength'] = float(value[0]) / float(value[1])
                                else:
                                    metadata['FocalLength'] = float(value)
                            except Exception:
                                pass
                
                return metadata
                
        except Exception as e:
            # Debug: Print what's failing (first few only)
            if not hasattr(self, '_debug_count'):
                self._debug_count = 0
            self._debug_count += 1
            if self._debug_count <= 3:
                print(f"DEBUG: Failed to open {file_path.name}: {e}")
            return {}
    
    def _extract_detailed_exif(self, file_path: Path) -> Dict[str, Any]:
        """Extract comprehensive EXIF data using Pillow."""
        try:
            with Image.open(file_path) as img:
                exif_dict = img.getexif()
                if not exif_dict:
                    return {}
                
                metadata = {}
                
                # Extract GPS data if available
                gps_info = {}
                if 'GPSInfo' in exif_dict:
                    gps_data = exif_dict['GPSInfo']
                    for key, value in gps_data.items():
                        gps_tag = GPSTAGS.get(key, key)
                        gps_info[gps_tag] = value
                    
                    # Parse GPS coordinates
                    if 'GPSLatitude' in gps_info and 'GPSLatitudeRef' in gps_info:
                        lat = self._format_gps_coordinate(gps_info['GPSLatitude'], gps_info['GPSLatitudeRef'])
                        if lat is not None:
                            metadata['GPSLatitude'] = lat
                    
                    if 'GPSLongitude' in gps_info and 'GPSLongitudeRef' in gps_info:
                        lon = self._format_gps_coordinate(gps_info['GPSLongitude'], gps_info['GPSLongitudeRef'])
                        if lon is not None:
                            metadata['GPSLongitude'] = lon
                    
                    if 'GPSAltitude' in gps_info:
                        try:
                            altitude = float(gps_info['GPSAltitude'])
                            metadata['GPSAltitude'] = altitude
                        except (ValueError, TypeError):
                            pass
                
                # Extract all EXIF tags
                for tag_id, value in exif_dict.items():
                    if tag_id == 'GPSInfo':
                        continue
                        
                    tag_name = TAGS.get(tag_id, f"Tag_{tag_id}")
                    
                    # Camera and lens information
                    if tag_name == 'Make':
                        metadata['Make'] = str(value).strip()
                    elif tag_name == 'Model':
                        metadata['Model'] = str(value).strip()
                    elif tag_name == 'LensModel':
                        metadata['LensModel'] = str(value).strip()
                    elif tag_name == 'LensMake':
                        metadata['LensMake'] = str(value).strip()
                    elif tag_name == 'SerialNumber':
                        metadata['CameraSerialNumber'] = str(value).strip()
                    
                    # Date and time
                    elif tag_name in ['DateTime', 'DateTimeOriginal', 'DateTimeDigitized']:
                        try:
                            dt = datetime.strptime(str(value), '%Y:%m:%d %H:%M:%S')
                            metadata[tag_name] = dt
                            if 'DateTime' not in metadata:
                                metadata['DateTime'] = dt
                        except Exception:
                            pass
                    
                    # Camera settings
                    elif tag_name == 'ISOSpeedRatings':
                        try:
                            metadata['ISO'] = int(value)
                        except Exception:
                            pass
                    elif tag_name == 'FNumber':
                        try:
                            if isinstance(value, tuple) and len(value) == 2:
                                metadata['FNumber'] = float(value[0]) / float(value[1])
                            else:
                                metadata['FNumber'] = float(value)
                        except Exception:
                            pass
                    elif tag_name == 'ExposureTime':
                        metadata['ExposureTime'] = str(value)
                    elif tag_name == 'FocalLength':
                        try:
                            if isinstance(value, tuple) and len(value) == 2:
                                metadata['FocalLength'] = float(value[0]) / float(value[1])
                            else:
                                metadata['FocalLength'] = float(value)
                        except Exception:
                            pass
                    elif tag_name == 'FocalLengthIn35mmFilm':
                        try:
                            metadata['FocalLength35mm'] = int(value)
                        except Exception:
                            pass
                    
                    # Exposure settings
                    elif tag_name == 'ExposureProgram':
                        programs = {
                            0: 'Not defined', 1: 'Manual', 2: 'Normal program',
                            3: 'Aperture priority', 4: 'Shutter priority', 5: 'Creative program',
                            6: 'Action program', 7: 'Portrait mode', 8: 'Landscape mode'
                        }
                        metadata['ExposureProgram'] = programs.get(value, f'Unknown ({value})')
                    elif tag_name == 'MeteringMode':
                        metering_modes = {
                            0: 'Unknown', 1: 'Average', 2: 'Center-weighted average',
                            3: 'Spot', 4: 'Multi-spot', 5: 'Pattern', 6: 'Partial'
                        }
                        metadata['MeteringMode'] = metering_modes.get(value, f'Unknown ({value})')
                    elif tag_name == 'Flash':
                        flash_modes = {
                            0: 'No flash', 1: 'Flash fired', 5: 'Flash fired, no return',
                            7: 'Flash fired, return detected', 9: 'Flash fired, compulsory',
                            16: 'No flash, compulsory', 24: 'No flash, auto', 25: 'Flash fired, auto'
                        }
                        metadata['Flash'] = flash_modes.get(value, f'Flash mode {value}')
                    elif tag_name == 'WhiteBalance':
                        wb_modes = {0: 'Auto', 1: 'Manual'}
                        metadata['WhiteBalance'] = wb_modes.get(value, f'Unknown ({value})')
                    
                    # Image properties
                    elif tag_name == 'ImageWidth':
                        try:
                            metadata['ImageWidth'] = int(value)
                        except Exception:
                            pass
                    elif tag_name == 'ImageLength':
                        try:
                            metadata['ImageHeight'] = int(value)
                        except Exception:
                            pass
                    elif tag_name == 'ColorSpace':
                        color_spaces = {1: 'sRGB', 65535: 'Uncalibrated'}
                        metadata['ColorSpace'] = color_spaces.get(value, f'Unknown ({value})')
                    
                    # Software and metadata
                    elif tag_name == 'Software':
                        metadata['Software'] = str(value).strip()
                    elif tag_name == 'Artist':
                        metadata['Artist'] = str(value).strip()
                    elif tag_name == 'Copyright':
                        metadata['Copyright'] = str(value).strip()
                    
                    # Exposure compensation
                    elif tag_name == 'ExposureBiasValue':
                        try:
                            if isinstance(value, tuple) and len(value) == 2:
                                bias = float(value[0]) / float(value[1])
                                metadata['ExposureBias'] = f"{bias:+.1f} EV"
                            else:
                                metadata['ExposureBias'] = f"{float(value):+.1f} EV"
                        except Exception:
                            pass
                
                # Always get image dimensions from PIL (more reliable than EXIF)
                metadata['ImageWidth'] = img.width
                metadata['ImageHeight'] = img.height
                
                return metadata
                
        except Exception:
            return {}
    
    def _format_gps_coordinate(self, coord_tuple, ref):
        """Format GPS coordinates from EXIF tuple format."""
        if not coord_tuple or not ref:
            return None
        
        try:
            degrees = float(coord_tuple[0])
            minutes = float(coord_tuple[1]) if len(coord_tuple) > 1 else 0
            seconds = float(coord_tuple[2]) if len(coord_tuple) > 2 else 0
            
            decimal = degrees + (minutes / 60.0) + (seconds / 3600.0)
            
            if ref in ['S', 'W']:
                decimal = -decimal
                
            return decimal
        except (ValueError, TypeError, IndexError):
            return None
    
    def _analyze_metadata(self, metadata_results: Dict[Path, Dict[str, Any]], scan_result: ScanResult) -> Dict[str, Any]:
        """Analyze extracted metadata and generate statistics."""
        # Initialize counters
        cameras = Counter()
        lenses = Counter()
        lens_makes = Counter()
        software = Counter()
        exposure_programs = Counter()
        metering_modes = Counter()
        flash_usage = Counter()
        white_balance = Counter()
        color_spaces = Counter()
        
        apertures = []
        iso_values = []
        focal_lengths = []
        shutter_speeds = []
        exposure_biases = []
        dates = []
        
        files_with_gps = 0
        files_with_artist = 0
        files_with_copyright = 0
        
        resolutions = Counter()
        
        for file_path, metadata in metadata_results.items():
            # Camera information
            make = metadata.get('Make', '').strip()
            model = metadata.get('Model', '').strip()
            if make and model:
                camera = f"{make} {model}"
                cameras[camera] += 1
            elif model:
                cameras[model] += 1
            
            # Lens information
            lens = metadata.get('LensModel', '').strip()
            if lens:
                lenses[lens] += 1
            
            lens_make = metadata.get('LensMake', '').strip()
            if lens_make:
                lens_makes[lens_make] += 1
            
            # Software
            sw = metadata.get('Software', '').strip()
            if sw:
                software[sw] += 1
            
            # Shooting modes (only in detailed mode)
            if self.detailed:
                if metadata.get('ExposureProgram'):
                    exposure_programs[metadata['ExposureProgram']] += 1
                if metadata.get('MeteringMode'):
                    metering_modes[metadata['MeteringMode']] += 1
                if metadata.get('Flash'):
                    flash_usage[metadata['Flash']] += 1
                if metadata.get('WhiteBalance'):
                    white_balance[metadata['WhiteBalance']] += 1
                if metadata.get('ColorSpace'):
                    color_spaces[metadata['ColorSpace']] += 1
            
            # Technical settings
            if metadata.get('FNumber') is not None:
                apertures.append(metadata['FNumber'])
            if metadata.get('ISO') is not None:
                iso_values.append(metadata['ISO'])
            if metadata.get('FocalLength') is not None:
                focal_lengths.append(metadata['FocalLength'])
            if metadata.get('ExposureTime'):
                shutter_speeds.append(metadata['ExposureTime'])
            if metadata.get('ExposureBias'):
                exposure_biases.append(metadata['ExposureBias'])
            
            # Date information
            if metadata.get('DateTime'):
                dates.append(metadata['DateTime'])
            
            # GPS and metadata
            if metadata.get('GPSLatitude') is not None and metadata.get('GPSLongitude') is not None:
                files_with_gps += 1
            if metadata.get('Artist'):
                files_with_artist += 1
            if metadata.get('Copyright'):
                files_with_copyright += 1
            
            # Image dimensions
            width = metadata.get('ImageWidth')
            height = metadata.get('ImageHeight')
            if width and height:
                resolution = f"{width}x{height}"
                resolutions[resolution] += 1
        
        # Calculate ranges and most used values
        aperture_range = (min(apertures), max(apertures)) if apertures else None
        iso_range = (min(iso_values), max(iso_values)) if iso_values else None
        focal_length_range = (min(focal_lengths), max(focal_lengths)) if focal_lengths else None
        
        date_range = None
        if dates:
            min_date = min(dates)
            max_date = max(dates)
            date_range = (min_date.strftime('%Y-%m-%d'), max_date.strftime('%Y-%m-%d'))
        
        # Most used values
        most_used_apertures = [(f"f/{ap:.1f}", count) for ap, count in Counter(apertures).most_common(5)]
        most_used_isos = [(f"ISO{iso}", count) for iso, count in Counter(iso_values).most_common(5)]
        most_used_focal_lengths = [(f"{int(fl)}mm", count) for fl, count in Counter([int(fl) for fl in focal_lengths]).most_common(5)]
        common_shutter_speeds = [(self._format_shutter_speed(speed), count) for speed, count in Counter(shutter_speeds).most_common(5)]
        
        # File statistics
        files_by_type = {
            'raw': len(scan_result.files_by_type['raw']),
            'jpeg': len(scan_result.files_by_type['jpeg']),
            'video': len(scan_result.files_by_type['video'])
        }
        
        size_by_type = scan_result.size_by_type
        
        files_by_extension = Counter()
        for file_info in scan_result.files:
            files_by_extension[file_info.extension] += 1
        
        return {
            'date_range': date_range,
            'files_by_type': files_by_type,
            'size_by_type': size_by_type,
            'files_by_extension': dict(files_by_extension),
            'cameras': dict(cameras),
            'lenses': dict(lenses),
            'lens_makes': dict(lens_makes),
            'aperture_range': aperture_range,
            'iso_range': iso_range,
            'focal_length_range': focal_length_range,
            'most_used_apertures': most_used_apertures,
            'most_used_isos': most_used_isos,
            'most_used_focal_lengths': most_used_focal_lengths,
            'common_shutter_speeds': common_shutter_speeds,
            'exposure_programs': dict(exposure_programs),
            'metering_modes': dict(metering_modes),
            'flash_usage': dict(flash_usage),
            'white_balance': dict(white_balance),
            'exposure_compensation': exposure_biases,
            'resolutions': dict(resolutions),
            'color_spaces': dict(color_spaces),
            'files_with_gps': files_with_gps,
            'files_with_artist': files_with_artist,
            'files_with_copyright': files_with_copyright,
            'software_used': dict(software)
        }
    
    def _format_shutter_speed(self, exposure_time):
        """Format shutter speed for display."""
        if not exposure_time:
            return "Unknown"
        
        try:
            if '/' in str(exposure_time):
                parts = str(exposure_time).split('/')
                if len(parts) == 2:
                    numerator = float(parts[0])
                    denominator = float(parts[1])
                    if numerator == 1:
                        return f"1/{int(denominator)}"
                    else:
                        return f"{numerator/denominator:.2f}s"
            else:
                exp_float = float(exposure_time)
                if exp_float >= 1:
                    return f"{exp_float:.1f}s"
                else:
                    return f"1/{int(1/exp_float)}"
        except (ValueError, ZeroDivisionError):
            return str(exposure_time)
    
    def _create_empty_result(self, scan_time: float, exif_time: float) -> AnalysisResult:
        """Create empty analysis result."""
        return AnalysisResult(
            total_files=0,
            total_size=0,
            image_files_analyzed=0,
            scan_time=scan_time,
            exif_time=exif_time,
            date_range=None,
            files_by_type={},
            size_by_type={},
            files_by_extension={},
            cameras={},
            lenses={},
            lens_makes={},
            aperture_range=None,
            iso_range=None,
            focal_length_range=None,
            most_used_apertures=[],
            most_used_isos=[],
            most_used_focal_lengths=[],
            common_shutter_speeds=[],
            exposure_programs={},
            metering_modes={},
            flash_usage={},
            white_balance={},
            exposure_compensation=[],
            resolutions={},
            color_spaces={},
            files_with_gps=0,
            files_with_artist=0,
            files_with_copyright=0,
            software_used={}
        )


def format_size(size_bytes: int) -> str:
    """Format file size in human readable format."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"