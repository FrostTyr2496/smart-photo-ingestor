# Photo Ingest Tool - Setup Guide for Kiro IDE

## 🎯 **Project Overview**

This is an advanced photo analysis and organization tool with AI-powered visual content analysis, GPS location integration, and comprehensive EXIF data processing. The tool provides both traditional metadata analysis and cutting-edge LLM-based visual content understanding.

## 🚀 **Quick Start**

### **1. Environment Setup**

```bash
# Clone or sync the repository
git clone <repository-url>
cd photo-ingest-tool

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -e .
```

### **2. Install Ollama (for AI Visual Analysis)**

**macOS:**
```bash
# Option 1: Homebrew
brew install ollama

# Option 2: Direct download from https://ollama.ai

# Start Ollama server
ollama serve

# Pull required models (in another terminal)
ollama pull llama3.1:8b    # Text generation
ollama pull llava:7b       # Vision analysis
```

**Linux:**
```bash
curl -fsSL https://ollama.ai/install.sh | sh
ollama serve
ollama pull llama3.1:8b
ollama pull llava:7b
```

### **3. Configuration Setup**

```bash
# Set up default configuration
python -m photo_ingest.cli config set example_config.yaml

# Verify setup
python -m photo_ingest.cli config show
```

## 📊 **Core Features**

### **1. Enhanced EXIF Analysis**
- Comprehensive metadata extraction
- Camera and lens detection
- Technical parameter analysis (aperture, ISO, focal length)
- Date range analysis
- Device-specific breakdowns

### **2. AI-Powered Visual Analysis** 🆕
- LLM-based image content description
- Smart sampling per device and folder
- Context-aware analysis prompts
- Confidence scoring

### **3. GPS Location Integration** 🆕
- Automatic GPS coordinate extraction from EXIF
- Reverse geocoding to place names
- Location-aware visual analysis prompts
- Geographic context in summaries

### **4. Folder-by-Folder Analysis** 🆕
- Hierarchical photo organization analysis
- Individual folder content summaries
- Overall collection organization insights
- Combined device + folder + location analysis

### **5. Rich Output Formats**
- Beautiful Rich-formatted tables and panels
- JSON export for automation
- Verbose logging for debugging
- Configurable analysis modes

## 🎮 **Usage Examples**

### **Basic Analysis**
```bash
# Quick EXIF analysis (no AI required)
python -m photo_ingest.cli analyze /path/to/photos --exif-only

# Full analysis with AI visual content + GPS locations
python -m photo_ingest.cli analyze /path/to/photos --peek

# Verbose mode to see detailed processing
python -m photo_ingest.cli -v analyze /path/to/photos --peek
```

### **Advanced Options**
```bash
# Custom sampling and folder analysis
python -m photo_ingest.cli analyze /path/to/photos --peek --samples 10 --folder-analysis

# Disable GPS location resolution
python -m photo_ingest.cli analyze /path/to/photos --peek --no-location

# JSON output for automation
python -m photo_ingest.cli analyze /path/to/photos --peek --json > analysis.json

# Device analysis only (skip folder analysis)
python -m photo_ingest.cli analyze /path/to/photos --peek --no-folder-analysis
```

### **Configuration Management**
```bash
# Set default config (only need to do once)
python -m photo_ingest.cli config set my_config.yaml

# Show current configuration
python -m photo_ingest.cli config show

# Create example config file
python -m photo_ingest.cli config create my_config.yaml

# Clear saved config
python -m photo_ingest.cli config clear
```

## 🧪 **Testing & Verification**

### **Test Basic Functionality**
```bash
# Test core functionality without AI
python simple_test.py

# Test configuration persistence
python test_config_persistence.py

# Test folder analysis features
python test_folder_analysis.py
```

### **Test AI Integration**
```bash
# Test Ollama connection and verbose logging
python test_verbose_ollama.py

# Demo all features with sample data
python demo_enhanced_analyze.py
python demo_folder_analysis.py
```

### **Test with Real Photos**
```bash
# Test with your photo collection
python -m photo_ingest.cli -v analyze "/path/to/your/photos" --peek --samples 3
```

## 📁 **Project Structure**

