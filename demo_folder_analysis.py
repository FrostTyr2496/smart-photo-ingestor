#!/usr/bin/env python3
"""Demo script showcasing the new folder analysis feature."""

import tempfile
import shutil
from pathlib import Path
from PIL import Image
import subprocess
import sys

def create_realistic_photo_structure():
    """Create a realistic photo folder structure for demonstration."""
    temp_dir = tempfile.mkdtemp(prefix="photo_demo_")
    temp_path = Path(temp_dir)
    
    print(f"📁 Creating demo photo collection: {temp_path}")
    
    # Create a realistic folder structure
    photo_structure = {
        "2024_Summer_Vacation": {
            "Beach_Day": ["sunrise.jpg", "beach_volleyball.jpg", "sunset_waves.jpg", "sandcastle.jpg"],
            "City_Tour": ["cathedral.jpg", "street_art.jpg", "market_scene.jpg", "night_lights.jpg", "cafe.jpg"],
            "Mountain_Hike": ["trail_start.jpg", "summit_view.jpg", "wildflowers.jpg", "group_photo.jpg"]
        },
        "Family_Events": {
            "Birthday_Party": ["cake_cutting.jpg", "group_celebration.jpg", "kids_playing.jpg"],
            "Wedding": ["ceremony.jpg", "first_dance.jpg", "reception.jpg", "bouquet.jpg", "rings.jpg"]
        },
        "Nature_Photography": {
            "Wildlife": ["deer_family.jpg", "bird_flight.jpg", "butterfly.jpg"],
            "Landscapes": ["mountain_range.jpg", "forest_path.jpg", "lake_reflection.jpg", "storm_clouds.jpg"]
        },
        "Street_Photography": ["urban_life.jpg", "architecture.jpg", "people_walking.jpg", "neon_signs.jpg"]
    }
    
    total_files = 0
    
    for main_folder, contents in photo_structure.items():
        main_path = temp_path / main_folder
        main_path.mkdir()
        
        if isinstance(contents, dict):
            # Nested folder structure
            for sub_folder, files in contents.items():
                sub_path = main_path / sub_folder
                sub_path.mkdir()
                
                for filename in files:
                    # Create varied image sizes to simulate different cameras
                    if "drone" in filename.lower() or "aerial" in filename.lower():
                        size = (4000, 3000)  # High-res drone
                        color = 'skyblue'
                    elif "portrait" in filename.lower() or "wedding" in filename.lower():
                        size = (3000, 4000)  # Portrait orientation
                        color = 'lightpink'
                    elif "landscape" in filename.lower() or "mountain" in filename.lower():
                        size = (4000, 2667)  # Landscape ratio
                        color = 'lightgreen'
                    else:
                        size = (2000, 1500)  # Standard size
                        color = 'lightblue'
                    
                    img = Image.new('RGB', size, color=color)
                    img_path = sub_path / filename
                    img.save(img_path, "JPEG", quality=85)
                    total_files += 1
                
                print(f"  📂 {main_folder}/{sub_folder}: {len(files)} photos")
        else:
            # Direct files in main folder
            for filename in contents:
                img = Image.new('RGB', (2000, 1500), color='lightgray')
                img_path = main_path / filename
                img.save(img_path, "JPEG", quality=85)
                total_files += 1
            
            print(f"  📂 {main_folder}: {len(contents)} photos")
    
    print(f"✅ Created {total_files} demo photos in realistic folder structure")
    return temp_path

def demo_folder_analysis_modes():
    """Demonstrate different folder analysis modes."""
    print("\n🎬 Folder Analysis Feature Demo")
    print("=" * 50)
    
    demo_dir = create_realistic_photo_structure()
    
    try:
        demos = [
            {
                "title": "📊 Basic Analysis (EXIF Only)",
                "command": [sys.executable, "-m", "photo_ingest.cli", "analyze", str(demo_dir), "--exif-only"],
                "description": "Standard EXIF analysis without visual content"
            },
            {
                "title": "🗂️  Folder Analysis Structure",
                "command": [sys.executable, "-m", "photo_ingest.cli", "analyze", str(demo_dir), "--exif-only", "--folder-analysis"],
                "description": "Shows folder organization (without LLM visual analysis)"
            },
            {
                "title": "📄 JSON Output with Folder Data",
                "command": [sys.executable, "-m", "photo_ingest.cli", "analyze", str(demo_dir), "--exif-only", "--json"],
                "description": "Machine-readable output including folder structure"
            }
        ]
        
        for i, demo in enumerate(demos, 1):
            print(f"\n{i}. {demo['title']}")
            print("-" * 40)
            print(f"Description: {demo['description']}")
            print(f"Command: {' '.join(demo['command'])}")
            print("\nOutput:")
            
            try:
                result = subprocess.run(demo['command'], capture_output=True, text=True, timeout=30)
                
                if result.returncode == 0:
                    output_lines = result.stdout.split('\n')
                    
                    # Show relevant parts of output
                    if "--json" in demo['command']:
                        # For JSON, show first part and folder analysis section
                        print("📄 JSON Output (truncated):")
                        for line in output_lines[:10]:
                            if line.strip():
                                print(f"  {line}")
                        print("  ... (truncated)")
                        
                        # Look for folder analysis in JSON
                        json_content = '\n'.join(output_lines)
                        if 'folder_analyses' in json_content:
                            print("✅ Folder analysis data included in JSON")
                    else:
                        # Show key sections
                        in_relevant_section = False
                        shown_lines = 0
                        max_lines = 20
                        
                        for line in output_lines:
                            if any(keyword in line for keyword in ['Analysis Summary', 'Folder Analysis', 'Processing Summary']):
                                in_relevant_section = True
                            elif line.strip() == '' and in_relevant_section:
                                in_relevant_section = False
                            
                            if in_relevant_section or any(keyword in line for keyword in ['📁', '📂', '🗂️', '⚙️']):
                                print(f"  {line}")
                                shown_lines += 1
                                if shown_lines >= max_lines:
                                    print("  ... (output truncated)")
                                    break
                    
                    print("✅ Demo completed successfully")
                else:
                    print(f"❌ Command failed: {result.stderr}")
                    
            except subprocess.TimeoutExpired:
                print("⏱️  Command timed out")
            except Exception as e:
                print(f"❌ Error running demo: {e}")
        
        return True
        
    finally:
        shutil.rmtree(demo_dir, ignore_errors=True)
        print(f"\n🧹 Cleaned up demo directory")

