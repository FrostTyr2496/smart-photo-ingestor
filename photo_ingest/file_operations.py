"""File operations manager for organized imports and raw backups."""

import shutil
import logging
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any
from datetime import datetime
from dataclasses import dataclass
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import IngestConfig, RawBackupConfig
from .deduplication import DuplicateStatus

logger = logging.getLogger(__name__)


class OperationType(Enum):
    """Type of file operation to perform."""
    ORGANIZED_ONLY = "organized"
    RAW_BACKUP_ONLY = "raw_backup"
    BOTH = "both"


@dataclass
class FileOperation:
    """Represents a planned file operation."""
    source_path: Path
    camera_code: str
    duplicate_status: DuplicateStatus
    metadata: Dict[str, Any]
    operation_type: OperationType
    dest_path: Optional[Path] = None
    raw_backup_path: Optional[Path] = None
    
    @property
    def event_date(self) -> str:
        """Get event date from metadata."""
        if 'DateTime' in self.metadata and self.metadata['DateTime']:
            try:
                if isinstance(self.metadata['DateTime'], str):
                    dt = datetime.fromisoformat(self.metadata['DateTime'].replace(':', '-', 2))
                else:
                    dt = self.metadata['DateTime']
                return dt.strftime('%Y-%m-%d')
            except Exception:
                pass
        
        # Fallback to file modification time
        try:
            return datetime.fromtimestamp(self.source_path.stat().st_mtime).strftime('%Y-%m-%d')
        except Exception:
            return datetime.now().strftime('%Y-%m-%d')


@dataclass
class OperationResults:
    """Results from file operations."""
    files_processed: int
    files_copied: int
    files_moved: int
    files_skipped: int
    duplicates_found: int
    errors: List[str]
    raw_backup_result: Optional['RawBackupResult'] = None


@dataclass
class RawBackupResult:
    """Result of raw backup operation."""
    backup_directory: Path
    files_backed_up: int
    files_skipped: int
    total_size: int
    errors: List[str]


