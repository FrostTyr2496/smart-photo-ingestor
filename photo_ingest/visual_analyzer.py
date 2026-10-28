"""Visual content analyzer with image sampling and LLM integration."""

import logging
import random
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from io import BytesIO
from collections import defaultdict

try:
    from PIL import Image
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

from .config import PeekConfig, LLMConfig
from .llm_client import VisionLLMClient
from .location_service import LocationService, LocationInfo

logger = logging.getLogger(__name__)


@dataclass
class ProcessedImage:
    """Processed image ready for LLM analysis."""
    image_data: bytes
    format: str = "jpeg"
    resolution: Tuple[int, int] = (0, 0)
    file_size: int = 0


@dataclass
class ImageAnalysis:
    """Analysis result for a single image."""
    file_path: Path
    visual_description: str
    confidence: float
    processing_time: float
    error: Optional[str] = None
    location_info: Optional[Dict[str, Any]] = None


@dataclass
class DeviceAnalysis:
    """Visual analysis results for a specific device."""
    device_code: str
    sampled_files: List[Path]
    visual_analyses: List[ImageAnalysis]
    device_summary: Optional[str] = None


@dataclass
class FolderAnalysis:
    """Visual analysis results for a specific folder."""
    folder_path: Path
    file_count: int
    sampled_files: List[Path]
    visual_analyses: List[ImageAnalysis]
    folder_summary: Optional[str] = None


@dataclass
class PeekResults:
    """Results from peek mode analysis."""
    device_analyses: Dict[str, DeviceAnalysis]
    folder_analyses: Dict[str, FolderAnalysis] = None
    overall_summary: Optional[str] = None
    folder_summary: Optional[str] = None
    processing_stats: 'PeekProcessingStats' = None


@dataclass
class PeekProcessingStats:
    """Processing statistics for peek operation."""
    total_files_scanned: int
    images_sampled: int
    processing_time: float
    llm_calls_made: int
    errors_encountered: int


