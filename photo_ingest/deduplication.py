"""Deduplication engine with performance optimizations."""

import hashlib
import logging
import mmap
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from enum import Enum
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import imagehash
    from PIL import Image
    IMAGEHASH_AVAILABLE = True
except ImportError:
    IMAGEHASH_AVAILABLE = False

from .config import PerformanceConfig
from .database import DatabaseManager, FileRecord

logger = logging.getLogger(__name__)


class DuplicateStatus(Enum):
    """Status of duplicate detection."""
    NEW = "new"
    DUPLICATE = "duplicate"
    SIMILAR = "similar"


@dataclass
class FileHashes:
    """Container for file hash information."""
    sha256: str
    size: int
    perceptual: Optional[str] = None
    
    def __post_init__(self):
        """Validate hash values."""
        if not self.sha256 or len(self.sha256) != 64:
            raise ValueError("Invalid SHA-256 hash")
        if self.size < 0:
            raise ValueError("File size cannot be negative")


@dataclass
class DuplicateCheckResult:
    """Result of duplicate check operation."""
    status: DuplicateStatus
    existing_file: Optional[str] = None
    similarity_score: Optional[float] = None
    hashes: Optional[FileHashes] = None


class DeduplicationEngine:
    """Manages file deduplication with performance optimizations."""
    
    def __init__(self, db_manager: DatabaseManager, config: PerformanceConfig):
        """Initialize deduplication engine.
        
        Args:
            db_manager: Database manager for storing hash records
            config: Performance configuration
        """
        self.db_manager = db_manager
        self.config = config
        self._pending_records: List[FileRecord] = []
        
        if not IMAGEHASH_AVAILABLE:
            logger.warning("imagehash not available - perceptual hashing disabled")
    
    def check_duplicate(self, file_path: Path) -> DuplicateCheckResult:
        """Check if file is duplicate using size pre-filter and cached hashes.
        
        Args:
            file_path: Path to file to check
            
        Returns:
            DuplicateCheckResult with status and details
        """
        try:
            file_size = file_path.stat().st_size
            file_mtime = int(file_path.stat().st_mtime)
        except (OSError, IOError) as e:
            logger.error(f"Cannot access file {file_path}: {e}")
            raise
        
        # Check if we've already processed this exact file
        if self._is_file_unchanged(file_path, file_mtime):
            existing_record = self.db_manager.get_file_by_path_and_mtime(str(file_path), file_mtime)
            if existing_record:
                return DuplicateCheckResult(
                    status=DuplicateStatus.DUPLICATE,
                    existing_file=existing_record.get('source_path'),
                    hashes=FileHashes(
                        sha256=existing_record['sha256_hash'],
                        perceptual=existing_record.get('perceptual_hash'),
                        size=existing_record['file_size']
                    )
                )
        
        # Size-based pre-filtering before expensive hashing
        potential_duplicates = self._get_files_by_size(file_size)
        if not potential_duplicates:
            # No files with same size, definitely new
            hashes = self.calculate_hashes(file_path)
            return DuplicateCheckResult(
                status=DuplicateStatus.NEW,
                hashes=hashes
            )
        
        # Calculate hashes and check for duplicates
        hashes = self.calculate_hashes(file_path)
        return self._check_hash_duplicates(hashes, potential_duplicates)
    
    def calculate_hashes(self, file_path: Path) -> FileHashes:
        """Calculate hashes using memory-mapped files for performance.
        
        Args:
            file_path: Path to file
            
        Returns:
            FileHashes object with calculated hashes
        """
        file_size = file_path.stat().st_size
        
        # Use memory-mapped hashing for large files if enabled
        if (self.config.memory_mapped_hashing and 
            file_size > 1024 * 1024):  # 1MB threshold
            sha256_hash = self._calculate_sha256_mmap(file_path)
        else:
            sha256_hash = self._calculate_sha256_standard(file_path)
        
        # Calculate perceptual hash for images if available
        perceptual_hash = None
        if IMAGEHASH_AVAILABLE and self._is_image_file(file_path):
            try:
                perceptual_hash = self._calculate_perceptual_hash(file_path)
            except Exception as e:
                logger.debug(f"Failed to calculate perceptual hash for {file_path}: {e}")
        
        return FileHashes(
            sha256=sha256_hash,
            perceptual=perceptual_hash,
            size=file_size
        )
    
    def batch_calculate_hashes(self, file_paths: List[Path]) -> Dict[Path, FileHashes]:
        """Calculate hashes for multiple files in parallel.
        
        Args:
            file_paths: List of file paths
            
        Returns:
            Dictionary mapping file paths to their hashes
        """
        results = {}
        
        if self.config.parallel_workers > 1 and len(file_paths) > 1:
            with ThreadPoolExecutor(max_workers=self.config.parallel_workers) as executor:
                future_to_path = {
                    executor.submit(self.calculate_hashes, path): path
                    for path in file_paths
                }
                
                for future in as_completed(future_to_path):
                    path = future_to_path[future]
                    try:
                        results[path] = future.result()
                    except Exception as e:
                        logger.error(f"Failed to calculate hashes for {path}: {e}")
        else:
            # Sequential processing
            for path in file_paths:
                try:
                    results[path] = self.calculate_hashes(path)
                except Exception as e:
                    logger.error(f"Failed to calculate hashes for {path}: {e}")
        
        return results
    
    def store_file_record(self, file_path: Path, hashes: FileHashes, 
                         dest_path: Optional[Path] = None,
                         raw_backup_path: Optional[Path] = None,
                         metadata: Optional[Dict] = None):
        """Store file record in database.
        
        Args:
            file_path: Source file path
            hashes: File hashes
            dest_path: Destination path (for organized import)
            raw_backup_path: Raw backup path
            metadata: Additional metadata
        """
        try:
            file_mtime = int(file_path.stat().st_mtime)
        except (OSError, IOError):
            logger.error(f"Cannot access file {file_path}")
            return
        
        from datetime import datetime
        
        record = FileRecord(
            source_path=str(file_path),
            dest_path=str(dest_path) if dest_path else None,
            raw_backup_path=str(raw_backup_path) if raw_backup_path else None,
            sha256_hash=hashes.sha256,
            perceptual_hash=hashes.perceptual,
            file_size=hashes.size,
            file_mtime=file_mtime,
            created_date=datetime.now().isoformat(),
            processed_date=datetime.now().isoformat(),
            camera_model=metadata.get('Model', '') if metadata else '',
            lens_model=metadata.get('LensModel', '') if metadata else '',
            device_code=metadata.get('device_code', '') if metadata else '',
            operation_type='organized' if dest_path else 'raw_backup'
        )
        
        if self.config.batch_size > 1:
            self._pending_records.append(record)
            if len(self._pending_records) >= self.config.batch_size:
                self._flush_pending_records()
        else:
            self.db_manager.insert_file_record(record)
    
    def flush_pending_records(self):
        """Flush any pending records to database."""
        self._flush_pending_records()
    
    def _flush_pending_records(self):
        """Internal method to flush pending records."""
        if self._pending_records:
            self.db_manager.batch_insert_records(self._pending_records)
            self._pending_records.clear()
    
    def _is_file_unchanged(self, file_path: Path, current_mtime: int) -> bool:
        """Check if file is unchanged based on path and modification time."""
        return self.db_manager.is_file_unchanged(file_path, current_mtime)
    
    def _get_files_by_size(self, file_size: int) -> List[Dict]:
        """Get files with matching size for duplicate detection."""
        # Use a small range to account for filesystem differences
        size_tolerance = 0  # Exact match for now
        return self.db_manager.get_files_by_size_range(
            file_size - size_tolerance, 
            file_size + size_tolerance
        )
    
    def _check_hash_duplicates(self, hashes: FileHashes, 
                              potential_duplicates: List[Dict]) -> DuplicateCheckResult:
        """Check for hash-based duplicates."""
        # Check for exact SHA-256 match
        for existing in potential_duplicates:
            if existing['sha256_hash'] == hashes.sha256:
                return DuplicateCheckResult(
                    status=DuplicateStatus.DUPLICATE,
                    existing_file=existing['source_path'],
                    hashes=hashes
                )
        
        # Check for perceptual hash similarity if available
        if hashes.perceptual and IMAGEHASH_AVAILABLE:
            similar_files = self.db_manager.get_files_by_perceptual_hash(hashes.perceptual)
            if similar_files:
                # For now, treat any perceptual match as similar
                # In a more sophisticated implementation, you'd calculate
                # Hamming distance between hashes
                return DuplicateCheckResult(
                    status=DuplicateStatus.SIMILAR,
                    existing_file=similar_files[0]['source_path'],
                    similarity_score=0.9,  # Placeholder
                    hashes=hashes
                )
        
        return DuplicateCheckResult(
            status=DuplicateStatus.NEW,
            hashes=hashes
        )
    
    def _calculate_sha256_standard(self, file_path: Path) -> str:
        """Calculate SHA-256 hash using standard file reading."""
        hash_sha256 = hashlib.sha256()
        
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        
        return hash_sha256.hexdigest()
    
    def _calculate_sha256_mmap(self, file_path: Path) -> str:
        """Calculate SHA-256 hash using memory-mapped file."""
        hash_sha256 = hashlib.sha256()
        
        with open(file_path, 'rb') as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                hash_sha256.update(mm)
        
        return hash_sha256.hexdigest()
    
    def _calculate_perceptual_hash(self, file_path: Path) -> str:
        """Calculate perceptual hash for image files."""
        if not IMAGEHASH_AVAILABLE:
            return None
        
        try:
            with Image.open(file_path) as img:
                # Use average hash for good balance of speed and accuracy
                phash = imagehash.average_hash(img)
                return str(phash)
        except Exception as e:
            logger.debug(f"Failed to calculate perceptual hash for {file_path}: {e}")
            return None
    
    def _is_image_file(self, file_path: Path) -> bool:
        """Check if file is an image that supports perceptual hashing."""
        image_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.heic', '.heif'}
        return file_path.suffix.lower() in image_extensions
    
    def get_duplicate_statistics(self) -> Dict[str, int]:
        """Get statistics about duplicates in the database."""
        stats = self.db_manager.get_database_stats()
        
        # Add duplicate-specific statistics
        with self.db_manager._get_connection() as conn:
            # Count files with same SHA-256 (exact duplicates)
            cursor = conn.execute("""
                SELECT COUNT(*) as count FROM (
                    SELECT sha256_hash FROM file_records 
                    GROUP BY sha256_hash 
                    HAVING COUNT(*) > 1
                )
            """)
            stats['duplicate_groups'] = cursor.fetchone()['count']
            
            # Count files with perceptual hashes (potential similar files)
            cursor = conn.execute("""
                SELECT COUNT(*) as count FROM file_records 
                WHERE perceptual_hash IS NOT NULL
            """)
            stats['files_with_perceptual_hash'] = cursor.fetchone()['count']
        
        return stats