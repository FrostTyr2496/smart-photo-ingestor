"""Photo analysis functionality with comprehensive EXIF extraction."""

import logging
import queue
import shutil
import threading
import time
from functools import lru_cache
from pathlib import Path
from collections import Counter
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

logger = logging.getLogger(__name__)

# Pillow's getexif() only exposes IFD0. Exposure, lens, and GPS data live in sub-IFDs.
EXIF_IFD_POINTER = 0x8769
GPS_IFD_POINTER = 0x8825

try:
    from exiftool import ExifToolHelper
    EXIFTOOL_AVAILABLE = True
except ImportError:
    EXIFTOOL_AVAILABLE = False


def _collect_exif_tags(exif) -> Dict[Any, Any]:
    """Merge IFD0 with the Exif sub-IFD so ISO, FNumber, LensModel, etc. are visible."""
    tags = dict(exif)
    tags.update(exif.get_ifd(EXIF_IFD_POINTER))
    return tags


# =============================================================================
# Metadata reading strategy (research + benchmarks, 2026-10-28; Nikon Z 6 NEF/JPG)
# =============================================================================
# ExifTool is the primary reader. Pillow is only a fallback (ExifTool missing/failed).
#   - Pillow cannot read Nikon MakerNotes: no CameraSerialNumber, ShutterCount, or clean
#     lens name. It also opens a NEF as its 120x160 embedded thumbnail, so ImageWidth/
#     ImageHeight are wrong for RAW. ExifTool returns all of these correctly.
#   - rawpy is NOT needed: it decodes pixels, not metadata, and embedded JPEG previews are
#     available from ExifTool (`-b -JpgFromRaw`, full size 6048x4024 on Z 6; see
#     visual_analyzer.extract_raw_preview).
# Throughput (258 real files on an external SSD, selected tags, wall clock):
#   one process, all tags 61 files/s | one process, selected tags 88/s |
#   4 processes 286/s | 8 processes 455/s | 12 processes no further gain (10 CPUs).
#   Chunk size (25 vs 100 vs all) made no difference; process startup is the fixed cost,
#   so keep exiftool processes alive for the whole run (never one process per file).
# DO NOT use `-fast2`: it is ~2x faster but skips MakerNotes, silently dropping
#   ShutterCount (verified on NEF). `-fast` is safe but gave no speedup here.
# Memory stays flat because files are processed in chunks and results are cached to
#   SQLite per chunk; only small normalized dicts are held per file.
# Read-only: exiftool is only ever asked to read (no -overwrite/-delete), so this is safe
#   on /Volumes/* sources.
# ExifTool key names (PyExifTool default common_args is `-G -n`): group0 prefixes
#   (`EXIF:`, `MakerNotes:`, `Composite:`, `File:`) and numeric values (-n), so enum tags
#   such as Flash/MeteringMode arrive as ints and are mapped via the tables below.
# =============================================================================

# Explicit tag list (bare names; ExifTool returns every group's copy, e.g. EXIF:ISO and
# MakerNotes:ISO). Asking for specific tags is ~30% faster than reading everything.
EXIFTOOL_TAGS = [
    "Make", "Model", "LensMake", "LensModel", "LensID", "SerialNumber", "ShutterCount",
    "DateTimeOriginal", "CreateDate", "ISO", "FNumber", "ExposureTime", "FocalLength",
    "FocalLengthIn35mmFormat", "ExposureProgram", "MeteringMode", "Flash", "WhiteBalance",
    "ColorSpace", "ExposureCompensation", "Software", "Artist", "Copyright",
    "GPSLatitude", "GPSLongitude", "GPSAltitude", "ImageWidth", "ImageHeight",
]
EXIFTOOL_CHUNK_SIZE = 200      # files per exiftool call; size does not affect speed, it bounds memory/progress
EXIFTOOL_MAX_WORKERS = 8       # benchmark showed no gain past 8 (disk/CPU bound)
# Bump when the normalized schema changes so stale SQLite cache rows are ignored.
EXIF_CACHE_SCHEMA = "analyzer-v1"
_DATETIME_KEYS = ("DateTime", "DateTimeOriginal", "DateTimeDigitized")

