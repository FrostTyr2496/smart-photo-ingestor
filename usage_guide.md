# Enhanced Analyze Command - Usage Guide

## 🎉 **Task 8 Complete: Enhanced Analyze Command with Visual Analysis**

The enhanced analyze command has been successfully implemented with all requested features!

## 🚀 **Quick Start**

### **Basic Usage (No Configuration Required)**
```bash
# Basic enhanced analyze - works out of the box
python -m photo_ingest.cli analyze /path/to/photos

# EXIF-only mode (fastest)
python -m photo_ingest.cli analyze /path/to/photos --exif-only

# JSON output for automation
python -m photo_ingest.cli analyze /path/to/photos --json

# Detailed analysis with more EXIF data
python -m photo_ingest.cli analyze /path/to/photos --detailed
```

## 👁️ **Visual Analysis Mode (Requires Setup)**

### **Prerequisites for Visual Analysis**
1. **Install Ollama** (macOS):
   ```bash
   # Option 1: Download from https://ollama.ai
   # Option 2: Use Homebrew
   brew install ollama
   
   # Start Ollama
   ollama serve
   
   # Pull required models (in another terminal)
   ollama pull llama3.1:8b    # Text generation
   ollama pull llava:7b       # Vision analysis
   ```

2. **Create Configuration File**:
   ```bash
   # Use the provided example
   cp example_config.yaml ingest.yaml
   # Edit ingest.yaml to match your setup
   ```

### **Visual Analysis Commands**
```bash
# Enable visual analysis with config
python -m photo_ingest.cli analyze /path/to/photos --config ingest.yaml --peek

# Custom sample count
python -m photo_ingest.cli analyze /path/to/photos --config ingest.yaml --peek --samples 10

# Visual analysis with JSON output
python -m photo_ingest.cli analyze /path/to/photos --config ingest.yaml --peek --json

# Verbose mode to see Ollama request details
python -m photo_ingest.cli -v analyze /path/to/photos --config ingest.yaml --peek

# Extra verbose for debugging
python -m photo_ingest.cli -vv analyze /path/to/photos --config ingest.yaml --peek

# Disable folder-by-folder analysis (device analysis only)
python -m photo_ingest.cli analyze /path/to/photos --config ingest.yaml --peek --no-folder-analysis

# Disable GPS location resolution
python -m photo_ingest.cli analyze /path/to/photos --config ingest.yaml --peek --no-location
```

## 📊 **Enhanced Features**

### **1. Rich-Formatted Output**
- Beautiful tables with camera and lens information
- Device-specific breakdowns
- Folder-by-folder analysis tables
- Technical parameter analysis (aperture, ISO, focal length ranges)
- Processing statistics and timing

### **2. Device Detection & Breakdown**
- Automatic camera/device detection from EXIF data
- Per-device file counts and statistics
- Configurable device mappings

### **3. Folder-by-Folder Analysis** 🆕
- Analyzes photos organized by folder structure
- Individual folder summaries with content themes
- Overall folder organization summary
- Combines with device analysis for comprehensive view
- Configurable with `--folder-analysis` / `--no-folder-analysis`

### **4. GPS Location Integration** 🆕
- Automatic GPS coordinate extraction from EXIF data
- Reverse geocoding to resolve place names
- Location-aware visual analysis prompts
- Geographic context in device and folder summaries
- Configurable with `--location` / `--no-location`

### **5. Visual Content Analysis**
- LLM-powered image content description
- Smart sampling (configurable samples per device and folder)
- Image downscaling for efficient processing
- Location-enhanced analysis prompts
- Device-specific, folder-specific, and overall visual summaries
- Hierarchical analysis: folders → devices → locations → overall

### **5. Flexible Processing Modes**
- `--exif-only`: Fast EXIF-only analysis
- `--peek`: Enable visual analysis (device + folder analysis)
- `--detailed`: Comprehensive EXIF extraction
- `--json`: Machine-readable output with full hierarchy

### **5. JSON Output for Automation**
```json
{
  "exif_analysis": {
    "total_files": 150,
    "cameras": {"Canon EOS R5": 75, "Nikon Z6": 75},
    "lenses": {"RF 24-70mm": 50, "Z 50mm": 45},
    "date_range": ["2024-01-01", "2024-01-31"]
  },
  "visual_analysis": {
    "overall_summary": "Comprehensive photography collection...",
    "device_analyses": {...},
    "folder_analyses": {
      "/path/to/Vacation": {
        "folder_path": "/path/to/Vacation",
        "file_count": 45,
        "folder_summary": "Beach and sunset photography..."
      },
      "/path/to/Portraits": {
        "folder_path": "/path/to/Portraits", 
        "file_count": 30,
        "folder_summary": "Professional portrait session...",
        "visual_analyses": [
          {
            "file_path": "/path/to/Portraits/portrait1.jpg",
            "visual_description": "Professional headshot in studio setting...",
            "confidence": 0.85,
            "location_info": {
              "latitude": 40.7589,
              "longitude": -73.9851,
              "city": "New York",
              "country": "United States",
              "formatted_address": "New York, NY, United States"
            }
          }
        ]
      }
    },
    "folder_summary": "Well-organized collection with distinct themes..."
  },
  "processing_mode": "peek",
  "total_processing_time": 45.2
}
```

## 🛠️ **Programmatic Usage**