class ImageProcessor:
    """Handles image downscaling and format conversion for LLM analysis."""
    
    def __init__(self, max_resolution: int = 1024):
        """Initialize image processor.
        
        Args:
            max_resolution: Maximum dimension for processed images
        """
        if not PILLOW_AVAILABLE:
            raise ImportError("Pillow is required for image processing")
        
        self.max_resolution = max_resolution
    
    def prepare_for_analysis(self, file_path: Path) -> Optional[ProcessedImage]:
        """Downscale image to ~1MP and convert to JPEG for LLM.
        
        Args:
            file_path: Path to image file
            
        Returns:
            ProcessedImage ready for LLM analysis, or None if processing failed
        """
        try:
            # Try to extract embedded preview from RAW files first
            if self._is_raw_file(file_path):
                preview_data = self.extract_raw_preview(file_path)
                if preview_data:
                    return self._process_image_data(preview_data, file_path)
            
            # Fall back to standard image processing
            with Image.open(file_path) as img:
                # Convert to RGB if necessary
                if img.mode not in ('RGB', 'L'):
                    img = img.convert('RGB')
                
                # Downscale if needed
                processed_img = self.downscale_image(img, self.max_resolution)
                
                # Convert to JPEG bytes
                buffer = BytesIO()
                processed_img.save(buffer, format='JPEG', quality=85, optimize=True)
                image_data = buffer.getvalue()
                
                return ProcessedImage(
                    image_data=image_data,
                    format="jpeg",
                    resolution=(processed_img.width, processed_img.height),
                    file_size=len(image_data)
                )
                
        except Exception as e:
            logger.debug(f"Failed to process image {file_path.name}: {e}")
            return None
    
    def extract_raw_preview(self, raw_file: Path) -> Optional[bytes]:
        """Extract embedded JPEG preview from RAW files.
        
        Args:
            raw_file: Path to RAW file
            
        Returns:
            JPEG preview data or None if extraction failed
        """
        try:
            # Try to open with PIL first (works for some RAW formats)
            with Image.open(raw_file) as img:
                # If PIL can open it, it might have a preview
                if hasattr(img, 'thumbnail'):
                    # Create a copy for processing
                    preview = img.copy()
                    if preview.mode not in ('RGB', 'L'):
                        preview = preview.convert('RGB')
                    
                    buffer = BytesIO()
                    preview.save(buffer, format='JPEG', quality=85)
                    return buffer.getvalue()
        except Exception:
            pass
        
        # TODO (researched 2026-10-28, Nikon Z 6 NEF; not yet implemented): do NOT add rawpy.
        # Pillow only sees the NEF's tiny thumbnail (120x160), so the Pillow branch above is
        # not a usable preview for Nikon RAW. ExifTool already ships the embedded JPEGs:
        #   exiftool -b -JpgFromRaw  file.NEF  -> 6048x4024 full-size (~1 MB)  <- best for visual analysis
        #   exiftool -b -OtherImage  file.NEF  -> 1620x1080
        #   exiftool -b -PreviewImage file.NEF -> 640x424
        # Pass the tag list as an argument vector (never shell-interpolate the path), read
        # stdout bytes, and keep this optional/time-limited like the rest of the LLM path.
        # Other brands differ (e.g. Canon CR2 uses PreviewImage for the large JPEG), so try
        # JpgFromRaw, then OtherImage, then PreviewImage.
        return None
    
    def downscale_image(self, image: Image.Image, target_resolution: int) -> Image.Image:
        """Downscale image to target resolution maintaining aspect ratio.
        
        Args:
            image: PIL Image object
            target_resolution: Target maximum dimension
            
        Returns:
            Downscaled PIL Image
        """
        width, height = image.size
        max_dimension = max(width, height)
        
        if max_dimension <= target_resolution:
            return image
        
        # Calculate new dimensions maintaining aspect ratio
        scale_factor = target_resolution / max_dimension
        new_width = int(width * scale_factor)
        new_height = int(height * scale_factor)
        
        return image.resize((new_width, new_height), Image.Resampling.LANCZOS)
    
    def _is_raw_file(self, file_path: Path) -> bool:
        """Check if file is a RAW format."""
        raw_extensions = {'.nef', '.cr3', '.cr2', '.arw', '.dng', '.orf', '.raf', '.rw2'}
        return file_path.suffix.lower() in raw_extensions
    
    def _process_image_data(self, image_data: bytes, file_path: Path) -> Optional[ProcessedImage]:
        """Process raw image data into ProcessedImage."""
        try:
            with Image.open(BytesIO(image_data)) as img:
                if img.mode not in ('RGB', 'L'):
                    img = img.convert('RGB')
                
                processed_img = self.downscale_image(img, self.max_resolution)
                
                buffer = BytesIO()
                processed_img.save(buffer, format='JPEG', quality=85, optimize=True)
                processed_data = buffer.getvalue()
                
                return ProcessedImage(
                    image_data=processed_data,
                    format="jpeg",
                    resolution=(processed_img.width, processed_img.height),
                    file_size=len(processed_data)
                )
        except Exception as e:
            logger.debug(f"Failed to process image data for {file_path.name}: {e}")
            return None


