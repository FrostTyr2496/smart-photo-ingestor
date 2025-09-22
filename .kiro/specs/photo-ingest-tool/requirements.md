# Requirements Document

## Introduction

The Photo Ingest Tool is a command-line Python application designed to organize photography imports on macOS. The tool serves as the first step in a larger photo workflow (Imports → Staging → Catalog/Exports) and provides two main functions: analyzing photo folders to extract EXIF metadata summaries, and ingesting photos into a structured archive with deduplication and optional AI-generated notes.

## Requirements

### Requirement 1: Enhanced Analyze Command with Optional Visual Analysis

**User Story:** As a photographer, I want to analyze a folder of photos to understand both technical metadata and visual content, so that I can make informed decisions about organization and event naming.

#### Acceptance Criteria

1. WHEN I run `python photo_ingest.py analyze /path/to/folder` THEN the system SHALL scan the folder for supported file types and extract EXIF metadata including camera models, lenses, date range, ISO/aperture/shutter ranges, and GPS presence
2. WHEN I specify `--peek` flag THEN the system SHALL additionally perform visual content analysis using a vision-capable LLM
3. WHEN running with `--peek` THEN the system SHALL randomly sample N images from each camera/device folder and downscale them to approximately 1MP resolution for LLM processing
4. WHEN I specify `--exif-only` flag THEN the system SHALL skip visual analysis even if LLM is configured
5. WHEN I specify `--samples N` with `--peek` THEN the system SHALL sample N images per device for visual analysis
6. WHEN analysis is complete THEN the system SHALL output a human-readable summary table combining EXIF and visual analysis results
7. WHEN I specify the `--json` flag THEN the system SHALL output the complete analysis in JSON format for automation
8. WHEN running analyze command THEN the system SHALL NOT move any files or write to any databases
9. WHEN running analyze without `--peek` THEN the system SHALL NOT require any configuration files
10. WHEN visual analysis fails THEN the system SHALL fall back to EXIF-only analysis and continue processing

### Requirement 2: Ingest Command Configuration

**User Story:** As a photographer, I want to configure how photos are ingested into my archive, so that I can customize the organization structure and processing options to match my workflow.

#### Acceptance Criteria

1. WHEN I run the ingest command THEN the system SHALL support multiple config file locations: specified via `--config`, `./ingest.yaml`, or `~/.photo-ingest.yaml`
2. WHEN reading the config file THEN the system SHALL parse archive_root, camera mappings, file_types, LLM settings, and dedupe_store path with type validation
3. WHEN config contains environment variables (e.g., `${HOME}/Photos`) THEN the system SHALL substitute them with actual values
4. IF the config file is missing or invalid THEN the system SHALL display a clear, helpful error message with specific validation failures and exit
5. WHEN config specifies camera mappings THEN the system SHALL use short codes (e.g., "Z6II") instead of full camera names in folder structure
6. WHEN config specifies file types THEN the system SHALL only process files matching the configured extensions

### Requirement 3: Ingest Command File Organization

**User Story:** As a photographer, I want my photos automatically organized into a date and event-based folder structure, so that I can easily locate photos later.

#### Acceptance Criteria

1. WHEN I run `python photo_ingest.py ingest --config ingest.yaml --source /path --event "EventName"` THEN the system SHALL organize files into `{archive_root}/YYYY/YYYY-MM-DD_EventName/CameraCode/` structure
2. WHEN processing files THEN the system SHALL extract date information from EXIF data to determine the YYYY and MM-DD components
3. WHEN multiple camera types are present THEN the system SHALL create separate subdirectories for each camera code
4. WHEN I specify `--copy` flag THEN the system SHALL copy files to the destination
5. WHEN I specify `--move` flag THEN the system SHALL move files to the destination
6. IF neither --copy nor --move is specified THEN the system SHALL default to copy operation

### Requirement 4: Enhanced Deduplication System

**User Story:** As a photographer, I want duplicate and similar files to be detected and skipped during ingest, so that I don't waste storage space or create confusion with duplicate images.

#### Acceptance Criteria

1. WHEN processing files THEN the system SHALL calculate SHA-256 hash and store file size for each file
2. WHEN a file hash matches an existing entry in the SQLite database THEN the system SHALL mark the file as [DUPLICATE] and skip processing
3. WHEN processing image files THEN the system SHALL also calculate perceptual hash to detect similar images with different compression
4. WHEN a perceptual hash matches within similarity threshold THEN the system SHALL mark the file as [SIMILAR] and allow user configuration for handling
5. WHEN a file is successfully ingested THEN the system SHALL store its hash, perceptual hash, size, and path in the SQLite dedupe database
6. WHEN the dedupe database doesn't exist THEN the system SHALL create it automatically with proper schema
7. WHEN running in dry-run mode THEN the system SHALL NOT write to the dedupe database