# EXIF enum code -> label (shared by the Pillow and ExifTool paths).
EXPOSURE_PROGRAMS = {
    0: 'Not defined', 1: 'Manual', 2: 'Normal program',
    3: 'Aperture priority', 4: 'Shutter priority', 5: 'Creative program',
    6: 'Action program', 7: 'Portrait mode', 8: 'Landscape mode'
}
METERING_MODES = {
    0: 'Unknown', 1: 'Average', 2: 'Center-weighted average',
    3: 'Spot', 4: 'Multi-spot', 5: 'Pattern', 6: 'Partial'
}
FLASH_MODES = {
    0: 'No flash', 1: 'Flash fired', 5: 'Flash fired, no return',
    7: 'Flash fired, return detected', 9: 'Flash fired, compulsory',
    16: 'No flash, compulsory', 24: 'No flash, auto', 25: 'Flash fired, auto'
}
WHITE_BALANCE_MODES = {0: 'Auto', 1: 'Manual'}
COLOR_SPACES = {1: 'sRGB', 65535: 'Uncalibrated'}


@lru_cache(maxsize=1)
def _find_exiftool() -> Optional[str]:
    """Locate the exiftool binary. Checks PATH, then Homebrew locations, because
    launchd/Hazel jobs run with a minimal PATH that omits /opt/homebrew/bin."""
    found = shutil.which("exiftool")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/exiftool", "/usr/local/bin/exiftool"):
        if Path(candidate).exists():
            return candidate
    return None


def _clean_text(value: Any) -> str:
    """Strip whitespace and NUL padding (Nikon pads some strings with \\x00)."""
    return str(value).strip('\x00 \t\r\n')


def _parse_exif_datetime(value: Any) -> Optional[datetime]:
    """Parse 'YYYY:MM:DD HH:MM:SS' (ExifTool may append subseconds / timezone)."""
    try:
        return datetime.strptime(str(value)[:19], '%Y:%m:%d %H:%M:%S')
    except ValueError:
        return None


