"""Deduplication engine with performance optimizations."""

import hashlib
import logging
import mmap
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
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
    
    def batch_calculate_hashes(self, file_paths: List[Path], 
                              progress_callback=None) -> Dict[Path, FileHashes]:
        """Calculate hashes for multiple files in parallel with progress tracking.
        
        Args:
            file_paths: List of file paths
            progress_callback: Optional callback function for progress updates
            
        Returns:
            Dictionary mapping file paths to their hashes
        """
        results = {}
        total_files = len(file_paths)
        processed_files = 0
        
        def update_progress():
            nonlocal processed_files
            processed_files += 1
            if progress_callback:
                progress_callback(processed_files, total_files)
        
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
                        update_progress()
                    except Exception as e:
                        logger.error(f"Failed to calculate hashes for {path}: {e}")
                        update_progress()
        else:
            # Sequential processing
            for path in file_paths:
                try:
                    results[path] = self.calculate_hashes(path)
                    update_progress()
                except Exception as e:
                    logger.error(f"Failed to calculate hashes for {path}: {e}")
                    update_progress()
        
        return results
    
    def batch_check_duplicates(self, file_paths: List[Path], 
                              progress_callback=None) -> Dict[Path, DuplicateCheckResult]:
        """Check multiple files for duplicates in parallel.
        
        Args:
            file_paths: List of file paths to check
            progress_callback: Optional callback for progress updates
            
        Returns:
            Dictionary mapping file paths to duplicate check results
        """
        results = {}
        total_files = len(file_paths)
        processed_files = 0
        
        def update_progress():
            nonlocal processed_files
            processed_files += 1
            if progress_callback:
                progress_callback(processed_files, total_files)
        
        if self.config.parallel_workers > 1 and len(file_paths) > 1:
            with ThreadPoolExecutor(max_workers=self.config.parallel_workers) as executor:
                future_to_path = {
                    executor.submit(self.check_duplicate, path): path
                    for path in file_paths
                }
                
                for future in as_completed(future_to_path):
                    path = future_to_path[future]
                    try:
                        results[path] = future.result()
                        update_progress()
                    except Exception as e:
                        logger.error(f"Failed to check duplicates for {path}: {e}")
                        update_progress()
        else:
            # Sequential processing
            for path in file_paths:
                try:
                    results[path] = self.check_duplicate(path)
                    update_progress()
                except Exception as e:
                    logger.error(f"Failed to check duplicates for {path}: {e}")
                    update_progress()
        
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
        """Get files with matching size for duplicate detection with smart tolerance."""
        # Use adaptive size tolerance based on file size
        if file_size < 1024:  # < 1KB
            size_tolerance = 0  # Exact match for small files
        elif file_size < 1024 * 1024:  # < 1MB
            size_tolerance = 1  # 1 byte tolerance for medium files
        else:  # >= 1MB
            size_tolerance = file_size // (1024 * 1024)  # 1 byte per MB tolerance
        
        return self.db_manager.get_files_by_size_range(
            file_size - size_tolerance, 
            file_size + size_tolerance
        )
    
    def get_size_distribution_stats(self) -> Dict[str, Any]:
        """Get statistics about file size distribution for optimization insights."""
        with self.db_manager._get_connection() as conn:
            cursor = conn.execute("""
                SELECT 
                    COUNT(*) as total_files,
                    MIN(file_size) as min_size,
                    MAX(file_size) as max_size,
                    AVG(file_size) as avg_size,
                    COUNT(CASE WHEN file_size < 1024 THEN 1 END) as small_files,
                    COUNT(CASE WHEN file_size >= 1024 AND file_size < 1048576 THEN 1 END) as medium_files,
                    COUNT(CASE WHEN file_size >= 1048576 THEN 1 END) as large_files
                FROM file_records
            """)
            
            row = cursor.fetchone()
            if row:
                return dict(row)
            return {}
    
    def _check_hash_duplicates(self, hashes: FileHashes, 
                              potential_duplicates: List[Dict]) -> DuplicateCheckResult:
        """Check for hash-based duplicates with enhanced similarity detection."""
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
            similar_result = self._find_similar_images(hashes.perceptual)
            if similar_result:
                return similar_result
        
        return DuplicateCheckResult(
            status=DuplicateStatus.NEW,
            hashes=hashes
        )
    
    def _find_similar_images(self, perceptual_hash: str, threshold: int = 5) -> Optional[DuplicateCheckResult]:
        """Find similar images using Hamming distance on perceptual hashes.
        
        Args:
            perceptual_hash: Perceptual hash to compare
            threshold: Maximum Hamming distance for similarity (default: 5)
            
        Returns:
            DuplicateCheckResult if similar image found, None otherwise
        """
        if not IMAGEHASH_AVAILABLE:
            return None
        
        try:
            import imagehash
            target_hash = imagehash.hex_to_hash(perceptual_hash)
        except (ValueError, AttributeError):
            logger.debug(f"Invalid perceptual hash format: {perceptual_hash}")
            return None
        
        # Get all files with perceptual hashes
        all_perceptual_files = self.db_manager.get_all_files_with_perceptual_hash()
        
        for existing_file in all_perceptual_files:
            try:
                existing_hash = imagehash.hex_to_hash(existing_file['perceptual_hash'])
                hamming_distance = target_hash - existing_hash
                
                if hamming_distance <= threshold:
                    # Calculate similarity score (0.0 to 1.0)
                    max_distance = len(perceptual_hash) * 4  # 4 bits per hex char
                    similarity_score = 1.0 - (hamming_distance / max_distance)
                    
                    return DuplicateCheckResult(
                        status=DuplicateStatus.SIMILAR,
                        existing_file=existing_file['source_path'],
                        similarity_score=similarity_score,
                        hashes=FileHashes(
                            sha256=existing_file['sha256_hash'],
                            perceptual=existing_file['perceptual_hash'],
                            size=existing_file['file_size']
                        )
                    )
            except (ValueError, AttributeError) as e:
                logger.debug(f"Error comparing perceptual hash for {existing_file['source_path']}: {e}")
                continue
        
        return None
    
    def _calculate_sha256_standard(self, file_path: Path) -> str:
        """Calculate SHA-256 hash using standard file reading."""
        hash_sha256 = hashlib.sha256()
        
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        
        return hash_sha256.hexdigest()
    
    def _calculate_sha256_mmap(self, file_path: Path) -> str:
        """Calculate SHA-256 hash using memory-mapped file with chunked processing."""
        hash_sha256 = hashlib.sha256()
        file_size = file_path.stat().st_size
        
        # For very large files, process in chunks to avoid memory issues
        chunk_size = min(64 * 1024 * 1024, file_size)  # 64MB chunks max
        
        try:
            with open(file_path, 'rb') as f:
                if file_size == 0:
                    return hash_sha256.hexdigest()
                
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    if file_size <= chunk_size:
                        # Small enough to process at once
                        hash_sha256.update(mm)
                    else:
                        # Process in chunks for large files
                        for i in range(0, file_size, chunk_size):
                            end = min(i + chunk_size, file_size)
                            hash_sha256.update(mm[i:end])
        except (OSError, ValueError) as e:
            logger.warning(f"Memory mapping failed for {file_path}, falling back to standard hashing: {e}")
            return self._calculate_sha256_standard(file_path)
        
        return hash_sha256.hexdigest()
    
    def _calculate_perceptual_hash(self, file_path: Path) -> str:
        """Calculate perceptual hash for image files using multiple algorithms."""
        if not IMAGEHASH_AVAILABLE:
            return None
        
        try:
            with Image.open(file_path) as img:
                # Convert to RGB if necessary for consistent hashing
                if img.mode not in ('RGB', 'L'):
                    img = img.convert('RGB')
                
                # Use difference hash (dHash) for better similarity detection
                # dHash is more robust to gamma correction and color histogram adjustments
                phash = imagehash.dhash(img, hash_size=8)
                return str(phash)
        except Exception as e:
            logger.debug(f"Failed to calculate perceptual hash for {file_path}: {e}")
            return None
    
    def calculate_multiple_perceptual_hashes(self, file_path: Path) -> Dict[str, str]:
        """Calculate multiple perceptual hashes for comprehensive similarity detection.
        
        Args:
            file_path: Path to image file
            
        Returns:
            Dictionary with different hash types and their values
        """
        if not IMAGEHASH_AVAILABLE:
            return {}
        
        hashes = {}
        try:
            with Image.open(file_path) as img:
                # Convert to RGB if necessary
                if img.mode not in ('RGB', 'L'):
                    img = img.convert('RGB')
                
                # Different hash algorithms for different use cases
                hashes['dhash'] = str(imagehash.dhash(img, hash_size=8))      # Good for crops/resizes
                hashes['ahash'] = str(imagehash.average_hash(img, hash_size=8))  # Good for brightness changes
                hashes['phash'] = str(imagehash.phash(img, hash_size=8))      # Good for minor modifications
                hashes['whash'] = str(imagehash.whash(img, hash_size=8))      # Wavelet hash, good for compression
                
        except Exception as e:
            logger.debug(f"Failed to calculate multiple perceptual hashes for {file_path}: {e}")
        
        return hashes
    
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