```python
from photo_ingest.analyze_command import AnalyzeCommand
from photo_ingest.config import ConfigManager

# Load configuration (optional)
config = ConfigManager.load_config("ingest.yaml")

# Create enhanced analyze command
command = AnalyzeCommand(config=config)

# Run analysis
result = command.analyze_directory(
    Path("/path/to/photos"),
    peek_mode=True,      # Enable visual analysis
    samples=5            # Sample 5 images per device
)

# Access results
print(f"Total files: {result.exif_analysis.total_files}")
print(f"Cameras: {result.exif_analysis.cameras}")
if result.visual_analysis:
    print(f"Visual summary: {result.visual_analysis.overall_summary}")
```

## 📋 **All Task Requirements Implemented**

✅ **AnalyzeCommand class** with EXIF summarization logic  
✅ **Camera, lens, date range, and technical parameter analysis**  
✅ **VisualContentAnalyzer** with image sampling and downscaling for --peek mode  
✅ **ImageProcessor** for RAW preview extraction and JPEG downscaling  
✅ **VisionLLMClient** for vision-capable model integration  
✅ **--peek, --exif-only, and --samples flags** for flexible analysis modes  
✅ **Rich-based table formatting** combining EXIF and visual results  
✅ **JSON output option** for automation with both EXIF and visual data  
✅ **Unit tests** for analyze command and visual analysis components  

## 🎯 **Example Output**

```
📁 Analyzing folder: /Users/photographer/Photos/Vacation2024
👁️  Visual analysis enabled (sampling 5 images per device)
🔍 Using detailed EXIF analysis

╭─────────────────────────────────────── 📊 Analysis Summary ───────────────────────────────────────╮
│ 📁 Total Files: 247                                                                               │
│ 📸 Images Analyzed: 235                                                                           │
│ 💾 Total Size: 2.1 GB                                                                             │
│ ⏱️  Scan Time: 0.45s                                                                               │
│ 🔍 EXIF Time: 12.3s                                                                               │
│ 📅 Date Range: 2024-07-15 to 2024-07-22                                                          │
╰───────────────────────────────────────────────────────────────────────────────────────────────────╯

      📷 Camera Equipment       
                                
  Camera              Files     
 ─────────────────────────────── 
  Canon EOS R5           145    
  iPhone 15 Pro           89    
  DJI Air 2S              13    

╭─────────────────────────────────── 👁️  Overall Visual Analysis ──────────────────────────────────╮
│ 🎨 Comprehensive Analysis:                                                                     │
│ This vacation photography collection showcases a well-organized multi-day trip with distinct │
│ themes per location. The Canon R5 handled technical photography while mobile devices         │
│ captured spontaneous moments. The folder structure reflects thoughtful organization by        │
│ activity and location, creating a comprehensive visual narrative of the entire experience.   │
╰───────────────────────────────────────────────────────────────────────────────────────────────╯

           📁 Folder Analysis            
                                        
  Folder         Files  Sampled  Summary 
 ──────────────────────────────────────── 
  Beach_Day         45       5   Coastal photography with sunset and water activities...
  City_Explore      89       5   Urban architecture and street photography...
  Mountain_Hike     67       5   Nature and landscape photography from hiking...
  Drone_Shots       46       5   Aerial perspectives of coastline and mountains...

╭─────────────────────────────────── 🗂️  Folder Analysis ────────────────────────────────────────╮
│ 📁 Folder Organization Summary:                                                               │
│ The collection is thoughtfully organized into activity-based folders, each with distinct    │
│ photographic themes. Beach_Day focuses on coastal scenes, City_Explore captures urban       │
│ architecture, Mountain_Hike documents nature photography, and Drone_Shots provides aerial   │
│ perspectives that complement the ground-level photography throughout the trip.               │
╰───────────────────────────────────────────────────────────────────────────────────────────────╯

╭─────────────────────────────────── ⚙️ Processing Summary ──────────────────────────────────────╮
│ 🔍 Processing Mode: EXIF + Visual Analysis (Peek Mode)                                        │
│ ⏱️  Total Processing Time: 45.2s                                                               │
│ 👁️  Visual Analysis: 35 images sampled, 28 LLM calls (device + folder analysis)              │
╰───────────────────────────────────────────────────────────────────────────────────────────────╯
```

## 🧪 **Testing**

Run the test suite to verify functionality:
```bash
# Test basic functionality
python simple_test.py

# Test with dummy EXIF data (requires piexif)
python scripts/test_without_llm.py

# Demo all features
python demo_enhanced_analyze.py
```

## 🔍 **Debugging Ollama Issues**

### **Timeout Settings**
- **Text generation**: 60 seconds
- **Vision analysis**: 120 seconds (2 minutes)
- **Reasonable for 1-2MP photos**: 10-30 seconds after initial model load

### **Verbose Logging**
Use `-v` or `-vv` flags to see detailed Ollama request information:
```bash
# See Ollama request details
python -m photo_ingest.cli -v analyze /path/to/photos --peek

# Extra verbose with timing information
python -m photo_ingest.cli -vv analyze /path/to/photos --peek
```

### **Common Issues & Solutions**
1. **Ollama not responding**: Make sure `ollama serve` is running
2. **Model not found**: Run `ollama pull llava:7b` and `ollama pull llama3.1:8b`
3. **Timeout errors**: Check system resources (8GB+ RAM recommended)
4. **Slow responses**: First request loads the model (30-60s), subsequent requests are faster

### **Test Ollama Setup**
```bash
# Test Ollama integration with verbose logging
python scripts/test_verbose_ollama.py
```

## 🎊 **Success!**

The enhanced analyze command is now fully functional with all requested features, including verbose logging for debugging Ollama integration. It provides both traditional EXIF analysis and cutting-edge visual content analysis, making it a powerful tool for photography workflow management and analysis.