def normalize_exiftool_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map one ExifTool `-G -n` result row to the analyzer's metadata schema.

    Returns {} when the row carries no usable data (e.g. exiftool reported an error),
    so callers can fall back to Pillow. Empty strings are omitted, so presence checks
    like files_with_artist count only files that really have a value.
    """
    def first(*keys):
        for key in keys:
            value = raw.get(key)
            if value not in (None, ''):
                return value
        return None

    def as_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    metadata: Dict[str, Any] = {}

    for out_key, keys in (
        ('Make', ('EXIF:Make',)),
        ('Model', ('EXIF:Model',)),
        ('LensMake', ('EXIF:LensMake',)),
        ('LensModel', ('EXIF:LensModel', 'Composite:LensID')),
        # Nikon: EXIF:SerialNumber (also MakerNotes:SerialNumber). Needed for dedupe tier 2.
        ('CameraSerialNumber', ('EXIF:SerialNumber', 'MakerNotes:SerialNumber')),
        ('Software', ('EXIF:Software',)),
        ('Artist', ('EXIF:Artist',)),
        ('Copyright', ('EXIF:Copyright',)),
    ):
        value = first(*keys)
        if value is not None:
            text = _clean_text(value)
            if text:
                metadata[out_key] = text

    shutter_count = as_float(first('MakerNotes:ShutterCount'))
    if shutter_count is not None:
        metadata['ShutterCount'] = int(shutter_count)

    # Capture time is DateTimeOriginal; CreateDate is the fallback.
    original = _parse_exif_datetime(first('EXIF:DateTimeOriginal') or '')
    created = _parse_exif_datetime(first('EXIF:CreateDate') or '')
    if original:
        metadata['DateTimeOriginal'] = original
    if original or created:
        metadata['DateTime'] = original or created

    iso = as_float(first('EXIF:ISO', 'MakerNotes:ISO'))
    if iso is not None:
        metadata['ISO'] = int(iso)
    f_number = as_float(first('EXIF:FNumber'))
    if f_number is not None:
        metadata['FNumber'] = f_number
    focal = as_float(first('EXIF:FocalLength'))
    if focal is not None:
        metadata['FocalLength'] = focal
    focal35 = as_float(first('EXIF:FocalLengthIn35mmFormat'))
    if focal35 is not None:
        metadata['FocalLength35mm'] = int(focal35)
    # Decimal seconds (0.004), not "1/250": decimals sort/average directly (EXIF stores a
    # rational; fractions are display-only via _format_shutter_speed).
    exposure = as_float(first('EXIF:ExposureTime'))
    if exposure:
        metadata['ExposureTime'] = exposure

    # With -n these enums arrive as ints.
    for out_key, key, table in (
        ('ExposureProgram', 'EXIF:ExposureProgram', EXPOSURE_PROGRAMS),
        ('MeteringMode', 'EXIF:MeteringMode', METERING_MODES),
        ('WhiteBalance', 'EXIF:WhiteBalance', WHITE_BALANCE_MODES),
    ):
        value = raw.get(key)
        if isinstance(value, (int, float)):
            metadata[out_key] = table.get(int(value), f'Unknown ({int(value)})')
    flash = raw.get('EXIF:Flash')
    if isinstance(flash, (int, float)):
        metadata['Flash'] = FLASH_MODES.get(int(flash), f'Flash mode {int(flash)}')
    # On NEFs ColorSpace exists only in MakerNotes; on JPEGs it is in EXIF.
    color_space = as_float(first('EXIF:ColorSpace', 'MakerNotes:ColorSpace'))
    if color_space is not None:
        metadata['ColorSpace'] = COLOR_SPACES.get(int(color_space), f'Unknown ({int(color_space)})')
    bias = as_float(first('EXIF:ExposureCompensation'))
    if bias is not None:
        metadata['ExposureBias'] = f"{bias:+.1f} EV"

    lat = as_float(first('Composite:GPSLatitude', 'EXIF:GPSLatitude'))
    lon = as_float(first('Composite:GPSLongitude', 'EXIF:GPSLongitude'))
    if lat is not None and lon is not None:
        metadata['GPSLatitude'] = lat
        metadata['GPSLongitude'] = lon
        altitude = as_float(first('Composite:GPSAltitude', 'EXIF:GPSAltitude'))
        if altitude is not None:
            metadata['GPSAltitude'] = altitude

    # NOTE: for a NEF, EXIF:ImageWidth/Height is the raw sensor area (Z 6: 6064x4040) while
    # the paired JPEG is the output size (6048x4024). Do not compare them for equality.
    width = as_float(first('EXIF:ImageWidth', 'File:ImageWidth'))
    height = as_float(first('EXIF:ImageHeight', 'File:ImageHeight'))
    if width is not None:
        metadata['ImageWidth'] = int(width)
    if height is not None:
        metadata['ImageHeight'] = int(height)

    return metadata


def _meta_to_cache(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-safe copy for the SQLite cache (datetimes -> ISO strings, schema tag added)."""
    data = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in metadata.items()}
    data['_schema'] = EXIF_CACHE_SCHEMA
    return data