```
photo-ingest-tool/
├── photo_ingest/           # Main package
│   ├── cli.py              # Command-line interface
│   ├── analyze_command.py  # Enhanced analyze command
│   ├── visual_analyzer.py  # AI visual content analysis
│   ├── location_service.py # GPS location resolution
│   ├── llm_client.py       # LLM integration (Ollama)
│   ├── analyzer.py         # EXIF analysis engine
│   ├── output_manager.py   # Rich output formatting
│   ├── settings.py         # Configuration persistence
│   └── config.py           # Configuration management
├── tests/                  # Unit tests
├── example_config.yaml     # Example configuration
├── usage_guide.md         # Comprehensive usage guide
└── SETUP_GUIDE.md         # This file
```

## ⚙️ **Configuration Options**

### **Example Configuration (example_config.yaml)**
```yaml
# Archive root directory
archive_root: "${HOME}/Photos/Archive"

# LLM Configuration for AI Analysis
llm:
  enabled: true
  provider: "ollama"
  endpoint: "http://localhost:11434/api/generate"
  model: "llama3.1:8b"
  vision_model: "llava:7b"
  retry_attempts: 3
  retry_delay: 1.0

# Visual Analysis Configuration
peek:
  enabled: true
  include_exif_summary: true
  include_visual_analysis: true
  sample_count: 5
  max_resolution: 1024
  supported_formats: ["jpg", "jpeg", "heic", "nef", "cr3", "dng"]
  vision_prompt: |
    Analyze this photo and describe:
    1. Main subject/content
    2. Photography style (portrait, landscape, macro, etc.)
    3. Setting/location if identifiable
    4. Notable visual elements
    Keep response concise, 2-3 sentences.

# Device Detection (optional)
devices:
  mappings:
    "NIKON Z 6": "Z6"
    "Canon EOS R5": "R5"
    "iPhone 15 Pro": "iPhone"
  exif_identifiers:
    "Z6":
      Make: "NIKON CORPORATION"
      Model: "NIKON Z 6"

# Performance Settings
performance:
  parallel_workers: 4
  batch_size: 100
  cache_exif: true
```

## 🔧 **Troubleshooting**

### **Common Issues**

1. **"No such option: -v"**
   - Use: `python -m photo_ingest.cli -v analyze ...` (verbose flag before subcommand)

2. **"Ollama not responding"**
   - Ensure Ollama is running: `ollama serve`
   - Check models are installed: `ollama list`

3. **"Config file not found"**
   - Set default config: `python -m photo_ingest.cli config set example_config.yaml`
   - Or use explicit path: `--config /path/to/config.yaml`

4. **GPS location not working**
   - Check internet connection (uses OpenStreetMap API)
   - Disable if needed: `--no-location`

### **Performance Tips**

- **First AI request**: Takes 30-60s to load models
- **Subsequent requests**: Much faster (10-30s per image)
- **Large collections**: Use `--samples 3` to reduce processing time
- **Memory usage**: 8GB+ RAM recommended for AI features
- **Network**: GPS resolution requires internet connection

## 🎯 **Key Features Summary**

✅ **Enhanced EXIF Analysis** - Comprehensive metadata extraction  
✅ **AI Visual Content Analysis** - LLM-powered image understanding  
✅ **GPS Location Integration** - Automatic place name resolution  
✅ **Folder-by-Folder Analysis** - Hierarchical organization insights  
✅ **Rich Output Formatting** - Beautiful tables and summaries  
✅ **Configuration Persistence** - Set once, use everywhere  
✅ **JSON Export** - Automation-friendly output  
✅ **Verbose Logging** - Detailed debugging information  
✅ **Flexible Processing Modes** - EXIF-only, peek, custom sampling  

## 🚀 **Ready to Use!**

The tool is now fully functional with all advanced features. Start with:

```bash
# Set up configuration
python -m photo_ingest.cli config set example_config.yaml

# Analyze your photos with full AI + GPS features
python -m photo_ingest.cli -v analyze "/path/to/photos" --peek
```

For detailed usage examples and advanced features, see `usage_guide.md`.

---

**Version**: Prototype v1.0 with AI Visual Analysis + GPS Location Integration  
**Last Updated**: January 2025  
**Status**: Fully Functional Prototype