class FileOperationsManager:
    """Manages file operations with progress tracking and verification."""
    
    def __init__(self, config: IngestConfig, progress_callback: Optional[Callable] = None):
        """Initialize file operations manager.
        
        Args:
            config: Application configuration
            progress_callback: Optional progress callback (phase, current, total, message)
        """
        self.config = config
        self.progress_callback = progress_callback
        self.raw_backup_manager = RawBackupManager(config.raw_backup)
    
    def plan_operations(self, files_with_metadata: Dict[Path, Dict[str, Any]], 
                       event_name: str, duplicate_results: Dict[Path, DuplicateStatus],
                       raw_only: bool = False, organized_only: bool = False) -> List[FileOperation]:
        """Plan file organization operations.
        
        Args:
            files_with_metadata: Dictionary mapping file paths to their metadata
            event_name: Event name for organization
            duplicate_results: Duplicate detection results
            raw_only: Only perform raw backup
            organized_only: Only perform organized import
            
        Returns:
            List of planned file operations
        """
        operations = []
        
        for file_path, metadata in files_with_metadata.items():
            # Determine operation type
            if raw_only:
                operation_type = OperationType.RAW_BACKUP_ONLY
            elif organized_only:
                operation_type = OperationType.ORGANIZED_ONLY
            else:
                operation_type = OperationType.BOTH
            
            # Get duplicate status
            duplicate_status = duplicate_results.get(file_path, DuplicateStatus.NEW)
            
            # Skip duplicates unless in raw backup mode
            if duplicate_status == DuplicateStatus.DUPLICATE and not raw_only:
                continue
            
            # Determine device code
            camera_code = metadata.get('device_code', 'Unknown')
            
            # Plan destination paths
            dest_path = None
            raw_backup_path = None
            
            if operation_type in [OperationType.ORGANIZED_ONLY, OperationType.BOTH]:
                dest_path = self._plan_organized_path(file_path, metadata, event_name, camera_code)
            
            if operation_type in [OperationType.RAW_BACKUP_ONLY, OperationType.BOTH]:
                raw_backup_path = self.raw_backup_manager.get_backup_path(file_path, file_path.parent)
            
            operation = FileOperation(
                source_path=file_path,
                dest_path=dest_path,
                raw_backup_path=raw_backup_path,
                camera_code=camera_code,
                duplicate_status=duplicate_status,
                metadata=metadata,
                operation_type=operation_type
            )
            
            operations.append(operation)
        
        return operations
    
    def execute_operations(self, operations: List[FileOperation], 
                          copy_mode: bool = True, dry_run: bool = False) -> OperationResults:
        """Execute file operations.
        
        Args:
            operations: List of operations to execute
            copy_mode: True to copy files, False to move them
            dry_run: If True, only simulate operations
            
        Returns:
            OperationResults with execution statistics
        """
        results = OperationResults(
            files_processed=0,
            files_copied=0,
            files_moved=0,
            files_skipped=0,
            duplicates_found=0,
            errors=[]
        )
        
        if dry_run:
            return self._simulate_operations(operations, copy_mode)
        
        # Group operations by type for efficient processing
        raw_backup_ops = []
        organized_ops = []
        
        for op in operations:
            if op.operation_type in [OperationType.RAW_BACKUP_ONLY, OperationType.BOTH]:
                raw_backup_ops.append(op)
            if op.operation_type in [OperationType.ORGANIZED_ONLY, OperationType.BOTH]:
                organized_ops.append(op)
        
        # Execute raw backup operations first
        if raw_backup_ops:
            raw_backup_result = self._execute_raw_backup_operations(raw_backup_ops, copy_mode)
            results.raw_backup_result = raw_backup_result
            results.errors.extend(raw_backup_result.errors)
        
        # Execute organized operations
        if organized_ops:
            org_results = self._execute_organized_operations(organized_ops, copy_mode)
            results.files_processed += org_results.files_processed
            results.files_copied += org_results.files_copied
            results.files_moved += org_results.files_moved
            results.files_skipped += org_results.files_skipped
            results.duplicates_found += org_results.duplicates_found
            results.errors.extend(org_results.errors)
        
        return results
    
    def _plan_organized_path(self, file_path: Path, metadata: Dict[str, Any], 
                           event_name: str, camera_code: str) -> Path:
        """Plan organized destination path for a file.
        
        Args:
            file_path: Source file path
            metadata: File metadata
            event_name: Event name
            camera_code: Camera/device code
            
        Returns:
            Planned destination path
        """
        # Extract date from metadata
        event_date = self._extract_date_from_metadata(metadata, file_path)
        year = event_date[:4]
        
        # Build destination path: archive_root/YYYY/YYYY-MM-DD_EventName/CameraCode/filename
        dest_dir = (Path(self.config.archive_root) / 
                   year / 
                   f"{event_date}_{event_name}" / 
                   camera_code)
        
        return dest_dir / file_path.name
    
    def _extract_date_from_metadata(self, metadata: Dict[str, Any], file_path: Path) -> str:
        """Extract date string from metadata."""
        # Try to get date from EXIF
        if 'DateTime' in metadata and metadata['DateTime']:
            try:
                if isinstance(metadata['DateTime'], str):
                    # Handle ISO format or EXIF format
                    date_str = metadata['DateTime']
                    if 'T' in date_str:
                        dt = datetime.fromisoformat(date_str.split('T')[0])
                    else:
                        dt = datetime.fromisoformat(date_str.replace(':', '-', 2))
                else:
                    dt = metadata['DateTime']
                return dt.strftime('%Y-%m-%d')
            except Exception as e:
                logger.debug(f"Failed to parse date from metadata: {e}")
        
        # Fallback to file modification time
        try:
            return datetime.fromtimestamp(file_path.stat().st_mtime).strftime('%Y-%m-%d')
        except Exception:
            return datetime.now().strftime('%Y-%m-%d')
    
    def _execute_raw_backup_operations(self, operations: List[FileOperation], 
                                     copy_mode: bool) -> RawBackupResult:
        """Execute raw backup operations."""
        source_files = [op.source_path for op in operations]
        source_root = self._find_common_root(source_files)
        
        return self.raw_backup_manager.create_raw_backup(
            source_files, source_root, copy_mode=copy_mode
        )
    
    def _execute_organized_operations(self, operations: List[FileOperation], 
                                    copy_mode: bool) -> OperationResults:
        """Execute organized import operations."""
        results = OperationResults(
            files_processed=0,
            files_copied=0,
            files_moved=0,
            files_skipped=0,
            duplicates_found=0,
            errors=[]
        )
        
        for i, operation in enumerate(operations):
            if self.progress_callback:
                self.progress_callback("organize", i + 1, len(operations), 
                                     f"Processing {operation.source_path.name}")
            
            try:
                # Skip duplicates
                if operation.duplicate_status == DuplicateStatus.DUPLICATE:
                    results.duplicates_found += 1
                    results.files_skipped += 1
                    continue
                
                # Ensure destination directory exists
                operation.dest_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Perform file operation
                if copy_mode:
                    shutil.copy2(operation.source_path, operation.dest_path)
                    results.files_copied += 1
                else:
                    shutil.move(str(operation.source_path), operation.dest_path)
                    results.files_moved += 1
                
                # Verify file integrity
                if not self.verify_file_integrity(operation.source_path, operation.dest_path):
                    results.errors.append(f"File integrity check failed: {operation.source_path}")
                
                results.files_processed += 1
                
            except Exception as e:
                error_msg = f"Failed to process {operation.source_path}: {e}"
                logger.error(error_msg)
                results.errors.append(error_msg)
        
        return results
    
    def _simulate_operations(self, operations: List[FileOperation], 
                           copy_mode: bool) -> OperationResults:
        """Simulate operations for dry-run mode."""
        results = OperationResults(
            files_processed=len(operations),
            files_copied=len(operations) if copy_mode else 0,
            files_moved=0 if copy_mode else len(operations),
            files_skipped=0,
            duplicates_found=sum(1 for op in operations if op.duplicate_status == DuplicateStatus.DUPLICATE),
            errors=[]
        )
        
        # Simulate raw backup if needed
        raw_backup_ops = [op for op in operations 
                         if op.operation_type in [OperationType.RAW_BACKUP_ONLY, OperationType.BOTH]]
        
        if raw_backup_ops:
            backup_dir = self.raw_backup_manager.get_backup_directory()
            results.raw_backup_result = RawBackupResult(
                backup_directory=backup_dir,
                files_backed_up=len(raw_backup_ops),
                files_skipped=0,
                total_size=sum(op.source_path.stat().st_size for op in raw_backup_ops),
                errors=[]
            )
        
        return results
    
    def verify_file_integrity(self, source: Path, dest: Path) -> bool:
        """Verify copied file matches source checksum.
        
        Args:
            source: Source file path
            dest: Destination file path
            
        Returns:
            True if files match, False otherwise
        """
        try:
            # Quick check: file sizes
            if source.stat().st_size != dest.stat().st_size:
                return False
            
            # For small files, do full checksum verification
            if source.stat().st_size < 10 * 1024 * 1024:  # 10MB threshold
                import hashlib
                
                def file_hash(path):
                    hash_md5 = hashlib.md5()
                    with open(path, "rb") as f:
                        for chunk in iter(lambda: f.read(4096), b""):
                            hash_md5.update(chunk)
                    return hash_md5.hexdigest()
                
                return file_hash(source) == file_hash(dest)
            
            # For large files, assume size check is sufficient
            return True
            
        except Exception as e:
            logger.error(f"File integrity check failed: {e}")
            return False
    
    def _find_common_root(self, file_paths: List[Path]) -> Path:
        """Find common root directory for a list of files."""
        if not file_paths:
            return Path.cwd()
        
        if len(file_paths) == 1:
            return file_paths[0].parent
        
        # Find common path
        common_parts = file_paths[0].parts
        for path in file_paths[1:]:
            new_common = []
            for i, (a, b) in enumerate(zip(common_parts, path.parts)):
                if a == b:
                    new_common.append(a)
                else:
                    break
            common_parts = new_common
        
        return Path(*common_parts) if common_parts else Path("/")