def _meta_from_cache(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Inverse of _meta_to_cache. Returns None for rows written by another schema.

    The exif_cache table is shared with EXIFProcessor, which stores a different shape;
    the `_schema` tag keeps the two from reading each other's rows (a collision only
    costs a cache miss).
    """
    if not data or data.get('_schema') != EXIF_CACHE_SCHEMA:
        return None
    meta = {k: v for k, v in data.items() if k != '_schema'}
    for key in _DATETIME_KEYS:
        if isinstance(meta.get(key), str):
            try:
                meta[key] = datetime.fromisoformat(meta[key])
            except ValueError:
                del meta[key]
    return meta


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
                 detailed: bool = True,
                 db_manager=None):
        """
        Initialize photo analyzer.
        
        Args:
            file_types: File type configuration
            performance_config: Performance settings
            detailed: Whether to extract detailed EXIF data
            db_manager: Optional DatabaseManager; when given (and performance_config.cache_exif
                is on) batch results are cached in its exif_cache table, keyed by path + mtime
        """
        self.file_types = file_types or FileTypes()
        self.performance_config = performance_config or PerformanceConfig()
        self.detailed = detailed
        self.db_manager = db_manager
        # Results of extract_metadata_batch(), served by the per-file extractors so callers that
        # loop file by file (analyze_command) reuse the batch instead of re-reading each file.
        self._prefetched: Dict[Path, Dict[str, Any]] = {}
        
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
        """Extract metadata from batch of image files (files with no metadata are omitted)."""
        results = self.extract_metadata_batch([f.path for f in image_files], progress_callback)
        metadata_results = {path: meta for path, meta in results.items() if meta}

        failed = [f for f in image_files if f.path not in metadata_results]
        for file_info in failed[:5]:
            logger.debug(f"Failed to extract metadata from {file_info.path.name} (type: {file_info.file_type})")
        success_rate = (len(metadata_results) / len(image_files)) * 100 if image_files else 0
        logger.debug(f"EXIF extraction summary: {len(metadata_results)}/{len(image_files)} files ({success_rate:.1f}% success rate)")
        return metadata_results

    def extract_metadata_batch(self, paths: List[Path], progress_callback=None) -> Dict[Path, Dict[str, Any]]:
        """Read metadata for many files. Primary entry point for large runs.

        Order of preference per file: SQLite cache -> ExifTool (chunked, several long-lived
        processes, see the strategy notes at the top of this module) -> Pillow per file.
        Every input path is present in the result; files nothing could read map to {}.
        Results are also kept in self._prefetched for the per-file extractors.
        """
        results: Dict[Path, Dict[str, Any]] = {}
        total = len(paths)

        todo: List[Path] = []
        for path in paths:
            cached = self._cache_get(path)
            if cached is not None:
                results[path] = cached
            else:
                todo.append(path)
        if progress_callback and results:
            progress_callback("exif", len(results), total, "cache")

        if todo and self._exiftool_usable():
            for chunk_results in self._exiftool_read_chunks(todo):
                self._cache_put_many(chunk_results)
                results.update(chunk_results)
                if progress_callback:
                    progress_callback("exif", len(results), total, "exiftool")

        # Anything ExifTool could not read (missing binary, error row, unsupported file).
        for path in todo:
            if results.get(path):
                continue
            try:
                meta = self._extract_detailed_exif(path) if self.detailed else self._extract_basic_exif(path)
            except Exception as exc:
                logger.debug(f"Metadata fallback failed for {path.name}: {exc}")
                meta = {}
            results[path] = meta or {}
            if progress_callback:
                progress_callback("exif", len(results), total, path.name)

        self._prefetched.update({p: m for p, m in results.items() if m})
        return results

    def prefetch_metadata(self, paths: List[Path]) -> None:
        """Batch-read paths so later per-file _extract_*_exif calls are served from memory."""
        self.extract_metadata_batch(paths)

    @staticmethod
    def _exiftool_usable() -> bool:
        return EXIFTOOL_AVAILABLE and _find_exiftool() is not None

    def _exiftool_read_chunks(self, paths: List[Path]):
        """Yield {path: normalized metadata} per chunk, read by parallel exiftool processes.

        Each worker thread owns ONE exiftool process for the whole run (process startup is the
        dominant cost) and pulls chunks from a shared queue. Workers use check_execute=False so
        one unreadable file does not discard the other files in its chunk. Paths a worker could
        not read are simply absent from the yielded dict; the caller falls back to Pillow.
        """
        wanted_workers = max(1, min(self.performance_config.parallel_workers, EXIFTOOL_MAX_WORKERS))
        # Shrink chunks for small runs so every worker gets one (258 files / chunk 200 would
        # otherwise use only 2 of 4 workers). Floor of 10 keeps per-call overhead negligible.
        chunk_size = max(10, min(EXIFTOOL_CHUNK_SIZE, -(-len(paths) // wanted_workers)))
        chunks = [paths[i:i + chunk_size] for i in range(0, len(paths), chunk_size)]
        workers = min(wanted_workers, len(chunks))
        work: "queue.Queue[List[Path]]" = queue.Queue()
        for chunk in chunks:
            work.put(chunk)
        out: "queue.Queue" = queue.Queue()
        executable = _find_exiftool()

        def worker():
            try:
                with ExifToolHelper(executable=executable, check_execute=False) as et:
                    while True:
                        try:
                            chunk = work.get_nowait()
                        except queue.Empty:
                            return
                        try:
                            rows = et.get_tags([str(p) for p in chunk], EXIFTOOL_TAGS)
                        except Exception as exc:
                            logger.debug(f"exiftool chunk of {len(chunk)} files failed: {exc}")
                            rows = []
                        by_source = {row.get('SourceFile'): row for row in rows}
                        parsed = {}
                        for path in chunk:
                            row = by_source.get(str(path))
                            meta = normalize_exiftool_row(row) if row else {}
                            if meta:
                                parsed[path] = meta
                        out.put(parsed)
            except Exception as exc:  # exiftool failed to start; leftover chunks go to Pillow
                logger.debug(f"exiftool worker stopped: {exc}")
            finally:
                out.put(None)  # sentinel: this worker is done

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
        for thread in threads:
            thread.start()
        finished = 0
        while finished < workers:
            item = out.get()
            if item is None:
                finished += 1
            else:
                yield item
        for thread in threads:
            thread.join()

    def _cache_get(self, path: Path) -> Optional[Dict[str, Any]]:
        if not (self.db_manager and self.performance_config.cache_exif):
            return None
        try:
            return _meta_from_cache(self.db_manager.get_cached_exif(path))
        except Exception as exc:
            logger.debug(f"EXIF cache read failed for {path.name}: {exc}")
            return None

    def _cache_put_many(self, chunk_results: Dict[Path, Dict[str, Any]]) -> None:
        """Persist one chunk in a single transaction so a crash keeps completed chunks."""
        if not (self.db_manager and self.performance_config.cache_exif and chunk_results):
            return
        try:
            self.db_manager.cache_exif_many({p: _meta_to_cache(m) for p, m in chunk_results.items()})
        except Exception as exc:
            logger.warning(f"EXIF cache write failed ({len(chunk_results)} files): {exc}")
    
    def _extract_basic_exif(self, file_path: Path) -> Dict[str, Any]:
        """Extract basic EXIF data using Pillow."""
        # Served from extract_metadata_batch()/prefetch_metadata() when available (ExifTool-read).
        prefetched = self._prefetched.get(file_path)
        if prefetched is not None:
            return prefetched
        try:
            with Image.open(file_path) as img:
                metadata = {}
                
                # Always get image dimensions from PIL (most reliable)
                metadata['ImageWidth'] = img.width
                metadata['ImageHeight'] = img.height
                
                # Try to get EXIF data
                exif_dict = img.getexif()
                if exif_dict:
                    for tag_id, value in _collect_exif_tags(exif_dict).items():
                        tag_name = TAGS.get(tag_id, tag_id)
                        
                        if tag_name == 'Make':
                            metadata['Make'] = str(value).strip('\x00 \t\r\n')
                        elif tag_name == 'Model':
                            metadata['Model'] = str(value).strip('\x00 \t\r\n')
                        elif tag_name == 'LensModel':
                            metadata['LensModel'] = str(value).strip('\x00 \t\r\n')
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
                            try:
                                metadata['ExposureTime'] = float(value)
                            except (TypeError, ValueError):
                                pass
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
                logger.debug(f"Failed to open {file_path.name}: {e}")
            return self._extract_exiftool_exif(file_path)
    
    def _extract_detailed_exif(self, file_path: Path) -> Dict[str, Any]:
        """Extract comprehensive EXIF data using Pillow."""
        # Served from extract_metadata_batch()/prefetch_metadata() when available (ExifTool-read).
        prefetched = self._prefetched.get(file_path)
        if prefetched is not None:
            return prefetched
        try:
            with Image.open(file_path) as img:
                exif_dict = img.getexif()
                if not exif_dict:
                    return self._extract_exiftool_exif(file_path)
                
                metadata = {}
                
                # Extract GPS data if available (GPS IFD, not an IFD0 string key)
                gps_info = {}
                gps_data = exif_dict.get_ifd(GPS_IFD_POINTER)
                if gps_data:
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
                
                # Extract all EXIF tags (IFD0 + Exif sub-IFD)
                for tag_id, value in _collect_exif_tags(exif_dict).items():
                    if tag_id == GPS_IFD_POINTER:
                        continue
                        
                    tag_name = TAGS.get(tag_id, f"Tag_{tag_id}")
                    
                    # Camera and lens information
                    if tag_name == 'Make':
                        metadata['Make'] = str(value).strip('\x00 \t\r\n')
                    elif tag_name == 'Model':
                        metadata['Model'] = str(value).strip('\x00 \t\r\n')
                    elif tag_name == 'LensModel':
                        metadata['LensModel'] = str(value).strip('\x00 \t\r\n')
                    elif tag_name == 'LensMake':
                        metadata['LensMake'] = str(value).strip('\x00 \t\r\n')
                    elif tag_name == 'SerialNumber':
                        metadata['CameraSerialNumber'] = str(value).strip('\x00 \t\r\n')
                    
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
                        try:
                            metadata['ExposureTime'] = float(value)
                        except (TypeError, ValueError):
                            pass
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
                        metadata['ExposureProgram'] = EXPOSURE_PROGRAMS.get(value, f'Unknown ({value})')
                    elif tag_name == 'MeteringMode':
                        metadata['MeteringMode'] = METERING_MODES.get(value, f'Unknown ({value})')
                    elif tag_name == 'Flash':
                        metadata['Flash'] = FLASH_MODES.get(value, f'Flash mode {value}')
                    elif tag_name == 'WhiteBalance':
                        metadata['WhiteBalance'] = WHITE_BALANCE_MODES.get(value, f'Unknown ({value})')
                    
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
                        metadata['ColorSpace'] = COLOR_SPACES.get(value, f'Unknown ({value})')
                    
                    # Software and metadata
                    elif tag_name == 'Software':
                        metadata['Software'] = str(value).strip('\x00 \t\r\n')
                    elif tag_name == 'Artist':
                        metadata['Artist'] = str(value).strip('\x00 \t\r\n')
                    elif tag_name == 'Copyright':
                        metadata['Copyright'] = str(value).strip('\x00 \t\r\n')
                    
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
                
                # Capture time is DateTimeOriginal; IFD0 DateTime is the file-write time
                if 'DateTimeOriginal' in metadata:
                    metadata['DateTime'] = metadata['DateTimeOriginal']
                
                return metadata
                
        except Exception:
            return self._extract_exiftool_exif(file_path)
    
    def _extract_exiftool_exif(self, file_path: Path) -> Dict[str, Any]:
        """Single-file ExifTool read (starts its own process; use extract_metadata_batch for
        more than a handful of files). Used when Pillow cannot read a file (e.g. CR3)."""
        if not self._exiftool_usable():
            return {}
        try:
            with ExifToolHelper(executable=_find_exiftool(), check_execute=False) as et:
                rows = et.get_tags([str(file_path)], EXIFTOOL_TAGS)
        except Exception as e:
            logger.debug(f"ExifTool fallback failed for {file_path.name}: {e}")
            return {}
        return normalize_exiftool_row(rows[0]) if rows else {}
    
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