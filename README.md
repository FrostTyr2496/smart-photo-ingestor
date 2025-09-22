# Photo Ingest Tool

A command-line Python application designed to organize photography imports on macOS. The tool serves as the first step in a larger photo workflow (Imports → Staging → Catalog/Exports) and provides two main functions:

1. **Analyze**: Extract EXIF metadata summaries and optional visual content analysis
2. **Ingest**: Organize photos into structured archive with deduplication and optional AI-generated notes

## Features

- **Enhanced Analysis**: Analyze photo folders with EXIF metadata extraction and optional visual content analysis using vision-capable LLMs
- **Smart Organization**: Automatically organize photos by date, event, and camera/device
- **Deduplication**: Detect duplicate and similar files using SHA-256 and perceptual hashing
- **Raw Backup**: Create timestamped raw backups preserving original structure
- **macOS Integration**: Native macOS features including notifications and Spotlight metadata preservation
- **Performance Optimized**: Parallel processing, caching, and incremental updates for large photo sets

## Installation

```bash
pip install -e .
```

## Usage

### Analyze Command

```bash
# Basic EXIF analysis
photo-ingest analyze /path/to/photos

# Include visual content analysis
photo-ingest analyze /path/to/photos --peek

# JSON output for automation
photo-ingest analyze /path/to/photos --json
```

### Ingest Command

```bash
# Basic ingest with configuration
photo-ingest ingest --config ingest.yaml --source /path/to/photos --event "Vacation 2024"

# Dry run to preview operations
photo-ingest ingest --config ingest.yaml --source /path/to/photos --event "Vacation 2024" --dry-run

# Raw backup only
photo-ingest ingest --config ingest.yaml --source /path/to/photos --event "Vacation 2024" --raw-only
```

## Requirements

- Python 3.8+
- macOS (optimized for M1/M3 Macs)
- ExifTool (for comprehensive EXIF extraction)

## Development

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Format code
black photo_ingest/
isort photo_ingest/

# Type checking
mypy photo_ingest/
```

## License

MIT License