### Requirement 5: Dry Run Functionality

**User Story:** As a photographer, I want to preview what the ingest operation will do before actually moving files, so that I can verify the organization plan before committing to it.

#### Acceptance Criteria

1. WHEN I specify the `--dry-run` flag THEN the system SHALL display the proposed file organization plan without moving any files
2. WHEN in dry-run mode THEN the system SHALL show source and destination paths for each file
3. WHEN in dry-run mode THEN the system SHALL indicate whether each file is [NEW] or [DUPLICATE]
4. WHEN in dry-run mode THEN the system SHALL show what manifest.csv and Summary.md files would be created
5. WHEN in dry-run mode THEN the system SHALL display the LLM prompt that would be sent but not actually call the LLM service

### Requirement 6: Output File Generation

**User Story:** As a photographer, I want detailed records of what was ingested, so that I can track and audit my photo organization process.

#### Acceptance Criteria

1. WHEN ingest operation completes successfully THEN the system SHALL create a manifest.csv file with columns for source path, destination path, hash, and key EXIF data
2. WHEN ingest operation completes successfully THEN the system SHALL create a Summary.md file containing file counts, date ranges, lens information, and ISO ranges
3. WHEN LLM integration is enabled THEN the system SHALL create a Notes.md file with AI-generated summary content
4. WHEN files are created THEN the system SHALL place them in the event's root directory (not in camera subdirectories)
5. IF file creation fails THEN the system SHALL log the error but continue processing other files

### Requirement 7: LLM Integration with Extensibility

**User Story:** As a photographer, I want AI-generated notes about my photo sessions, so that I can have contextual summaries of my shoots without manual note-taking.

#### Acceptance Criteria

1. WHEN LLM integration is enabled in config THEN the system SHALL build a prompt from EXIF statistics using configurable prompt templates
2. WHEN calling the LLM service THEN the system SHALL use the configured Ollama endpoint and model with retry logic and exponential backoff
3. WHEN LLM call succeeds THEN the system SHALL write the response to Notes.md in the event directory
4. IF LLM call fails after retries THEN the system SHALL log the error and continue without creating Notes.md
5. WHEN LLM integration is disabled in config THEN the system SHALL skip LLM calls entirely
6. WHEN config specifies prompt templates THEN the system SHALL use custom templates instead of default prompts
7. WHEN implementing LLM integration THEN the system SHALL use an extensible architecture that allows future support for additional providers

### Requirement 8: Error Handling and Reliability

**User Story:** As a photographer, I want the tool to handle errors gracefully and provide clear feedback, so that I can troubleshoot issues and trust the tool with my valuable photos.

#### Acceptance Criteria

1. WHEN exiftool is not available THEN the system SHALL display a clear error message about the missing dependency
2. WHEN source directory doesn't exist THEN the system SHALL display an error and exit gracefully
3. WHEN archive directory cannot be created THEN the system SHALL display an error with specific details
4. WHEN file copy/move operations fail THEN the system SHALL log the specific error and continue with remaining files
5. WHEN the tool encounters any error THEN the system SHALL provide actionable error messages rather than technical stack traces

### Requirement 9: Enhanced macOS Integration

**User Story:** As a macOS user, I want the tool to work seamlessly on my M1/M3 Mac and integrate with automation tools, so that I can automate photo imports when memory cards are inserted.

#### Acceptance Criteria

1. WHEN running on macOS THEN the system SHALL work with both Intel and Apple Silicon architectures
2. WHEN called by Hazel or Automator THEN the system SHALL execute without requiring interactive input
3. WHEN processing files THEN the system SHALL handle macOS-specific file system features and preserve Spotlight metadata
4. WHEN dealing with external volumes THEN the system SHALL handle volume mounting and unmounting gracefully
5. WHEN processing completes THEN the system SHALL send macOS notifications with summary information
6. WHEN running multiple times on the same source THEN the system SHALL be idempotent and safe to re-run
7. WHEN provided with launch agent configuration THEN the system SHALL support automatic processing via macOS launch agents

### Requirement 10: Enhanced User Experience and Output

**User Story:** As a photographer, I want beautiful, informative output and progress feedback during processing, so that I can easily understand what the tool is doing and review results.

#### Acceptance Criteria

1. WHEN running analyze command THEN the system SHALL display results in formatted tables with colors and proper alignment using Rich library
2. WHEN processing large numbers of files THEN the system SHALL show progress bars for scanning, hashing, and copying operations
3. WHEN running any command THEN the system SHALL support multiple verbosity levels (quiet, normal, verbose, debug)
4. WHEN analyze command completes THEN the system SHALL support both human-readable and JSON output formats
5. WHEN ingest command completes THEN the system SHALL support JSON output option for automation integration
6. WHEN any operation fails THEN the system SHALL provide structured logging with clear, actionable error messages
7. WHEN running in quiet mode THEN the system SHALL only output essential information and errors