def show_folder_analysis_features():
    """Show the key features of folder analysis."""
    print("\n🌟 Folder Analysis Features")
    print("=" * 35)
    
    features = [
        "📁 **Hierarchical Analysis**: Analyzes photos by folder structure",
        "🎯 **Individual Folder Summaries**: Each folder gets its own content summary",
        "🗂️  **Organization Overview**: Overall summary of how photos are organized",
        "📊 **Rich Display**: Beautiful tables showing folder breakdowns",
        "🔄 **Combined Analysis**: Works alongside device analysis for complete picture",
        "📄 **JSON Export**: Full folder data available in machine-readable format",
        "⚙️  **Configurable**: Enable/disable with --folder-analysis flags",
        "🎨 **Visual Content**: When combined with --peek, analyzes visual themes per folder"
    ]
    
    for feature in features:
        print(f"  {feature}")
    
    print(f"\n💡 **Use Cases:**")
    use_cases = [
        "📸 **Event Photography**: Analyze wedding, party, or vacation photo collections",
        "🗃️  **Archive Organization**: Understand how your photo library is structured",
        "📈 **Collection Analysis**: Get insights into shooting patterns by location/event",
        "🤖 **Automation**: Use JSON output to build photo management workflows",
        "📋 **Reporting**: Generate summaries of large photo collections"
    ]
    
    for use_case in use_cases:
        print(f"  {use_case}")

def show_usage_examples():
    """Show practical usage examples."""
    print(f"\n🚀 Practical Usage Examples")
    print("=" * 35)
    
    examples = [
        {
            "scenario": "Analyze vacation photos with visual content",
            "command": "python -m photo_ingest.cli -v analyze /Photos/Vacation2024 --peek"
        },
        {
            "scenario": "Quick folder structure overview (no LLM needed)",
            "command": "python -m photo_ingest.cli analyze /Photos/Events --exif-only"
        },
        {
            "scenario": "Export complete analysis to JSON for automation",
            "command": "python -m photo_ingest.cli analyze /Photos --peek --json > analysis.json"
        },
        {
            "scenario": "Device analysis only (skip folder analysis)",
            "command": "python -m photo_ingest.cli analyze /Photos --peek --no-folder-analysis"
        },
        {
            "scenario": "Custom sampling with folder analysis",
            "command": "python -m photo_ingest.cli analyze /Photos --peek --samples 3"
        }
    ]
    
    for i, example in enumerate(examples, 1):
        print(f"\n{i}. **{example['scenario']}**")
        print(f"   `{example['command']}`")

def main():
    """Run the folder analysis demo."""
    print("🗂️  Enhanced Photo Analysis - Folder Analysis Demo")
    print("=" * 60)
    
    try:
        # Run the main demo
        demo_folder_analysis_modes()
        
        # Show features and usage
        show_folder_analysis_features()
        show_usage_examples()
        
        print("\n" + "=" * 60)
        print("🎉 Folder Analysis Demo Complete!")
        print("\n📋 **What's New:**")
        print("✅ Folder-by-folder visual analysis")
        print("✅ Individual folder content summaries")
        print("✅ Overall folder organization insights")
        print("✅ Combined device + folder analysis")
        print("✅ Rich table displays for folder data")
        print("✅ Complete JSON export with folder hierarchy")
        
        print(f"\n🎯 **Ready to analyze your photo collection!**")
        print("Try: `python -m photo_ingest.cli -v analyze /path/to/photos --peek`")
        
        return 0
        
    except KeyboardInterrupt:
        print("\n⏹️  Demo interrupted by user")
        return 1
    except Exception as e:
        print(f"\n❌ Demo failed: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit(main())