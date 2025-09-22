"""File scanning and discovery functionality with progress tracking."""

import os
import logging
from pathlib import Path
from typing import List, Dict, Set, Optional, Callable, Iterator, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
import mimetypes

from .config import FileTypes, PerformanceConfig


logger = logging.getLogger(__name__)


@dataclass
class FileInfo:
    """Basic file information for discovered files."""
    path: Path
    size: int
    modified_time: datetime
    file_type: str  # 'raw', 'jpeg', 'video'
    extension: str
    mime_type: Optional[str] = None
    
    def __post_init__(self):
        """Set mime type if not provided."""
        if self.mime_type is None:
            self.mime_type, _ = mimetypes.guess_type(str(self.path))


@dataclass
class ScanResult:
    """Result of directory scanning operation."""
    files: List[FileInfo]
    total_files: int
    total_size: int
    scan_time: float
    errors: List[str]
    directories_scanned: int
    
    @property
    def files_by_type(self) -> Dict[str, List[FileInfo]]:
        """Group files by type."""
        result = {'raw': [], 'jpeg': [], 'video': []}
        for file_info in self.files:
            if file_info.file_type in result:
                result[file_info.file_type].append(file_info)
        return result
    
    @property
    def size_by_type(self) -> Dict[str, int]:
        """Get total size by file type."""
        result = {'raw': 0, 'jpeg': 0, 'video': 0}
        for file_info in self.files:
            if file_info.file_type in result:
                result[file_info.file_type] += file_info.size
        return result