### Requirement 11: Performance and Scalability

**User Story:** As a photographer processing large photo sets, I want the tool to handle thousands of files efficiently, so that I can process entire photo shoots without long wait times.

#### Acceptance Criteria

1. WHEN processing large numbers of files THEN the system SHALL use parallel processing for I/O-bound operations like file hashing and EXIF extraction
2. WHEN re-running on previously processed directories THEN the system SHALL implement incremental processing to skip already-processed files
3. WHEN copying files THEN the system SHALL verify copied files match source checksums to ensure data integrity
4. WHEN processing files THEN the system SHALL optimize memory usage to handle large photo sets without excessive RAM consumption
5. WHEN duplicate detection runs THEN the system SHALL use file size as a fast pre-filter before calculating expensive hashes
6. WHEN database operations occur THEN the system SHALL use efficient SQLite queries with proper indexing for fast lookups

### Requirement 12: Robust EXIF Processing

**User Story:** As a photographer, I want reliable EXIF data extraction that works across different camera brands and file formats, so that my photos are properly organized regardless of equipment used.

#### Acceptance Criteria

1. WHEN exiftool is available THEN the system SHALL use PyExifTool wrapper for primary EXIF extraction
2. WHEN exiftool is not available THEN the system SHALL fall back to Pillow for basic EXIF data extraction
3. WHEN EXIF extraction fails for a file THEN the system SHALL log the error and continue processing with available metadata
4. WHEN processing video files THEN the system SHALL extract relevant metadata including creation date and camera information
5. WHEN EXIF data is missing or corrupted THEN the system SHALL use file modification time as fallback for date-based organization
6. WHEN processing RAW files THEN the system SHALL handle manufacturer-specific EXIF variations correctly

### Requirement 13: Multi-Device Auto-Detection and Processing

**User Story:** As a photographer using multiple cameras and devices, I want the tool to automatically detect and organize photos from different devices when I plug in memory cards, so that I can streamline my import workflow without manual device specification.

#### Acceptance Criteria

1. WHEN config specifies device-specific EXIF mappings THEN the system SHALL automatically detect device type from EXIF data and organize files accordingly
2. WHEN a device is configured with specific EXIF identifiers THEN the system SHALL use the configured folder name instead of the raw EXIF camera model
3. WHEN processing files from an unconfigured device THEN the system SHALL use the EXIF camera model name as the folder name
4. WHEN multiple devices are present in the same source directory THEN the system SHALL organize files into separate device-specific subdirectories
5. WHEN device detection fails THEN the system SHALL fall back to using "Unknown" as the device folder name and log the issue
6. WHEN config specifies device priority rules THEN the system SHALL apply priority ordering for device detection when EXIF data is ambiguous

### Requirement 14: Enhanced Performance and Caching

**User Story:** As a photographer processing large photo collections repeatedly, I want the tool to remember previous processing results and skip unchanged files, so that subsequent runs are much faster.

#### Acceptance Criteria

1. WHEN processing files THEN the system SHALL cache EXIF data in the database to avoid re-extraction on subsequent runs
2. WHEN a file's modification time hasn't changed THEN the system SHALL skip re-hashing and use cached hash values
3. WHEN scanning directories THEN the system SHALL cache directory scan results with timestamps for faster re-scanning
4. WHEN database operations occur THEN the system SHALL use batch inserts and transactions for optimal performance
5. WHEN processing large files THEN the system SHALL use memory-mapped file access for faster hashing operations
6. WHEN running incremental processing THEN the system SHALL only process new or modified files since the last run
7. WHEN database grows large THEN the system SHALL maintain optimal performance through proper indexing and query optimization

### Requirement 15: Raw Backup and Organized Import Options

**User Story:** As a photographer, I want the option to create both a raw backup copy and an organized copy of my photos, so that I have an untouched archive for safety while still benefiting from organized file structure.

#### Acceptance Criteria

1. WHEN config specifies raw backup enabled THEN the system SHALL create an unadulterated copy of all source files in a date-timestamped raw backup directory
2. WHEN performing raw backup THEN the system SHALL organize raw files by import timestamp in format `{raw_backup_root}/YYYY-MM-DD_HHMMSS/`
3. WHEN raw backup is enabled THEN the system SHALL preserve original file names, directory structure, and all metadata exactly as found
4. WHEN both raw backup and organized import are enabled THEN the system SHALL perform both operations and track both locations in the manifest
5. WHEN raw backup directory already exists for the same timestamp THEN the system SHALL append a sequence number to avoid conflicts
6. WHEN raw backup fails THEN the system SHALL log the error but continue with organized import if enabled
7. WHEN user specifies `--raw-only` flag THEN the system SHALL perform only raw backup without organized import
8. WHEN user specifies `--organized-only` flag THEN the system SHALL perform only organized import without raw backup