class VisualContentAnalyzer:
    """Analyzes visual content using LLM vision models."""
    
    def __init__(self, config: PeekConfig, llm_config: LLMConfig, verbose: bool = False, enable_location: bool = True):
        """Initialize visual content analyzer.
        
        Args:
            config: Peek configuration
            llm_config: LLM configuration
            verbose: Enable verbose logging
            enable_location: Enable GPS location resolution
        """
        self.config = config
        self.llm_config = llm_config
        self.verbose = verbose
        self.image_processor = ImageProcessor(config.max_resolution)
        self.vision_client = VisionLLMClient(llm_config, verbose) if llm_config.enabled else None
        self.location_service = LocationService(enable_reverse_geocoding=enable_location) if enable_location else None
    
    def analyze_directory(self, device_groups: Dict[str, List[Path]], 
                         progress_callback=None, 
                         enable_folder_analysis: bool = True) -> PeekResults:
        """Analyze visual content by device and folder with sampling.
        
        Args:
            device_groups: Dictionary mapping device codes to file lists
            progress_callback: Optional progress callback function
            enable_folder_analysis: Whether to perform folder-based analysis
            
        Returns:
            PeekResults with visual analysis data
        """
        start_time = time.time()
        results = PeekResults(device_analyses={})
        
        total_files = sum(len(files) for files in device_groups.values())
        total_sampled = 0
        llm_calls = 0
        errors = 0
        processed = 0
        
        for device_code, files in device_groups.items():
            if progress_callback:
                progress_callback("visual", processed, len(device_groups), f"Analyzing {device_code}")
            
            # Sample images from this device
            sampled_files = self._sample_images(files, self.config.sample_count)
            total_sampled += len(sampled_files)
            
            # Process each sampled image
            device_analysis = DeviceAnalysis(
                device_code=device_code,
                sampled_files=sampled_files,
                visual_analyses=[]
            )
            
            for file_path in sampled_files:
                try:
                    # Extract location information from EXIF if available
                    location_info = self._extract_location_info(file_path)
                    
                    if self.config.include_visual_analysis and self.vision_client:
                        # Process and analyze with vision LLM
                        processed_image = self.image_processor.prepare_for_analysis(file_path)
                        if processed_image:
                            analysis_start = time.time()
                            
                            # Create location-aware prompt
                            prompt = self._create_location_aware_prompt(self.config.vision_prompt, location_info)
                            
                            visual_analysis = self.vision_client.analyze_image(
                                processed_image, 
                                prompt
                            )
                            analysis_time = time.time() - analysis_start
                            llm_calls += 1
                            
                            device_analysis.visual_analyses.append(ImageAnalysis(
                                file_path=file_path,
                                visual_description=visual_analysis.visual_description,
                                confidence=visual_analysis.confidence,
                                processing_time=analysis_time,
                                location_info=location_info.to_dict() if location_info else None
                            ))
                        else:
                            errors += 1
                            device_analysis.visual_analyses.append(ImageAnalysis(
                                file_path=file_path,
                                visual_description="Failed to process image",
                                confidence=0.0,
                                processing_time=0.0,
                                error="Image processing failed",
                                location_info=location_info.to_dict() if location_info else None
                            ))
                    else:
                        # Just record that we sampled this file
                        device_analysis.visual_analyses.append(ImageAnalysis(
                            file_path=file_path,
                            visual_description="Visual analysis disabled",
                            confidence=0.0,
                            processing_time=0.0,
                            location_info=location_info.to_dict() if location_info else None
                        ))
                        
                except Exception as e:
                    errors += 1
                    device_analysis.visual_analyses.append(ImageAnalysis(
                        file_path=file_path,
                        visual_description=f"Analysis failed: {str(e)}",
                        confidence=0.0,
                        processing_time=0.0,
                        error=str(e)
                    ))
            
            # Generate device summary if we have analyses
            if device_analysis.visual_analyses and self.vision_client:
                device_analysis.device_summary = self._generate_device_summary(device_analysis)
            
            results.device_analyses[device_code] = device_analysis
            processed += 1
        
        # Perform folder-based analysis if enabled
        if enable_folder_analysis:
            if progress_callback:
                progress_callback("visual", processed, len(device_groups) + 1, "Analyzing by folders")
            
            folder_analyses = self._analyze_by_folders(device_groups, progress_callback)
            results.folder_analyses = folder_analyses
            
            # Generate folder summary
            if self.vision_client and folder_analyses:
                results.folder_summary = self._generate_folder_summary(folder_analyses)
                llm_calls += len([f for f in folder_analyses.values() if f.folder_summary])
        
        # Generate overall summary
        if self.vision_client and (results.device_analyses or results.folder_analyses):
            results.overall_summary = self._generate_enhanced_overall_summary(results)
            llm_calls += 1
        
        processing_time = time.time() - start_time
        results.processing_stats = PeekProcessingStats(
            total_files_scanned=total_files,
            images_sampled=total_sampled,
            processing_time=processing_time,
            llm_calls_made=llm_calls,
            errors_encountered=errors
        )
        
        return results
    
    def _sample_images(self, files: List[Path], sample_count: int) -> List[Path]:
        """Randomly sample images, prioritizing supported formats.
        
        Args:
            files: List of file paths
            sample_count: Number of files to sample
            
        Returns:
            List of sampled file paths
        """
        # Filter for supported formats
        supported_files = []
        for file_path in files:
            ext = file_path.suffix.lower().lstrip('.')
            if ext in self.config.supported_formats:
                supported_files.append(file_path)
        
        if not supported_files:
            return []
        
        # Sample randomly
        sample_size = min(sample_count, len(supported_files))
        return random.sample(supported_files, sample_size)
    
    def _generate_device_summary(self, device_analysis: DeviceAnalysis) -> str:
        """Generate summary for a specific device's visual analysis.
        
        Args:
            device_analysis: Device analysis results
            
        Returns:
            Summary string for the device
        """
        if not self.vision_client:
            return "Visual analysis not available"
        
        # Collect all visual descriptions and locations
        descriptions = []
        locations = []
        for analysis in device_analysis.visual_analyses:
            if analysis.visual_description and not analysis.error:
                descriptions.append(analysis.visual_description)
                if analysis.location_info and analysis.location_info.get('formatted_address'):
                    locations.append(analysis.location_info['formatted_address'])
        
        if not descriptions:
            return "No successful visual analyses"
        
        # Create location context if available
        location_context = ""
        if locations:
            unique_locations = list(set(locations))
            if len(unique_locations) == 1:
                location_context = f"\nLOCATION: Photos were taken at {unique_locations[0]}."
            elif len(unique_locations) <= 3:
                location_context = f"\nLOCATIONS: Photos were taken at {', '.join(unique_locations)}."
            else:
                location_context = f"\nLOCATIONS: Photos were taken at multiple locations including {', '.join(unique_locations[:3])} and others."
        
        # Create a summary prompt
        summary_prompt = f"""Based on these {len(descriptions)} photo descriptions from a {device_analysis.device_code} camera, provide a 2-3 sentence summary of the photography session:

{chr(10).join(f"- {desc}" for desc in descriptions)}{location_context}

Focus on common themes, subjects, photography style, and how the locations relate to the content."""
        
        try:
            summary_response = self.vision_client.generate_text_summary(summary_prompt)
            return summary_response
        except Exception as e:
            return f"Failed to generate device summary: {str(e)}"
    
    def _analyze_by_folders(self, device_groups: Dict[str, List[Path]], 
                           progress_callback=None) -> Dict[str, FolderAnalysis]:
        """Analyze images grouped by folder.
        
        Args:
            device_groups: Dictionary mapping device codes to file lists
            progress_callback: Optional progress callback function
            
        Returns:
            Dictionary mapping folder paths to folder analyses
        """
        # Group all files by their parent directory
        folder_groups = defaultdict(list)
        for device_files in device_groups.values():
            for file_path in device_files:
                folder_key = str(file_path.parent)
                folder_groups[folder_key].append(file_path)
        
        folder_analyses = {}
        processed_folders = 0
        
        for folder_path_str, files in folder_groups.items():
            folder_path = Path(folder_path_str)
            
            if progress_callback:
                progress_callback("folder", processed_folders, len(folder_groups), f"Analyzing {folder_path.name}")
            
            # Sample images from this folder
            sampled_files = self._sample_images(files, self.config.sample_count)
            
            # Process each sampled image
            folder_analysis = FolderAnalysis(
                folder_path=folder_path,
                file_count=len(files),
                sampled_files=sampled_files,
                visual_analyses=[]
            )
            
            for file_path in sampled_files:
                try:
                    # Extract location information from EXIF if available
                    location_info = self._extract_location_info(file_path)
                    
                    if self.config.include_visual_analysis and self.vision_client:
                        # Process and analyze with vision LLM
                        processed_image = self.image_processor.prepare_for_analysis(file_path)
                        if processed_image:
                            analysis_start = time.time()
                            
                            # Create location-aware prompt
                            prompt = self._create_location_aware_prompt(self.config.vision_prompt, location_info)
                            
                            visual_analysis = self.vision_client.analyze_image(
                                processed_image, 
                                prompt
                            )
                            analysis_time = time.time() - analysis_start
                            
                            folder_analysis.visual_analyses.append(ImageAnalysis(
                                file_path=file_path,
                                visual_description=visual_analysis.visual_description,
                                confidence=visual_analysis.confidence,
                                processing_time=analysis_time,
                                location_info=location_info.to_dict() if location_info else None
                            ))
                        else:
                            folder_analysis.visual_analyses.append(ImageAnalysis(
                                file_path=file_path,
                                visual_description="Failed to process image",
                                confidence=0.0,
                                processing_time=0.0,
                                error="Image processing failed",
                                location_info=location_info.to_dict() if location_info else None
                            ))
                    else:
                        # Just record that we sampled this file
                        folder_analysis.visual_analyses.append(ImageAnalysis(
                            file_path=file_path,
                            visual_description="Visual analysis disabled",
                            confidence=0.0,
                            processing_time=0.0,
                            location_info=location_info.to_dict() if location_info else None
                        ))
                        
                except Exception as e:
                    folder_analysis.visual_analyses.append(ImageAnalysis(
                        file_path=file_path,
                        visual_description=f"Analysis failed: {str(e)}",
                        confidence=0.0,
                        processing_time=0.0,
                        error=str(e)
                    ))
            
            # Generate folder summary if we have analyses
            if folder_analysis.visual_analyses and self.vision_client:
                folder_analysis.folder_summary = self._generate_single_folder_summary(folder_analysis)
            
            folder_analyses[folder_path_str] = folder_analysis
            processed_folders += 1
        
        return folder_analyses
    
    def _generate_single_folder_summary(self, folder_analysis: FolderAnalysis) -> str:
        """Generate summary for a specific folder's visual analysis.
        
        Args:
            folder_analysis: Folder analysis results
            
        Returns:
            Summary string for the folder
        """
        if not self.vision_client:
            return "Visual analysis not available"
        
        # Collect all visual descriptions and locations
        descriptions = []
        locations = []
        for analysis in folder_analysis.visual_analyses:
            if analysis.visual_description and not analysis.error and analysis.visual_description != "Visual analysis disabled":
                descriptions.append(analysis.visual_description)
                if analysis.location_info and analysis.location_info.get('formatted_address'):
                    locations.append(analysis.location_info['formatted_address'])
        
        if not descriptions:
            return "No successful visual analyses"
        
        # Create location context if available
        location_context = ""
        if locations:
            unique_locations = list(set(locations))
            if len(unique_locations) == 1:
                location_context = f"\nLOCATION: Photos in this folder were taken at {unique_locations[0]}."
            elif len(unique_locations) <= 3:
                location_context = f"\nLOCATIONS: Photos in this folder were taken at {', '.join(unique_locations)}."
            else:
                location_context = f"\nLOCATIONS: Photos in this folder span multiple locations including {', '.join(unique_locations[:3])} and others."
        
        # Create a folder summary prompt
        folder_name = folder_analysis.folder_path.name
        summary_prompt = f"""Based on these {len(descriptions)} photo descriptions from the folder "{folder_name}" (containing {folder_analysis.file_count} total images), provide a 2-3 sentence summary of what this folder contains:

{chr(10).join(f"- {desc}" for desc in descriptions)}{location_context}

Focus on the main subjects, themes, photographic content, and geographic context of this specific folder."""
        
        try:
            summary_response = self.vision_client.generate_text_summary(summary_prompt)
            return summary_response
        except Exception as e:
            return f"Failed to generate folder summary: {str(e)}"
    
    def _generate_folder_summary(self, folder_analyses: Dict[str, FolderAnalysis]) -> str:
        """Generate overall summary from all folder analyses.
        
        Args:
            folder_analyses: Dictionary of folder analyses
            
        Returns:
            Overall folder summary string
        """
        if not self.vision_client:
            return "Visual analysis not available"
        
        # Collect folder summaries
        folder_summaries = []
        for folder_path, analysis in folder_analyses.items():
            if analysis.folder_summary and "Failed to generate" not in analysis.folder_summary:
                folder_name = Path(folder_path).name
                folder_summaries.append(f"📁 {folder_name} ({analysis.file_count} files): {analysis.folder_summary}")
        
        if not folder_summaries:
            return "No folder summaries available"
        
        # Create folder overview prompt
        folder_prompt = f"""Based on these folder-by-folder photography summaries, provide a 3-4 sentence overview of the entire photo collection organized by folders:

{chr(10).join(folder_summaries)}

Describe the variety of content, organization patterns, and overall scope of the photography collection."""
        
        try:
            folder_response = self.vision_client.generate_text_summary(folder_prompt)
            return folder_response
        except Exception as e:
            return f"Failed to generate folder overview: {str(e)}"
    
    def _extract_location_info(self, file_path: Path) -> Optional[LocationInfo]:
        """Extract GPS location information from image EXIF data.
        
        Args:
            file_path: Path to image file
            
        Returns:
            LocationInfo object or None if no GPS data found
        """
        if not self.location_service:
            return None
        
        try:
            from PIL import Image
            from PIL.ExifTags import TAGS, GPSTAGS
            
            with Image.open(file_path) as img:
                exif_dict = img.getexif()
                if not exif_dict:
                    return None
                
                # Look for GPS info
                gps_info = {}
                for tag_id, value in exif_dict.items():
                    tag_name = TAGS.get(tag_id, tag_id)
                    if tag_name == 'GPSInfo':
                        for gps_tag_id, gps_value in value.items():
                            gps_tag_name = GPSTAGS.get(gps_tag_id, gps_tag_id)
                            gps_info[gps_tag_name] = gps_value
                        break
                
                if not gps_info:
                    return None
                
                # Extract latitude and longitude
                latitude = self._parse_gps_coordinate(
                    gps_info.get('GPSLatitude'),
                    gps_info.get('GPSLatitudeRef')
                )
                longitude = self._parse_gps_coordinate(
                    gps_info.get('GPSLongitude'),
                    gps_info.get('GPSLongitudeRef')
                )
                
                if latitude is not None and longitude is not None:
                    return self.location_service.get_location_info(latitude, longitude)
                
        except Exception as e:
            if self.verbose:
                logger.debug(f"Failed to extract GPS from {file_path.name}: {e}")
        
        return None
    
    def _parse_gps_coordinate(self, coord_tuple, ref) -> Optional[float]:
        """Parse GPS coordinate from EXIF tuple format.
        
        Args:
            coord_tuple: GPS coordinate tuple (degrees, minutes, seconds)
            ref: Reference direction (N/S for latitude, E/W for longitude)
            
        Returns:
            Decimal coordinate or None if parsing fails
        """
        if not coord_tuple or not ref:
            return None
        
        try:
            degrees = float(coord_tuple[0])
            minutes = float(coord_tuple[1]) if len(coord_tuple) > 1 else 0
            seconds = float(coord_tuple[2]) if len(coord_tuple) > 2 else 0
            
            decimal = degrees + (minutes / 60.0) + (seconds / 3600.0)
            
            if ref in ['S', 'W']:
                decimal = -decimal
                
            return decimal
        except (ValueError, TypeError, IndexError):
            return None
    
    def _create_location_aware_prompt(self, base_prompt: str, location_info: Optional[LocationInfo]) -> str:
        """Create a location-aware prompt for visual analysis.
        
        Args:
            base_prompt: Base vision analysis prompt
            location_info: Location information from GPS data
            
        Returns:
            Enhanced prompt with location context
        """
        if not location_info:
            return base_prompt
        
        location_context = location_info.get_location_context()
        
        # Enhance the prompt with location information
        enhanced_prompt = f"""{base_prompt}

LOCATION CONTEXT: This photo was taken at {location_context}. Consider this location information when describing the scene, and mention if the visual content matches or provides insights about this location."""
        
        return enhanced_prompt
    
    def _generate_enhanced_overall_summary(self, results: PeekResults) -> str:
        """Generate enhanced overall summary from both device and folder analyses.
        
        Args:
            results: Peek results with device and folder analyses
            
        Returns:
            Enhanced overall summary string
        """
        if not self.vision_client:
            return "Visual analysis not available"
        
        summary_components = []
        
        # Add device summaries if available
        if results.device_analyses:
            device_summaries = []
            for device_code, analysis in results.device_analyses.items():
                if analysis.device_summary and "Failed to generate" not in analysis.device_summary:
                    device_summaries.append(f"📷 {device_code}: {analysis.device_summary}")
            
            if device_summaries:
                summary_components.append("EQUIPMENT ANALYSIS:\n" + "\n".join(device_summaries))
        
        # Add folder summary if available
        if results.folder_summary and "Failed to generate" not in results.folder_summary:
            summary_components.append(f"FOLDER ORGANIZATION:\n{results.folder_summary}")
        
        if not summary_components:
            return "No analysis summaries available"
        
        # Create comprehensive summary prompt
        comprehensive_prompt = f"""Based on this comprehensive photography analysis that includes both equipment usage and folder organization, provide a 4-5 sentence executive summary of this photo collection:

{chr(10).join(summary_components)}

Synthesize the equipment usage patterns with the folder organization to describe the overall photography collection, its scope, themes, and how it's organized."""
        
        try:
            comprehensive_response = self.vision_client.generate_text_summary(comprehensive_prompt)
            return comprehensive_response
        except Exception as e:
            return f"Failed to generate comprehensive summary: {str(e)}"
    
    def _generate_overall_summary(self, results: PeekResults) -> str:
        """Generate overall summary from all device analyses.
        
        Args:
            results: Peek results with device analyses
            
        Returns:
            Overall summary string
        """
        if not self.vision_client:
            return "Visual analysis not available"
        
        # Collect device summaries
        device_summaries = []
        for device_code, analysis in results.device_analyses.items():
            if analysis.device_summary and "Failed to generate" not in analysis.device_summary:
                device_summaries.append(f"{device_code}: {analysis.device_summary}")
        
        if not device_summaries:
            return "No device summaries available"
        
        # Create overall summary prompt
        overall_prompt = f"""Based on these photography session summaries from different cameras/devices, provide a 2-3 sentence overall summary:

{chr(10).join(device_summaries)}

Highlight the overall theme, variety of equipment used, and general photography style."""
        
        try:
            overall_response = self.vision_client.generate_text_summary(overall_prompt)
            return overall_response
        except Exception as e:
            return f"Failed to generate overall summary: {str(e)}"