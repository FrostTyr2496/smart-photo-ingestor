"""Database management for photo ingest operations."""

import sqlite3
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class ProcessingStatus(Enum):
    """Status of file processing operations."""
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class OperationType(Enum):
    """Type of file operation performed."""
    ORGANIZED = "organized"
    RAW_BACKUP = "raw_backup"
    BOTH = "both"


@dataclass
class FileRecord:
    """Represents a file record in the database."""
    source_path: str
    sha256_hash: str
    file_size: int
    file_mtime: int
    created_date: str
    processed_date: str
    operation_type: str
    dest_path: Optional[str] = None
    raw_backup_path: Optional[str] = None
    perceptual_hash: Optional[str] = None
    camera_model: Optional[str] = None
    lens_model: Optional[str] = None
    device_code: Optional[str] = None
    processing_status: str = ProcessingStatus.COMPLETED.value


@dataclass
class ExifCacheRecord:
    """Represents cached EXIF data."""
    file_path: str
    file_mtime: int
    exif_data: Dict[str, Any]
    created_date: str


@dataclass
class DirectoryCacheRecord:
    """Represents cached directory scan data."""
    directory_path: str
    scan_timestamp: int
    file_count: int
    last_modified: int


class DatabaseManager:
    """Centralized database operations with caching support."""
    
    def __init__(self, db_path: str):
        """Initialize database manager.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self._batch_records: List[FileRecord] = []
        self._init_database()
    
    def _init_database(self):
        """Initialize database with schema creation."""
        logger.info(f"Initializing database at {self.db_path}")
        
        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        with self._get_connection() as conn:
            self._create_schema(conn)
            self._create_indexes(conn)
            conn.commit()
    
    @contextmanager
    def _get_connection(self):
        """Get database connection with proper cleanup."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row  # Enable dict-like access
        try:
            yield conn
        finally:
            conn.close()
    
    def _create_schema(self, conn: sqlite3.Connection):
        """Create database schema with all required tables."""
        
        # File records table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS file_records (
                id INTEGER PRIMARY KEY,
                source_path TEXT NOT NULL,
                dest_path TEXT,
                raw_backup_path TEXT,
                sha256_hash TEXT NOT NULL,
                perceptual_hash TEXT,
                file_size INTEGER NOT NULL,
                file_mtime INTEGER NOT NULL,
                created_date TEXT NOT NULL,
                processed_date TEXT NOT NULL,
                camera_model TEXT,
                lens_model TEXT,
                device_code TEXT,
                operation_type TEXT NOT NULL,
                processing_status TEXT DEFAULT 'completed',
                UNIQUE(sha256_hash)
            )
        """)
        
        # EXIF cache table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS exif_cache (
                id INTEGER PRIMARY KEY,
                file_path TEXT NOT NULL,
                file_mtime INTEGER NOT NULL,
                exif_data TEXT NOT NULL,
                created_date TEXT NOT NULL,
                UNIQUE(file_path, file_mtime)
            )
        """)
        
        # Directory cache table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS directory_cache (
                id INTEGER PRIMARY KEY,
                directory_path TEXT NOT NULL,
                scan_timestamp INTEGER NOT NULL,
                file_count INTEGER NOT NULL,
                last_modified INTEGER NOT NULL,
                UNIQUE(directory_path)
            )
        """)
    
    def _create_indexes(self, conn: sqlite3.Connection):
        """Create optimized indexes for common query patterns."""
        
        # File records indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sha256 ON file_records(sha256_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_perceptual ON file_records(perceptual_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_size_mtime ON file_records(file_size, file_mtime)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_device_date ON file_records(device_code, created_date)")
        
        # EXIF cache indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_exif_path_mtime ON exif_cache(file_path, file_mtime)")
        
        # Directory cache indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dir_path ON directory_cache(directory_path)")
    
    def get_cached_exif(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """Retrieve cached EXIF data if file unchanged.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Cached EXIF data if available and file unchanged, None otherwise
        """
        try:
            file_mtime = int(file_path.stat().st_mtime)
        except (OSError, IOError):
            return None
        
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT exif_data FROM exif_cache WHERE file_path = ? AND file_mtime = ?",
                (str(file_path), file_mtime)
            )
            row = cursor.fetchone()
            
            if row:
                try:
                    return json.loads(row['exif_data'])
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON in cached EXIF data for {file_path}")
                    return None
            
            return None
    
    def cache_exif(self, file_path: Path, exif_data: Dict[str, Any]):
        """Cache EXIF data for future use.
        
        Args:
            file_path: Path to the file
            exif_data: EXIF data to cache
        """
        try:
            file_mtime = int(file_path.stat().st_mtime)
        except (OSError, IOError):
            logger.warning(f"Cannot get mtime for {file_path}, skipping EXIF cache")
            return
        
        created_date = datetime.now().isoformat()
        exif_json = json.dumps(exif_data)
        
        with self._get_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO exif_cache 
                   (file_path, file_mtime, exif_data, created_date) 
                   VALUES (?, ?, ?, ?)""",
                (str(file_path), file_mtime, exif_json, created_date)
            )
            conn.commit()
    
    def is_directory_changed(self, dir_path: Path) -> bool:
        """Check if directory has changed since last scan.
        
        Args:
            dir_path: Path to directory
            
        Returns:
            True if directory has changed or not cached, False otherwise
        """
        try:
            current_mtime = int(dir_path.stat().st_mtime)
        except (OSError, IOError):
            return True
        
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT last_modified FROM directory_cache WHERE directory_path = ?",
                (str(dir_path),)
            )
            row = cursor.fetchone()
            
            if row:
                return current_mtime > row['last_modified']
            
            return True  # Not cached, consider changed
    
    def update_directory_cache(self, dir_path: Path, file_count: int):
        """Update directory scan cache.
        
        Args:
            dir_path: Path to directory
            file_count: Number of files found in directory
        """
        try:
            current_mtime = int(dir_path.stat().st_mtime)
        except (OSError, IOError):
            logger.warning(f"Cannot get mtime for {dir_path}, skipping directory cache")
            return
        
        scan_timestamp = int(datetime.now().timestamp())
        
        with self._get_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO directory_cache 
                   (directory_path, scan_timestamp, file_count, last_modified) 
                   VALUES (?, ?, ?, ?)""",
                (str(dir_path), scan_timestamp, file_count, current_mtime)
            )
            conn.commit()
    
    def get_files_by_size_range(self, min_size: int, max_size: int) -> List[Dict[str, Any]]:
        """Fast lookup by file size for duplicate detection.
        
        Args:
            min_size: Minimum file size
            max_size: Maximum file size
            
        Returns:
            List of file records matching size criteria
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """SELECT source_path, sha256_hash, perceptual_hash, file_size, device_code 
                   FROM file_records 
                   WHERE file_size BETWEEN ? AND ?""",
                (min_size, max_size)
            )
            return [dict(row) for row in cursor.fetchall()]
    
    def get_file_by_hash(self, sha256_hash: str) -> Optional[Dict[str, Any]]:
        """Get file record by SHA-256 hash.
        
        Args:
            sha256_hash: SHA-256 hash to search for
            
        Returns:
            File record if found, None otherwise
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM file_records WHERE sha256_hash = ?",
                (sha256_hash,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_files_by_perceptual_hash(self, perceptual_hash: str, threshold: float = 0.9) -> List[Dict[str, Any]]:
        """Get files with similar perceptual hashes.
        
        Args:
            perceptual_hash: Perceptual hash to search for
            threshold: Similarity threshold (not implemented in this basic version)
            
        Returns:
            List of similar file records
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM file_records WHERE perceptual_hash = ?",
                (perceptual_hash,)
            )
            return [dict(row) for row in cursor.fetchall()]
    
    def insert_file_record(self, record: FileRecord):
        """Insert a single file record.
        
        Args:
            record: File record to insert
        """
        with self._get_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO file_records 
                   (source_path, dest_path, raw_backup_path, sha256_hash, perceptual_hash,
                    file_size, file_mtime, created_date, processed_date, camera_model,
                    lens_model, device_code, operation_type, processing_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (record.source_path, record.dest_path, record.raw_backup_path,
                 record.sha256_hash, record.perceptual_hash, record.file_size,
                 record.file_mtime, record.created_date, record.processed_date,
                 record.camera_model, record.lens_model, record.device_code,
                 record.operation_type, record.processing_status)
            )
            conn.commit()
    
    def batch_insert_records(self, records: List[FileRecord]):
        """Insert multiple records in single transaction.
        
        Args:
            records: List of file records to insert
        """
        if not records:
            return
        
        with self._get_connection() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO file_records 
                   (source_path, dest_path, raw_backup_path, sha256_hash, perceptual_hash,
                    file_size, file_mtime, created_date, processed_date, camera_model,
                    lens_model, device_code, operation_type, processing_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(r.source_path, r.dest_path, r.raw_backup_path, r.sha256_hash,
                  r.perceptual_hash, r.file_size, r.file_mtime, r.created_date,
                  r.processed_date, r.camera_model, r.lens_model, r.device_code,
                  r.operation_type, r.processing_status) for r in records]
            )
            conn.commit()
    
    def is_file_unchanged(self, file_path: Path, current_mtime: int) -> bool:
        """Check if file is unchanged based on path and modification time.
        
        Args:
            file_path: Path to the file
            current_mtime: Current modification time
            
        Returns:
            True if file exists in database with same mtime, False otherwise
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT id FROM file_records WHERE source_path = ? AND file_mtime = ?",
                (str(file_path), current_mtime)
            )
            return cursor.fetchone() is not None
    
    def get_file_by_path_and_mtime(self, file_path: str, mtime: int) -> Optional[Dict[str, Any]]:
        """Get file record by path and modification time.
        
        Args:
            file_path: File path to search for
            mtime: Modification time
            
        Returns:
            File record if found, None otherwise
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM file_records WHERE source_path = ? AND file_mtime = ?",
                (file_path, mtime)
            )
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_database_stats(self) -> Dict[str, int]:
        """Get database statistics.
        
        Returns:
            Dictionary with database statistics
        """
        with self._get_connection() as conn:
            stats = {}
            
            # File records count
            cursor = conn.execute("SELECT COUNT(*) as count FROM file_records")
            stats['file_records'] = cursor.fetchone()['count']
            
            # EXIF cache count
            cursor = conn.execute("SELECT COUNT(*) as count FROM exif_cache")
            stats['exif_cache'] = cursor.fetchone()['count']
            
            # Directory cache count
            cursor = conn.execute("SELECT COUNT(*) as count FROM directory_cache")
            stats['directory_cache'] = cursor.fetchone()['count']
            
            # Unique devices
            cursor = conn.execute("SELECT COUNT(DISTINCT device_code) as count FROM file_records")
            stats['unique_devices'] = cursor.fetchone()['count']
            
            return stats
    
    def cleanup_old_cache_entries(self, days_old: int = 30):
        """Clean up old cache entries.
        
        Args:
            days_old: Remove cache entries older than this many days
        """
        if days_old == 0:
            # Special case: delete all entries
            with self._get_connection() as conn:
                conn.execute("DELETE FROM exif_cache")
                conn.execute("DELETE FROM directory_cache")
                conn.commit()
            return
        
        cutoff_date = datetime.now().timestamp() - (days_old * 24 * 60 * 60)
        
        with self._get_connection() as conn:
            # Clean old EXIF cache entries
            conn.execute(
                "DELETE FROM exif_cache WHERE created_date < ?",
                (datetime.fromtimestamp(cutoff_date).isoformat(),)
            )
            
            # Clean old directory cache entries
            conn.execute(
                "DELETE FROM directory_cache WHERE scan_timestamp < ?",
                (int(cutoff_date),)
            )
            
            conn.commit()
    
    def migrate_database(self):
        """Handle database migrations for schema updates."""
        # This is a placeholder for future migrations
        # In a real implementation, you would check schema version
        # and apply necessary migrations
        logger.info("Database migration check completed")
        pass