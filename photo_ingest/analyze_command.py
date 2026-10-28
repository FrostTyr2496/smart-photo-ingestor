"""Enhanced analyze command with EXIF summarization and optional visual analysis."""

import time
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from collections import defaultdict

from .analyzer import PhotoAnalyzer, AnalysisResult
from .visual_analyzer import VisualContentAnalyzer, PeekResults
from .device_detector import DeviceDetector
from .config import IngestConfig, FileTypes, PerformanceConfig
from .file_scanner import FileScanner


@dataclass
class EnhancedAnalysisResult:
    """Enhanced analysis result combining EXIF and visual analysis."""
    # EXIF analysis results
    exif_analysis: AnalysisResult
    
    # Visual analysis results (optional)
    visual_analysis: Optional[PeekResults] = None
    
    # Device-specific breakdown
    device_breakdown: Dict[str, Dict[str, Any]] = None
    
    # Processing metadata
    processing_mode: str = "exif_only"  # "exif_only", "peek", "exif_with_visual"
    total_processing_time: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "exif_analysis": self.exif_analysis.to_dict(),
            "device_breakdown": self.device_breakdown or {},
            "processing_mode": self.processing_mode,
            "total_processing_time": self.total_processing_time
        }
        
        if self.visual_analysis:
            visual_data = {
                "device_analyses": {
                    device: {
                        "device_code": analysis.device_code,
                        "sampled_files": [str(f) for f in analysis.sampled_files],
                        "visual_analyses": [
                            {
                                "file_path": str(va.file_path),
                                "visual_description": va.visual_description,
                                "confidence": va.confidence,
                                "processing_time": va.processing_time,
                                "error": va.error,
                                "location_info": va.location_info
                            }
                            for va in analysis.visual_analyses
                        ],
                        "device_summary": analysis.device_summary
                    }
                    for device, analysis in self.visual_analysis.device_analyses.items()
                },
                "overall_summary": self.visual_analysis.overall_summary,
                "processing_stats": asdict(self.visual_analysis.processing_stats) if self.visual_analysis.processing_stats else None
            }
            
            # Add folder analyses if available
            if self.visual_analysis.folder_analyses:
                visual_data["folder_analyses"] = {
                    folder_path: {
                        "folder_path": str(analysis.folder_path),
                        "file_count": analysis.file_count,
                        "sampled_files": [str(f) for f in analysis.sampled_files],
                        "visual_analyses": [
                            {
                                "file_path": str(va.file_path),
                                "visual_description": va.visual_description,
                                "confidence": va.confidence,
                                "processing_time": va.processing_time,
                                "error": va.error,
                                "location_info": va.location_info
                            }
                            for va in analysis.visual_analyses
                        ],
                        "folder_summary": analysis.folder_summary
                    }
                    for folder_path, analysis in self.visual_analysis.folder_analyses.items()
                }
                visual_data["folder_summary"] = self.visual_analysis.folder_summary
            
            result["visual_analysis"] = visual_data
        
        return result