class FileScanner:
    """Handles file scanning and discovery with progress tracking."""
    
    def __init__(self, 
                 file_types: FileTypes,
                 performance_config: Optional[PerformanceConfig] = None,
                 progress_callback: Optional[Callable[[int, int, str], None]] = None):
        """
        Initialize file scanner.
        
        Args:
            file_types: Configuration for supported file types
            performance_config: Performance optimization settings
            progress_callback: Optional callback for progress updates (current, total, message)
        """
        self.file_types = file_types
        self.performance_config = performance_config or PerformanceConfig()
        self.progress_callback = progress_callback
        
        # Build set of supported extensions for fast lookup
        self._supported_extensions = set()
        for ext_list in [file_types.raw, file_types.jpeg, file_types.video]:
            self._supported_extensions.update(ext.lower() for ext in ext_list)
        
        # Create extension to type mapping
        self._extension_to_type = {}
        for ext in file_types.raw:
            self._extension_to_type[ext.lower()] = 'raw'
        for ext in file_types.jpeg:
            self._extension_to_type[ext.lower()] = 'jpeg'
        for ext in file_types.video:
            self._extension_to_type[ext.lower()] = 'video'
    
    def scan_directory(self, directory: Path, recursive: bool = True) -> ScanResult:
        """
        Scan directory for supported file types.
        
        Args:
            directory: Directory to scan
            recursive: Whether to scan subdirectories recursively
            
        Returns:
            ScanResult: Results of the scan operation
            
        Raises:
            FileNotFoundError: If directory doesn't exist
            PermissionError: If directory is not accessible
        """
        start_time = datetime.now()
        
        if not directory.exists():
            raise FileNotFoundError(f"Directory not found: {directory}")
        
        if not directory.is_dir():
            raise ValueError(f"Path is not a directory: {directory}")
        
        logger.info(f"Starting {'recursive' if recursive else 'non-recursive'} scan of {directory}")
        
        try:
            # First pass: discover all files
            all_files = list(self._discover_files(directory, recursive))
            
            if self.progress_callback:
                self.progress_callback(0, len(all_files), "Analyzing files...")
            
            # Second pass: extract file information
            if self.performance_config.parallel_workers > 1:
                files = self._extract_file_info_parallel(all_files)
            else:
                files = self._extract_file_info_sequential(all_files)
            
            scan_time = (datetime.now() - start_time).total_seconds()
            
            result = ScanResult(
                files=files,
                total_files=len(files),
                total_size=sum(f.size for f in files),
                scan_time=scan_time,
                errors=[],  # TODO: Collect errors during scanning
                directories_scanned=len(set(f.path.parent for f in files))
            )
            
            logger.info(f"Scan completed: {result.total_files} files, "
                       f"{result.total_size / (1024*1024):.1f} MB, "
                       f"{scan_time:.2f}s")
            
            return result
            
        except Exception as e:
            logger.error(f"Error scanning directory {directory}: {e}")
            raise
    
    def _discover_files(self, directory: Path, recursive: bool) -> Iterator[Path]:
        """Discover all supported files in directory."""
        try:
            if recursive:
                # Use rglob for recursive scanning
                for file_path in directory.rglob("*"):
                    if self._is_supported_file(file_path):
                        yield file_path
            else:
                # Use iterdir for non-recursive scanning
                for file_path in directory.iterdir():
                    if file_path.is_file() and self._is_supported_file(file_path):
                        yield file_path
        except PermissionError as e:
            logger.warning(f"Permission denied accessing {directory}: {e}")
        except Exception as e:
            logger.error(f"Error discovering files in {directory}: {e}")
    
    def _is_supported_file(self, file_path: Path) -> bool:
        """Check if file is a supported type."""
        if not file_path.is_file():
            return False
        
        extension = file_path.suffix.lower()
        if extension.startswith('.'):
            extension = extension[1:]
        
        return extension in self._supported_extensions
    
    def _get_file_type(self, file_path: Path) -> str:
        """Get file type category for a file."""
        extension = file_path.suffix.lower()
        if extension.startswith('.'):
            extension = extension[1:]
        
        return self._extension_to_type.get(extension, 'unknown')
    
    def _extract_file_info_sequential(self, file_paths: List[Path]) -> List[FileInfo]:
        """Extract file information sequentially."""
        files = []
        for i, file_path in enumerate(file_paths):
            try:
                file_info = self._create_file_info(file_path)
                files.append(file_info)
                
                if self.progress_callback:
                    self.progress_callback(i + 1, len(file_paths), f"Processing {file_path.name}")
                    
            except Exception as e:
                logger.warning(f"Error processing file {file_path}: {e}")
        
        return files
    
    def _extract_file_info_parallel(self, file_paths: List[Path]) -> List[FileInfo]:
        """Extract file information in parallel."""
        files = []
        completed = 0
        
        with ThreadPoolExecutor(max_workers=self.performance_config.parallel_workers) as executor:
            # Submit all tasks
            future_to_path = {
                executor.submit(self._create_file_info, path): path 
                for path in file_paths
            }
            
            # Process completed tasks
            for future in as_completed(future_to_path):
                path = future_to_path[future]
                completed += 1
                
                try:
                    file_info = future.result()
                    files.append(file_info)
                except Exception as e:
                    logger.warning(f"Error processing file {path}: {e}")
                
                if self.progress_callback:
                    self.progress_callback(completed, len(file_paths), f"Processing {path.name}")
        
        return files
    
    def _create_file_info(self, file_path: Path) -> FileInfo:
        """Create FileInfo object for a file."""
        try:
            stat = file_path.stat()
            return FileInfo(
                path=file_path,
                size=stat.st_size,
                modified_time=datetime.fromtimestamp(stat.st_mtime),
                file_type=self._get_file_type(file_path),
                extension=file_path.suffix.lower().lstrip('.'),
                mime_type=None  # Will be set in __post_init__
            )
        except Exception as e:
            logger.error(f"Error getting file info for {file_path}: {e}")
            raise
    
    def filter_by_size(self, files: List[FileInfo], min_size: int = 0, max_size: Optional[int] = None) -> List[FileInfo]:
        """Filter files by size range."""
        filtered = []
        for file_info in files:
            if file_info.size >= min_size:
                if max_size is None or file_info.size <= max_size:
                    filtered.append(file_info)
        return filtered
    
    def filter_by_type(self, files: List[FileInfo], file_types: List[str]) -> List[FileInfo]:
        """Filter files by type."""
        return [f for f in files if f.file_type in file_types]
    
    def filter_by_date_range(self, files: List[FileInfo], 
                           start_date: Optional[datetime] = None,
                           end_date: Optional[datetime] = None) -> List[FileInfo]:
        """Filter files by modification date range."""
        filtered = []
        for file_info in files:
            if start_date and file_info.modified_time < start_date:
                continue
            if end_date and file_info.modified_time > end_date:
                continue
            filtered.append(file_info)
        return filtered
    
    def group_by_directory(self, files: List[FileInfo]) -> Dict[Path, List[FileInfo]]:
        """Group files by their parent directory."""
        groups = {}
        for file_info in files:
            parent = file_info.path.parent
            if parent not in groups:
                groups[parent] = []
            groups[parent].append(file_info)
        return groups
    
    def get_summary_stats(self, files: List[FileInfo]) -> Dict[str, any]:
        """Get summary statistics for a list of files."""
        if not files:
            return {
                'total_files': 0,
                'total_size': 0,
                'by_type': {},
                'by_extension': {},
                'date_range': None,
                'largest_file': None,
                'smallest_file': None
            }
        
        # Count by type
        by_type = {}
        for file_info in files:
            by_type[file_info.file_type] = by_type.get(file_info.file_type, 0) + 1
        
        # Count by extension
        by_extension = {}
        for file_info in files:
            ext = file_info.extension
            by_extension[ext] = by_extension.get(ext, 0) + 1
        
        # Date range
        dates = [f.modified_time for f in files]
        date_range = (min(dates), max(dates)) if dates else None
        
        # Size extremes
        files_by_size = sorted(files, key=lambda f: f.size)
        largest_file = files_by_size[-1] if files_by_size else None
        smallest_file = files_by_size[0] if files_by_size else None
        
        return {
            'total_files': len(files),
            'total_size': sum(f.size for f in files),
            'by_type': by_type,
            'by_extension': by_extension,
            'date_range': date_range,
            'largest_file': largest_file,
            'smallest_file': smallest_file
        }


class DirectoryValidator:
    """Validates directory structure and accessibility."""
    
    @staticmethod
    def validate_source_directory(directory: Path) -> Tuple[bool, List[str]]:
        """
        Validate source directory for scanning.
        
        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []
        
        if not directory.exists():
            errors.append(f"Directory does not exist: {directory}")
            return False, errors
        
        if not directory.is_dir():
            errors.append(f"Path is not a directory: {directory}")
            return False, errors
        
        # Check read permissions
        if not os.access(directory, os.R_OK):
            errors.append(f"No read permission for directory: {directory}")
            return False, errors
        
        # Check if directory is empty
        try:
            if not any(directory.iterdir()):
                errors.append(f"Directory is empty: {directory}")
                return False, errors
        except PermissionError:
            errors.append(f"Cannot list directory contents: {directory}")
            return False, errors
        
        return True, errors
    
    @staticmethod
    def check_directory_permissions(directory: Path) -> Dict[str, bool]:
        """Check various permissions on a directory."""
        return {
            'exists': directory.exists(),
            'is_dir': directory.is_dir() if directory.exists() else False,
            'readable': os.access(directory, os.R_OK) if directory.exists() else False,
            'writable': os.access(directory, os.W_OK) if directory.exists() else False,
            'executable': os.access(directory, os.X_OK) if directory.exists() else False,
        }