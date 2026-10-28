"""Output manager for Rich-based formatting and progress display."""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional

try:
    from rich.console import Console
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
    from rich.panel import Panel
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from .analyzer import AnalysisResult
from .analyze_command import EnhancedAnalysisResult
from .file_operations import FileOperation, OperationResults, OperationType
from .deduplication import DuplicateStatus

class OutputManager:
    """Manages all output formatting and progress display."""
    
    def __init__(self, verbosity: int = 0, quiet: bool = False):
        """Initialize output manager.
        
        Args:
            verbosity: Verbosity level (0=normal, 1=verbose, 2=debug)
            quiet: Suppress non-essential output
        """
        self.verbosity = verbosity
        self.quiet = quiet
        
        if RICH_AVAILABLE:
            self.console = Console()
        else:
            self.console = None
    
    def display_analyze_results(self, results: AnalysisResult, json_format: bool = False):
        """Display analyze command results.
        
        Args:
            results: Analysis results
            json_format: Output in JSON format
        """
        if json_format:
            self._display_json_results(results)
        elif RICH_AVAILABLE:
            self._display_rich_analyze_results(results)
        else:
            self._display_plain_analyze_results(results)
    
    def display_enhanced_analyze_results(self, results: EnhancedAnalysisResult, json_format: bool = False):
        """Display enhanced analyze command results.
        
        Args:
            results: Enhanced analysis results
            json_format: Output in JSON format
        """
        if json_format:
            self._display_enhanced_json_results(results)
        elif RICH_AVAILABLE:
            self._display_rich_enhanced_analyze_results(results)
        else:
            self._display_plain_enhanced_analyze_results(results)
    
    def display_ingest_plan(self, operations: List[FileOperation], dry_run: bool = True):
        """Display ingest operation plan.
        
        Args:
            operations: List of planned operations
            dry_run: Whether this is a dry run display
        """
        if RICH_AVAILABLE:
            self._display_rich_ingest_plan(operations, dry_run)
        else:
            self._display_plain_ingest_plan(operations, dry_run)
    
    def display_ingest_results(self, results: OperationResults):
        """Display ingest operation results.
        
        Args:
            results: Operation results
        """
        if RICH_AVAILABLE:
            self._display_rich_ingest_results(results)
        else:
            self._display_plain_ingest_results(results)
    
    def create_progress_bar(self, description: str, total: int) -> Optional['Progress']:
        """Create Rich progress bar.
        
        Args:
            description: Progress description
            total: Total number of items
            
        Returns:
            Progress object if Rich is available, None otherwise
        """
        if not RICH_AVAILABLE or self.quiet:
            return None
        
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=self.console
        )
        
        return progress
    
    def _display_rich_analyze_results(self, results: AnalysisResult):
        """Display analysis results using Rich formatting."""
        # Main summary panel
        summary_text = f"""
📁 Total Files: {results.total_files:,}
📸 Images Analyzed: {results.image_files_analyzed:,}
💾 Total Size: {self._format_size(results.total_size)}
⏱️  Scan Time: {results.scan_time:.2f}s
🔍 EXIF Time: {results.exif_time:.2f}s
        """.strip()
        
        if results.date_range:
            summary_text += f"\n📅 Date Range: {results.date_range[0]} to {results.date_range[1]}"
        
        self.console.print(Panel(summary_text, title="📊 Analysis Summary", box=box.ROUNDED))
        
        # File types table
        if results.files_by_type:
            file_table = Table(title="📁 File Types", box=box.SIMPLE)
            file_table.add_column("Type", style="cyan")
            file_table.add_column("Count", justify="right", style="green")
            file_table.add_column("Size", justify="right", style="blue")
            
            for file_type, count in results.files_by_type.items():
                if count > 0:
                    size = results.size_by_type.get(file_type, 0)
                    file_table.add_row(
                        file_type.upper(),
                        f"{count:,}",
                        self._format_size(size)
                    )
            
            self.console.print(file_table)
        
        # Camera equipment table
        if results.cameras:
            camera_table = Table(title="📷 Camera Equipment", box=box.SIMPLE)
            camera_table.add_column("Camera", style="cyan")
            camera_table.add_column("Files", justify="right", style="green")
            
            for camera, count in sorted(results.cameras.items(), key=lambda x: x[1], reverse=True):
                camera_table.add_row(camera, f"{count:,}")
            
            self.console.print(camera_table)
        
        # Lens information
        if results.lenses:
            lens_table = Table(title="🔍 Lenses Used", box=box.SIMPLE)
            lens_table.add_column("Lens", style="cyan")
            lens_table.add_column("Files", justify="right", style="green")
            
            for lens, count in sorted(results.lenses.items(), key=lambda x: x[1], reverse=True)[:10]:
                lens_table.add_row(lens, f"{count:,}")
            
            self.console.print(lens_table)
        
        # Technical settings summary
        if results.aperture_range or results.iso_range:
            tech_text = ""
            
            if results.aperture_range:
                tech_text += f"📐 Aperture: f/{results.aperture_range[0]:.1f} - f/{results.aperture_range[1]:.1f}\n"
            
            if results.iso_range:
                tech_text += f"🎞️  ISO: {results.iso_range[0]} - {results.iso_range[1]}\n"
            
            if results.focal_length_range:
                tech_text += f"🔭 Focal Length: {results.focal_length_range[0]:.0f}mm - {results.focal_length_range[1]:.0f}mm\n"
            
            if results.files_with_gps > 0:
                tech_text += f"🌍 GPS Data: {results.files_with_gps:,} files\n"
            
            if tech_text:
                self.console.print(Panel(tech_text.strip(), title="⚙️ Technical Summary", box=box.ROUNDED))
        
        # Most used settings
        if self.verbosity > 0 and (results.most_used_apertures or results.most_used_isos):
            settings_table = Table(title="📊 Most Used Settings", box=box.SIMPLE)
            settings_table.add_column("Setting", style="cyan")
            settings_table.add_column("Value", style="yellow")
            settings_table.add_column("Count", justify="right", style="green")
            
            for setting, count in results.most_used_apertures[:5]:
                settings_table.add_row("Aperture", setting, f"{count:,}")
            
            for setting, count in results.most_used_isos[:5]:
                settings_table.add_row("ISO", setting, f"{count:,}")
            
            self.console.print(settings_table)
        
        # Resolution summary
        if results.resolutions:
            resolution_table = Table(title="📐 Image Resolutions", box=box.SIMPLE)
            resolution_table.add_column("Resolution", style="cyan")
            resolution_table.add_column("Files", justify="right", style="green")
            resolution_table.add_column("Percentage", justify="right", style="blue")
            
            total_with_resolution = sum(results.resolutions.values())
            sorted_resolutions = sorted(results.resolutions.items(), key=lambda x: x[1], reverse=True)
            
            # Show top 3 resolutions
            for resolution, count in sorted_resolutions[:3]:
                percentage = (count / total_with_resolution) * 100 if total_with_resolution > 0 else 0
                resolution_table.add_row(
                    resolution,
                    f"{count:,}",
                    f"{percentage:.1f}%"
                )
            
            # Summarize remaining resolutions if there are more than 3
            if len(sorted_resolutions) > 3:
                other_count = sum(count for _, count in sorted_resolutions[3:])
                other_percentage = (other_count / total_with_resolution) * 100 if total_with_resolution > 0 else 0
                other_resolutions = len(sorted_resolutions) - 3
                resolution_table.add_row(
                    f"Other ({other_resolutions} resolutions)",
                    f"{other_count:,}",
                    f"{other_percentage:.1f}%"
                )
            
            self.console.print(resolution_table)
    
    def _display_rich_ingest_plan(self, operations: List[FileOperation], dry_run: bool):
        """Display ingest plan using Rich formatting."""
        title = "🔍 Dry Run Plan" if dry_run else "📋 Ingest Plan"
        
        # Summary statistics
        total_files = len(operations)
        duplicates = sum(1 for op in operations if op.duplicate_status == DuplicateStatus.DUPLICATE)
        new_files = total_files - duplicates
        
        # Group by operation type
        raw_only = sum(1 for op in operations if op.operation_type == OperationType.RAW_BACKUP_ONLY)
        organized_only = sum(1 for op in operations if op.operation_type == OperationType.ORGANIZED_ONLY)
        both = sum(1 for op in operations if op.operation_type == OperationType.BOTH)
        
        summary_text = f"""
📁 Total Files: {total_files:,}
✅ New Files: {new_files:,}
🔄 Duplicates: {duplicates:,}
📦 Raw Backup Only: {raw_only:,}
📂 Organized Only: {organized_only:,}
🔄 Both Operations: {both:,}
        """.strip()
        
        self.console.print(Panel(summary_text, title=title, box=box.ROUNDED))
        
        # Device breakdown
        device_counts = {}
        for op in operations:
            device_counts[op.camera_code] = device_counts.get(op.camera_code, 0) + 1
        
        if device_counts:
            device_table = Table(title="📷 Files by Device", box=box.SIMPLE)
            device_table.add_column("Device", style="cyan")
            device_table.add_column("Files", justify="right", style="green")
            
            for device, count in sorted(device_counts.items(), key=lambda x: x[1], reverse=True):
                device_table.add_row(device, f"{count:,}")
            
            self.console.print(device_table)
        
        # Show sample operations if verbose
        if self.verbosity > 0 and operations:
            sample_table = Table(title="📋 Sample Operations", box=box.SIMPLE)
            sample_table.add_column("Source", style="cyan")
            sample_table.add_column("Destination", style="green")
            sample_table.add_column("Status", style="yellow")
            
            for op in operations[:10]:  # Show first 10
                status = "DUPLICATE" if op.duplicate_status == DuplicateStatus.DUPLICATE else "NEW"
                dest = str(op.dest_path) if op.dest_path else str(op.raw_backup_path)
                sample_table.add_row(
                    str(op.source_path.name),
                    str(Path(dest).name) if dest else "N/A",
                    status
                )
            
            if len(operations) > 10:
                sample_table.add_row("...", f"({len(operations) - 10} more files)", "")
            
            self.console.print(sample_table)
    
    def _display_rich_ingest_results(self, results: OperationResults):
        """Display ingest results using Rich formatting."""
        # Main results
        summary_text = f"""
📁 Files Processed: {results.files_processed:,}
📋 Files Copied: {results.files_copied:,}
🚚 Files Moved: {results.files_moved:,}
⏭️  Files Skipped: {results.files_skipped:,}
🔄 Duplicates Found: {results.duplicates_found:,}
        """.strip()
        
        if results.errors:
            summary_text += f"\n❌ Errors: {len(results.errors)}"
        
        self.console.print(Panel(summary_text, title="✅ Ingest Results", box=box.ROUNDED))
        
        # Raw backup results
        if results.raw_backup_result:
            backup_text = f"""
📦 Backup Directory: {results.raw_backup_result.backup_directory}
📁 Files Backed Up: {results.raw_backup_result.files_backed_up:,}
💾 Total Size: {self._format_size(results.raw_backup_result.total_size)}
⏭️  Files Skipped: {results.raw_backup_result.files_skipped:,}
            """.strip()
            
            if results.raw_backup_result.errors:
                backup_text += f"\n❌ Backup Errors: {len(results.raw_backup_result.errors)}"
            
            self.console.print(Panel(backup_text, title="📦 Raw Backup Results", box=box.ROUNDED))
        
        # Show errors if any
        if results.errors and self.verbosity > 0:
            error_table = Table(title="❌ Errors", box=box.SIMPLE)
            error_table.add_column("Error", style="red")
            
            for error in results.errors[:10]:  # Show first 10 errors
                error_table.add_row(error)
            
            if len(results.errors) > 10:
                error_table.add_row(f"... and {len(results.errors) - 10} more errors")
            
            self.console.print(error_table)
    
    def _display_json_results(self, results: AnalysisResult):
        """Display results in JSON format."""
        json_data = results.to_dict()
        print(json.dumps(json_data, indent=2, default=str))
    
    def _display_enhanced_json_results(self, results: EnhancedAnalysisResult):
        """Display enhanced results in JSON format."""
        json_data = results.to_dict()
        print(json.dumps(json_data, indent=2, default=str))
    
    def _display_rich_enhanced_analyze_results(self, results: EnhancedAnalysisResult):
        """Display enhanced analysis results using Rich formatting."""
        # Display basic EXIF analysis first
        self._display_rich_analyze_results(results.exif_analysis)
        
        # Display device breakdown if available
        if results.device_breakdown:
            self._display_device_breakdown(results.device_breakdown)
        
        # Display visual analysis results if available
        if results.visual_analysis and results.processing_mode in ["peek", "exif_with_visual"]:
            self._display_visual_analysis_results(results.visual_analysis)
        
        # Display processing summary
        self._display_processing_summary(results)
    
    def _display_device_breakdown(self, device_breakdown: Dict[str, Dict[str, Any]]):
        """Display device-specific breakdown."""
        if not device_breakdown:
            return
        
        device_table = Table(title="📱 Device Breakdown", box=box.SIMPLE)
        device_table.add_column("Device", style="cyan")
        device_table.add_column("Files", justify="right", style="green")
        device_table.add_column("Size", justify="right", style="blue")
        device_table.add_column("Date Range", style="yellow")
        device_table.add_column("ISO Range", style="magenta")
        
        for device_code, stats in device_breakdown.items():
            date_range = "N/A"
            if stats['date_range'][0] and stats['date_range'][1]:
                if stats['date_range'][0] == stats['date_range'][1]:
                    date_range = stats['date_range'][0]
                else:
                    date_range = f"{stats['date_range'][0]} to {stats['date_range'][1]}"
            
            iso_range = "N/A"
            if stats['iso_range'][0] is not None and stats['iso_range'][1] is not None:
                if stats['iso_range'][0] == stats['iso_range'][1]:
                    iso_range = f"ISO {stats['iso_range'][0]}"
                else:
                    iso_range = f"ISO {stats['iso_range'][0]}-{stats['iso_range'][1]}"
            
            device_table.add_row(
                device_code,
                f"{stats['file_count']:,}",
                self._format_size(stats['total_size']),
                date_range,
                iso_range
            )
        
        self.console.print(device_table)
    
    def _display_visual_analysis_results(self, visual_results):
        """Display visual analysis results."""
        if not visual_results.device_analyses and not visual_results.folder_analyses:
            return
        
        # Overall visual summary
        if visual_results.overall_summary:
            summary_text = f"🎨 Comprehensive Analysis:\n{visual_results.overall_summary}"
            self.console.print(Panel(summary_text, title="👁️  Overall Visual Analysis", box=box.ROUNDED))
        
        # Folder-based analysis
        if visual_results.folder_analyses:
            self._display_folder_analysis_results(visual_results.folder_analyses)
            
            # Folder summary
            if visual_results.folder_summary:
                folder_text = f"📁 Folder Organization Summary:\n{visual_results.folder_summary}"
                self.console.print(Panel(folder_text, title="🗂️  Folder Analysis", box=box.ROUNDED))
        
        # Device-specific visual analysis
        if visual_results.device_analyses:
            for device_code, analysis in visual_results.device_analyses.items():
                if analysis.visual_analyses:
                    self._display_device_visual_analysis(device_code, analysis)
        
        # Processing statistics
        if visual_results.processing_stats:
            stats = visual_results.processing_stats
            stats_text = f"""
📊 Images Sampled: {stats.images_sampled:,} from {stats.total_files_scanned:,} total files
🤖 LLM Calls: {stats.llm_calls_made:,}
⏱️  Processing Time: {stats.processing_time:.2f}s
❌ Errors: {stats.errors_encountered:,}
            """.strip()
            
            self.console.print(Panel(stats_text, title="📈 Visual Analysis Stats", box=box.ROUNDED))
    
    def _display_folder_analysis_results(self, folder_analyses):
        """Display folder-based analysis results."""
        if not folder_analyses:
            return
        
        # Folder summary table
        folder_table = Table(title="📁 Folder Analysis", box=box.SIMPLE)
        folder_table.add_column("Folder", style="cyan")
        folder_table.add_column("Files", justify="right", style="green")
        folder_table.add_column("Sampled", justify="right", style="blue")
        folder_table.add_column("Summary", style="yellow")
        
        # Sort folders by path for consistent display
        sorted_folders = sorted(folder_analyses.items(), key=lambda x: x[0])
        
        for folder_path, analysis in sorted_folders:
            folder_name = Path(folder_path).name or "Root"
            summary = analysis.folder_summary or "No summary available"
            
            # Truncate long summaries for table display
            if len(summary) > 100:
                summary = summary[:97] + "..."
            
            folder_table.add_row(
                folder_name,
                f"{analysis.file_count:,}",
                f"{len(analysis.sampled_files):,}",
                summary
            )
        
        self.console.print(folder_table)
        
        # Show detailed folder summaries if verbose
        if self.verbosity > 0:
            for folder_path, analysis in sorted_folders[:5]:  # Show first 5 folders
                if analysis.folder_summary and "Failed to generate" not in analysis.folder_summary:
                    folder_name = Path(folder_path).name or "Root"
                    folder_text = f"📁 {folder_name} ({analysis.file_count} files): {analysis.folder_summary}"
                    self.console.print(Panel(folder_text, title=f"🗂️  {folder_name} Details", box=box.SIMPLE))
    
    def _display_device_visual_analysis(self, device_code: str, analysis):
        """Display visual analysis for a specific device."""
        if not analysis.visual_analyses:
            return
        
        # Device summary
        if analysis.device_summary:
            device_text = f"📷 {device_code}: {analysis.device_summary}"
            self.console.print(Panel(device_text, title=f"🎯 {device_code} Visual Summary", box=box.SIMPLE))
        
        # Sample analyses (show only if verbose)
        if self.verbosity > 0:
            sample_table = Table(title=f"🖼️  {device_code} Sample Analyses", box=box.SIMPLE)
            sample_table.add_column("File", style="cyan")
            sample_table.add_column("Description", style="green")
            sample_table.add_column("Confidence", justify="right", style="yellow")
            
            for va in analysis.visual_analyses[:3]:  # Show first 3
                if va.visual_description and not va.error:
                    confidence_str = f"{va.confidence:.1%}" if va.confidence > 0 else "N/A"
                    # Truncate long descriptions
                    description = va.visual_description[:80] + "..." if len(va.visual_description) > 80 else va.visual_description
                    sample_table.add_row(
                        va.file_path.name,
                        description,
                        confidence_str
                    )
            
            if len(analysis.visual_analyses) > 3:
                sample_table.add_row("...", f"({len(analysis.visual_analyses) - 3} more samples)", "")
            
            self.console.print(sample_table)
    
    def _display_processing_summary(self, results: EnhancedAnalysisResult):
        """Display processing summary."""
        mode_descriptions = {
            "exif_only": "EXIF Analysis Only",
            "peek": "EXIF + Visual Analysis (Peek Mode)",
            "exif_with_visual": "EXIF + Visual Analysis (Auto)"
        }
        
        summary_text = f"""
🔍 Processing Mode: {mode_descriptions.get(results.processing_mode, results.processing_mode)}
⏱️  Total Processing Time: {results.total_processing_time:.2f}s
        """.strip()
        
        if results.visual_analysis and results.visual_analysis.processing_stats:
            visual_stats = results.visual_analysis.processing_stats
            summary_text += f"\n👁️  Visual Analysis: {visual_stats.images_sampled} images sampled, {visual_stats.llm_calls_made} LLM calls"
        
        self.console.print(Panel(summary_text, title="⚙️ Processing Summary", box=box.ROUNDED))
    
    def _display_plain_enhanced_analyze_results(self, results: EnhancedAnalysisResult):
        """Display enhanced analysis results in plain text format."""
        # Display basic EXIF analysis
        self._display_plain_analyze_results(results.exif_analysis)
        
        # Display device breakdown
        if results.device_breakdown:
            print("\n=== Device Breakdown ===")
            for device_code, stats in results.device_breakdown.items():
                print(f"{device_code}: {stats['file_count']:,} files, {self._format_size(stats['total_size'])}")
        
        # Display visual analysis
        if results.visual_analysis:
            print("\n=== Visual Analysis ===")
            if results.visual_analysis.overall_summary:
                print(f"Overall Summary: {results.visual_analysis.overall_summary}")
            
            for device_code, analysis in results.visual_analysis.device_analyses.items():
                if analysis.device_summary:
                    print(f"{device_code}: {analysis.device_summary}")
        
        # Processing summary
        print("\n=== Processing Summary ===")
        print(f"Mode: {results.processing_mode}")
        print(f"Total Time: {results.total_processing_time:.2f}s")
    
    def _display_plain_analyze_results(self, results: AnalysisResult):
        """Display analysis results in plain text format."""
        print("\n=== Analysis Summary ===")
        print(f"Total Files: {results.total_files:,}")
        print(f"Images Analyzed: {results.image_files_analyzed:,}")
        print(f"Total Size: {self._format_size(results.total_size)}")
        print(f"Scan Time: {results.scan_time:.2f}s")
        print(f"EXIF Time: {results.exif_time:.2f}s")
        
        if results.date_range:
            print(f"Date Range: {results.date_range[0]} to {results.date_range[1]}")
        
        if results.cameras:
            print("\n=== Cameras ===")
            for camera, count in sorted(results.cameras.items(), key=lambda x: x[1], reverse=True):
                print(f"  {camera}: {count:,} files")
        
        if results.lenses:
            print("\n=== Lenses ===")
            for lens, count in sorted(results.lenses.items(), key=lambda x: x[1], reverse=True)[:5]:
                print(f"  {lens}: {count:,} files")
        
        if results.aperture_range:
            print(f"\nAperture Range: f/{results.aperture_range[0]:.1f} - f/{results.aperture_range[1]:.1f}")
        
        if results.iso_range:
            print(f"ISO Range: {results.iso_range[0]} - {results.iso_range[1]}")
        
        if results.files_with_gps > 0:
            print(f"Files with GPS: {results.files_with_gps:,}")
    
    def _display_plain_ingest_plan(self, operations: List[FileOperation], dry_run: bool):
        """Display ingest plan in plain text format."""
        title = "=== Dry Run Plan ===" if dry_run else "=== Ingest Plan ==="
        print(f"\n{title}")
        
        total_files = len(operations)
        duplicates = sum(1 for op in operations if op.duplicate_status == DuplicateStatus.DUPLICATE)
        
        print(f"Total Files: {total_files:,}")
        print(f"New Files: {total_files - duplicates:,}")
        print(f"Duplicates: {duplicates:,}")
        
        # Show sample operations
        print("\n=== Sample Operations ===")
        for i, op in enumerate(operations[:5]):
            status = "DUPLICATE" if op.duplicate_status == DuplicateStatus.DUPLICATE else "NEW"
            dest = op.dest_path or op.raw_backup_path
            print(f"  {op.source_path.name} -> {dest.name if dest else 'N/A'} [{status}]")
        
        if len(operations) > 5:
            print(f"  ... and {len(operations) - 5} more files")
    
    def _display_plain_ingest_results(self, results: OperationResults):
        """Display ingest results in plain text format."""
        print("\n=== Ingest Results ===")
        print(f"Files Processed: {results.files_processed:,}")
        print(f"Files Copied: {results.files_copied:,}")
        print(f"Files Moved: {results.files_moved:,}")
        print(f"Files Skipped: {results.files_skipped:,}")
        print(f"Duplicates Found: {results.duplicates_found:,}")
        
        if results.errors:
            print(f"Errors: {len(results.errors)}")
            if self.verbosity > 0:
                for error in results.errors[:5]:
                    print(f"  - {error}")
        
        if results.raw_backup_result:
            print("\n=== Raw Backup Results ===")
            print(f"Backup Directory: {results.raw_backup_result.backup_directory}")
            print(f"Files Backed Up: {results.raw_backup_result.files_backed_up:,}")
            print(f"Total Size: {self._format_size(results.raw_backup_result.total_size)}")
    
    def _format_size(self, size_bytes: int) -> str:
        """Format file size in human readable format."""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} PB"
    
    def print_message(self, message: str, style: Optional[str] = None):
        """Print a message with optional styling.
        
        Args:
            message: Message to print
            style: Rich style string (if Rich is available)
        """
        if self.quiet:
            return
        
        if RICH_AVAILABLE and style:
            self.console.print(message, style=style)
        else:
            print(message)
    
    def print_progress(self, message: str, style: Optional[str] = None):
        """Print a progress message that overwrites the previous line.
        
        Args:
            message: Progress message to print
            style: Rich style string (if Rich is available)
        """
        if self.quiet:
            return
        
        if RICH_AVAILABLE:
            # Use console.print with end='\r' to overwrite the line
            self.console.print(message, style=style, end='\r')
        else:
            # For plain text, use print with end='\r' and flush
            print(message, end='\r', flush=True)
    
    def print_error(self, message: str):
        """Print an error message."""
        if RICH_AVAILABLE:
            self.console.print(f"❌ {message}", style="red")
        else:
            print(f"ERROR: {message}")
    
    def print_warning(self, message: str):
        """Print a warning message."""
        if RICH_AVAILABLE:
            self.console.print(f"⚠️  {message}", style="yellow")
        else:
            print(f"WARNING: {message}")
    
    def print_success(self, message: str):
        """Print a success message."""
        if RICH_AVAILABLE:
            self.console.print(f"✅ {message}", style="green")
        else:
            print(f"SUCCESS: {message}")