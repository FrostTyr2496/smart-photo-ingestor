# Implementation Plan

- [x] 1. Set up project structure and core dependencies
  - Create Python project structure with proper package organization
  - Set up pyproject.toml with Click, Rich, Pydantic, PyExifTool, imagehash dependencies
  - Create basic CLI entry point with Click framework
  - _Requirements: 1.1, 2.1_

- [x] 2. Implement configuration system with validation
  - Create Pydantic models for all configuration classes (IngestConfig, DeviceMapping, etc.)
  - Implement configuration loading with multiple file location support
  - Add environment variable substitution functionality
  - Write unit tests for configuration validation and loading
  - _Requirements: 2.1, 2.2, 2.3, 2.4_

- [x] 3. Create database foundation and schema
  - Implement DatabaseManager class with SQLite connection handling
  - Create database schema with all tables (file_records, exif_cache, directory_cache)
  - Add database initialization and migration logic
  - Write unit tests for database operations
  - _Requirements: 4.6, 14.4_

- [x] 4. Build EXIF processing engine with fallback support
  - Implement EXIFProcessor class with PyExifTool integration
  - Add Pillow fallback for basic EXIF extraction
  - Create file system metadata fallback as last resort
  - Add EXIF data caching functionality
  - Write unit tests for EXIF extraction and caching
  - _Requirements: 12.1, 12.2, 12.3, 12.5, 14.1_

- [x] 5. Implement device detection system
  - Create DeviceDetector class for camera/device identification
  - Add EXIF-based device matching with configurable identifiers
  - Implement fallback to direct camera model mapping
  - Add device name sanitization for folder names
  - Write unit tests for device detection scenarios
  - _Requirements: 13.1, 13.2, 13.3, 13.4_

- [x] 6. Create file scanning and discovery functionality
  - Implement directory scanning with file type filtering
  - Add recursive directory traversal for supported file types
  - Create file metadata extraction and basic validation
  - Add progress tracking for large directory scans
  - Write unit tests for file scanning operations
  - _Requirements: 1.1, 11.1_

- [ ] 7. Build deduplication engine with performance optimizations
  - Implement DeduplicationEngine with SHA-256 hashing
  - Add perceptual hashing for image similarity detection
  - Create size-based pre-filtering for performance
  - Add memory-mapped file hashing for large files
  - Implement batch database operations for deduplication records
  - Write unit tests for duplicate detection and hashing
  - _Requirements: 4.1, 4.2, 4.3, 4.5, 11.5, 14.5_

- [ ] 8. Implement enhanced analyze command with optional visual analysis
  - Create AnalyzeCommand class with EXIF summarization logic
  - Add camera, lens, date range, and technical parameter analysis
  - Implement VisualContentAnalyzer with image sampling and downscaling for --peek mode
  - Add ImageProcessor for RAW preview extraction and JPEG downscaling
  - Create VisionLLMClient for vision-capable model integration
  - Add --peek, --exif-only, and --samples flags for flexible analysis modes
  - Implement Rich-based table formatting combining EXIF and visual results
  - Add JSON output option for automation with both EXIF and visual data
  - Write unit tests for analyze command and visual analysis components
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.9, 1.10, 10.1, 10.4_

- [ ] 9. Create raw backup system
  - Implement RawBackupManager for timestamped backups
  - Add original directory structure preservation
  - Create unique backup directory handling with sequence numbers
  - Add file integrity verification for backup operations
  - Write unit tests for raw backup functionality
  - _Requirements: 15.1, 15.2, 15.3, 15.5_

- [ ] 10. Build file operations manager for organized imports
  - Implement FileOperationsManager for copy/move operations
  - Add organized directory structure creation (YYYY/YYYY-MM-DD_Event/Device/)
  - Create file operation planning and execution
  - Add parallel processing for file operations
  - Write unit tests for file organization logic
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 11.1, 11.3_

- [ ] 11. Implement dry-run functionality and operation preview
  - Add dry-run mode to all file operations
  - Create operation plan display with Rich formatting
  - Show duplicate status and destination paths
  - Add preview of manifest and summary files that would be created
  - Write unit tests for dry-run operation planning
  - _Requirements: 5.1, 5.2, 5.3, 5.4_

- [ ] 12. Create output file generation system
  - Implement manifest.csv generation with file operation records
  - Add Summary.md creation with statistics and metadata summaries
  - Create structured data models for output file content
  - Add error handling for file creation failures
  - Write unit tests for output file generation
  - _Requirements: 6.1, 6.2, 6.4, 6.5_

- [ ] 13. Build LLM integration with Ollama support
  - Implement LLMClient with extensible provider architecture
  - Create OllamaProvider for local LLM integration
  - Add configurable prompt templates and retry logic
  - Implement Notes.md generation from EXIF statistics
  - Write unit tests for LLM integration (with mocking)
  - _Requirements: 7.1, 7.2, 7.3, 7.6, 7.7_

- [ ] 14. Implement ingest command with operation modes
  - Create IngestCommand class orchestrating all ingest operations
  - Add support for --raw-only, --organized-only, and combined modes
  - Integrate all components (EXIF, dedup, file ops, device detection)
  - Add comprehensive error handling and recovery
  - Write unit tests for ingest command orchestration
  - _Requirements: 15.7, 15.8, 8.4_

- [ ] 15. Add performance optimizations and caching
  - Implement incremental processing with modification time tracking
  - Add directory scan caching to skip unchanged directories
  - Create batch database operations for improved performance
  - Add parallel processing for I/O-bound operations
  - Write performance tests and benchmarks
  - _Requirements: 14.2, 14.3, 14.6, 14.7, 11.2_

- [ ] 16. Create Rich-based output and progress display
  - Implement OutputManager with Rich console integration
  - Add progress bars for long-running operations
  - Create formatted tables for analyze results
  - Add verbosity level support (quiet, normal, verbose, debug)
  - Write unit tests for output formatting
  - _Requirements: 10.1, 10.2, 10.3, 10.6, 10.7_

- [ ] 17. Add comprehensive error handling and logging
  - Implement custom exception classes for different error types
  - Add structured logging with appropriate levels
  - Create user-friendly error messages for common failure scenarios
  - Add graceful degradation for non-critical failures
  - Write unit tests for error handling scenarios
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

- [ ] 18. Implement macOS-specific integrations
  - Add macOS notification support for operation completion
  - Implement Spotlight metadata preservation during file operations
  - Add external volume handling for memory card workflows
  - Create launch agent template for automation
  - Write integration tests for macOS-specific features
  - _Requirements: 9.3, 9.4, 9.5, 9.7_

- [ ] 19. Create comprehensive test suite
  - Set up pytest framework with fixtures for test data
  - Create integration tests for end-to-end workflows
  - Add performance tests for large file sets
  - Create mock data sets for testing different camera types
  - Add error injection tests for resilience validation
  - _Requirements: All requirements validation_

- [ ] 20. Add CLI enhancements and final integration
  - Implement all CLI options and flags
  - Add command help text and usage examples
  - Create configuration file templates and documentation
  - Add version information and dependency checking
  - Perform final integration testing and bug fixes
  - _Requirements: 1.6, 2.6, 9.2_