class AnalyzeCommand:
    """Enhanced analyze command with EXIF summarization and optional visual analysis."""
    
    def __init__(self, 
                 config: Optional[IngestConfig] = None,
                 file_types: Optional[FileTypes] = None,
                 performance_config: Optional[PerformanceConfig] = None):
        """Initialize analyze command.
        
        Args:
            config: Full ingest configuration (optional)
            file_types: File type configuration
            performance_config: Performance settings
        """
        self.config = config
        self.file_types = file_types or (config.file_types if config else FileTypes())
        self.performance_config = performance_config or (config.performance if config else PerformanceConfig())
        
        # Initialize components
        self.scanner = FileScanner(self.file_types, self.performance_config)
        self.analyzer = PhotoAnalyzer(self.file_types, self.performance_config, detailed=True)
        
        # Initialize device detector if config available
        self.device_detector = None
        if config and config.devices:
            self.device_detector = DeviceDetector(config.devices)
        
        # Initialize visual analyzer if peek is enabled
        self.visual_analyzer = None
        if config and config.peek.enabled and config.llm.enabled:
            self.visual_analyzer = VisualContentAnalyzer(config.peek, config.llm)
    
    def analyze_directory(self, 
                         directory: Path,
                         peek_mode: bool = False,
                         exif_only: bool = False,
                         samples: int = 5,
                         enable_folder_analysis: bool = True,
                         progress_callback=None) -> EnhancedAnalysisResult:
        """Analyze directory with enhanced capabilities.
        
        Args:
            directory: Directory to analyze
            peek_mode: Enable visual content analysis
            exif_only: Skip visual analysis even if configured
            samples: Number of sample images per device for visual analysis
            progress_callback: Optional progress callback function
            
        Returns:
            EnhancedAnalysisResult with comprehensive analysis
        """
        start_time = time.time()
        
        # Determine processing mode
        processing_mode = self._determine_processing_mode(peek_mode, exif_only)
        
        # Run EXIF analysis
        if progress_callback:
            progress_callback("analyze", 0, 3, "Starting EXIF analysis")
        
        exif_result = self.analyzer.analyze_directory(directory, progress_callback)
        
        if progress_callback:
            progress_callback("analyze", 1, 3, "EXIF analysis complete")
        
        # Create device breakdown
        device_breakdown = self._create_device_breakdown(directory, exif_result)
        
        # Run visual analysis if requested and available
        visual_result = None
        if processing_mode in ["peek", "exif_with_visual"] and self.visual_analyzer:
            if progress_callback:
                progress_callback("analyze", 2, 3, "Starting visual analysis")
            
            # Group files by device for visual analysis
            device_groups = self._group_files_by_device(directory, samples)
            
            if device_groups:
                # Update peek config with custom sample count
                if self.config:
                    original_sample_count = self.config.peek.sample_count
                    self.config.peek.sample_count = samples
                
                visual_result = self.visual_analyzer.analyze_directory(
                    device_groups, 
                    progress_callback, 
                    enable_folder_analysis=enable_folder_analysis
                )
                
                # Restore original sample count
                if self.config:
                    self.config.peek.sample_count = original_sample_count
        
        if progress_callback:
            progress_callback("analyze", 3, 3, "Analysis complete")
        
        total_time = time.time() - start_time
        
        return EnhancedAnalysisResult(
            exif_analysis=exif_result,
            visual_analysis=visual_result,
            device_breakdown=device_breakdown,
            processing_mode=processing_mode,
            total_processing_time=total_time
        )
    
    def _determine_processing_mode(self, peek_mode: bool, exif_only: bool) -> str:
        """Determine the processing mode based on flags and configuration.
        
        Args:
            peek_mode: Peek mode requested
            exif_only: EXIF-only mode requested
            
        Returns:
            Processing mode string
        """
        if exif_only:
            return "exif_only"
        
        if peek_mode:
            if self.visual_analyzer:
                return "peek"
            else:
                return "exif_only"  # Fall back if visual analysis not available
        
        # Check if visual analysis is enabled by default in config
        if self.config and self.config.peek.include_visual_analysis and self.visual_analyzer:
            return "exif_with_visual"
        
        return "exif_only"
    
    def _create_device_breakdown(self, directory: Path, exif_result: AnalysisResult) -> Dict[str, Dict[str, Any]]:
        """Create device-specific breakdown of analysis results.
        
        Args:
            directory: Source directory
            exif_result: EXIF analysis results
            
        Returns:
            Dictionary with device-specific statistics
        """
        if not self.device_detector:
            return {}
        
        # Scan files and group by device
        scan_result = self.scanner.scan_directory(directory, recursive=True)
        image_files = [f for f in scan_result.files if f.file_type in ['raw', 'jpeg']]
        
        device_stats = defaultdict(lambda: {
            'file_count': 0,
            'total_size': 0,
            'cameras': set(),
            'lenses': set(),
            'date_range': [None, None],
            'iso_range': [None, None],
            'aperture_range': [None, None]
        })
        
        # One chunked ExifTool pass; the per-file calls below are then served from memory.
        self.analyzer.prefetch_metadata([f.path for f in image_files])

        # Process each image file
        for file_info in image_files:
            # Extract basic metadata for device detection
            metadata = self.analyzer._extract_basic_exif(file_info.path)
            if not metadata:
                continue
            
            # Detect device
            device_code = self.device_detector.detect_device(metadata)
            stats = device_stats[device_code]
            
            # Update statistics
            stats['file_count'] += 1
            stats['total_size'] += file_info.size
            
            # Camera and lens info
            make = metadata.get('Make', '').strip()
            model = metadata.get('Model', '').strip()
            if make and model:
                stats['cameras'].add(f"{make} {model}")
            elif model:
                stats['cameras'].add(model)
            
            lens = metadata.get('LensModel', '').strip()
            if lens:
                stats['lenses'].add(lens)
            
            # Date range
            if metadata.get('DateTime'):
                date = metadata['DateTime']
                if stats['date_range'][0] is None or date < stats['date_range'][0]:
                    stats['date_range'][0] = date
                if stats['date_range'][1] is None or date > stats['date_range'][1]:
                    stats['date_range'][1] = date
            
            # ISO range
            if metadata.get('ISO') is not None:
                iso = metadata['ISO']
                if stats['iso_range'][0] is None or iso < stats['iso_range'][0]:
                    stats['iso_range'][0] = iso
                if stats['iso_range'][1] is None or iso > stats['iso_range'][1]:
                    stats['iso_range'][1] = iso
            
            # Aperture range
            if metadata.get('FNumber') is not None:
                aperture = metadata['FNumber']
                if stats['aperture_range'][0] is None or aperture < stats['aperture_range'][0]:
                    stats['aperture_range'][0] = aperture
                if stats['aperture_range'][1] is None or aperture > stats['aperture_range'][1]:
                    stats['aperture_range'][1] = aperture
        
        # Convert to serializable format
        result = {}
        for device_code, stats in device_stats.items():
            result[device_code] = {
                'file_count': stats['file_count'],
                'total_size': stats['total_size'],
                'cameras': list(stats['cameras']),
                'lenses': list(stats['lenses']),
                'date_range': [
                    stats['date_range'][0].strftime('%Y-%m-%d') if stats['date_range'][0] else None,
                    stats['date_range'][1].strftime('%Y-%m-%d') if stats['date_range'][1] else None
                ],
                'iso_range': stats['iso_range'],
                'aperture_range': stats['aperture_range']
            }
        
        return result
    
    def _group_files_by_device(self, directory: Path, samples: int) -> Dict[str, List[Path]]:
        """Group files by detected device for visual analysis.
        
        Args:
            directory: Source directory
            samples: Number of samples per device
            
        Returns:
            Dictionary mapping device codes to file lists
        """
        if not self.device_detector:
            # If no device detector, group all files under "Unknown"
            scan_result = self.scanner.scan_directory(directory, recursive=True)
            image_files = [f.path for f in scan_result.files if f.file_type in ['raw', 'jpeg']]
            return {"Unknown": image_files}
        
        # Scan and group by device
        scan_result = self.scanner.scan_directory(directory, recursive=True)
        image_files = [f for f in scan_result.files if f.file_type in ['raw', 'jpeg']]
        
        device_groups = defaultdict(list)
        
        # One chunked ExifTool pass; the per-file calls below are then served from memory.
        self.analyzer.prefetch_metadata([f.path for f in image_files])
        
        for file_info in image_files:
            # Extract metadata for device detection
            metadata = self.analyzer._extract_basic_exif(file_info.path)
            device_code = "Unknown"
            
            if metadata:
                device_code = self.device_detector.detect_device(metadata)
            
            device_groups[device_code].append(file_info.path)
        
        return dict(device_groups)
    
    def get_supported_formats(self) -> List[str]:
        """Get list of supported file formats.
        
        Returns:
            List of supported file extensions
        """
        return self.file_types.get_all_extensions()
    
    def is_visual_analysis_available(self) -> bool:
        """Check if visual analysis is available.
        
        Returns:
            True if visual analysis can be performed
        """
        return self.visual_analyzer is not None
    
    def set_verbose(self, verbose: bool):
        """Enable or disable verbose logging.
        
        Args:
            verbose: Whether to enable verbose logging
        """
        if self.visual_analyzer:
            # Recreate visual analyzer with verbose flag
            self.visual_analyzer = VisualContentAnalyzer(
                self.config.peek, 
                self.config.llm, 
                verbose=verbose
            )