class RawBackupManager:
    """Handles raw backup operations preserving original structure."""
    
    def __init__(self, config: RawBackupConfig):
        """Initialize raw backup manager.
        
        Args:
            config: Raw backup configuration
        """
        self.config = config
        self.backup_timestamp = datetime.now().strftime(config.timestamp_format)
        self._backup_directory = None
    
    def create_raw_backup(self, files: List[Path], source_root: Path, 
                         copy_mode: bool = True) -> RawBackupResult:
        """Create timestamped raw backup preserving original structure.
        
        Args:
            files: List of files to backup
            source_root: Root directory of source files
            copy_mode: True to copy files, False to move them
            
        Returns:
            RawBackupResult with operation details
        """
        if not self.config.enabled:
            return RawBackupResult(
                backup_directory=Path(),
                files_backed_up=0,
                files_skipped=len(files),
                total_size=0,
                errors=["Raw backup is disabled"]
            )
        
        backup_dir = self.get_backup_directory()
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        result = RawBackupResult(
            backup_directory=backup_dir,
            files_backed_up=0,
            files_skipped=0,
            total_size=0,
            errors=[]
        )
        
        for file_path in files:
            try:
                backup_path = self.get_backup_path(file_path, source_root)
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                
                if copy_mode:
                    shutil.copy2(file_path, backup_path)
                else:
                    shutil.move(str(file_path), backup_path)
                
                result.files_backed_up += 1
                result.total_size += file_path.stat().st_size
                
            except Exception as e:
                error_msg = f"Failed to backup {file_path}: {e}"
                logger.error(error_msg)
                result.errors.append(error_msg)
                result.files_skipped += 1
        
        return result
    
    def get_backup_path(self, source_file: Path, source_root: Path) -> Path:
        """Generate backup path preserving relative structure.
        
        Args:
            source_file: Source file path
            source_root: Root directory of source files
            
        Returns:
            Backup file path
        """
        backup_dir = self.get_backup_directory()
        
        if self.config.preserve_structure:
            try:
                relative_path = source_file.relative_to(source_root)
            except ValueError:
                # File is not under source_root, use just the filename
                relative_path = source_file.name
        else:
            relative_path = source_file.name
        
        return backup_dir / relative_path
    
    def get_backup_directory(self) -> Path:
        """Get the backup directory, ensuring it's unique."""
        if self._backup_directory is None:
            self._backup_directory = self._ensure_unique_backup_dir()
        return self._backup_directory
    
    def _ensure_unique_backup_dir(self) -> Path:
        """Ensure backup directory is unique, append sequence if needed."""
        base_dir = Path(self.config.backup_root) / self.backup_timestamp
        
        if not base_dir.exists():
            return base_dir
        
        # Directory exists, find unique name with sequence number
        sequence = 1
        while True:
            unique_dir = Path(self.config.backup_root) / f"{self.backup_timestamp}_{sequence:02d}"
            if not unique_dir.exists():
                return unique_dir
            sequence += 1
            
            # Safety check to prevent infinite loop
            if sequence > 99:
                raise RuntimeError("Cannot create unique backup directory")