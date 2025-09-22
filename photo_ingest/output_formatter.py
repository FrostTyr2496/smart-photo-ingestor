"""Output formatting for analysis results."""

import json
from typing import Dict, Any, List, Tuple
from .analyzer import AnalysisResult, format_size


class AnalysisFormatter:
    """Formats analysis results for different output modes."""
    
    @staticmethod
    def format_human_readable(result: AnalysisResult, detailed: bool = True) -> str:
        """Format analysis result as human-readable text."""
        lines = []
        
        # Header
        lines.append("PHOTO ANALYSIS RESULTS")
        lines.append("=" * 60)
        lines.append("")
        
        # Summary
        lines.append("SUMMARY")
        lines.append("-" * 40)
        lines.append(f"Total files: {result.total_files}")
        lines.append(f"Image files analyzed: {result.image_files_analyzed}")
        lines.append(f"Total size: {format_size(result.total_size)}")
        lines.append(f"Scan time: {result.scan_time:.1f}s")
        lines.append(f"EXIF extraction time: {result.exif_time:.1f}s")
        
        if result.date_range:
            lines.append(f"Date range: {result.date_range[0]} to {result.date_range[1]}")
        
        if result.files_with_gps > 0:
            percentage = (result.files_with_gps / result.image_files_analyzed) * 100
            lines.append(f"Files with GPS: {result.files_with_gps} ({percentage:.1f}%)")
        
        if result.files_with_artist > 0:
            lines.append(f"Files with artist info: {result.files_with_artist}")
        
        if result.files_with_copyright > 0:
            lines.append(f"Files with copyright: {result.files_with_copyright}")
        
        lines.append("")
        
        # Equipment
        if result.cameras:
            lines.append("CAMERAS")
            lines.append("-" * 40)
            for camera, count in sorted(result.cameras.items(), key=lambda x: x[1], reverse=True):
                percentage = (count / result.image_files_analyzed) * 100
                lines.append(f"{camera:<50} {count:>4} ({percentage:.1f}%)")
            lines.append("")
        
        if result.lenses:
            lines.append("LENSES")
            lines.append("-" * 40)
            for lens, count in sorted(result.lenses.items(), key=lambda x: x[1], reverse=True):
                percentage = (count / result.image_files_analyzed) * 100
                lines.append(f"{lens:<50} {count:>4} ({percentage:.1f}%)")
            lines.append("")
        
        if detailed and result.lens_makes:
            lines.append("LENS MANUFACTURERS")
            lines.append("-" * 40)
            for make, count in sorted(result.lens_makes.items(), key=lambda x: x[1], reverse=True):
                percentage = (count / result.image_files_analyzed) * 100
                lines.append(f"{make:<30} {count:>4} ({percentage:.1f}%)")
            lines.append("")
        
        # Camera settings
        lines.append("CAMERA SETTINGS")
        lines.append("-" * 40)
        
        if result.aperture_range:
            lines.append(f"Aperture range: f/{result.aperture_range[0]:.1f} - f/{result.aperture_range[1]:.1f}")
            if result.most_used_apertures:
                aperture_list = [f"{ap} ({count})" for ap, count in result.most_used_apertures]
                lines.append(f"Most used: {', '.join(aperture_list)}")
        
        if result.iso_range:
            lines.append(f"ISO range: {result.iso_range[0]} - {result.iso_range[1]}")
            if result.most_used_isos:
                iso_list = [f"{iso} ({count})" for iso, count in result.most_used_isos]
                lines.append(f"Most used: {', '.join(iso_list)}")
        
        if result.focal_length_range:
            lines.append(f"Focal length range: {result.focal_length_range[0]:.0f}mm - {result.focal_length_range[1]:.0f}mm")
            if result.most_used_focal_lengths:
                fl_list = [f"{fl} ({count})" for fl, count in result.most_used_focal_lengths]
                lines.append(f"Most used: {', '.join(fl_list)}")
        
        if result.common_shutter_speeds:
            speed_list = [f"{speed} ({count})" for speed, count in result.common_shutter_speeds]
            lines.append(f"Common shutter speeds: {', '.join(speed_list)}")
        
        if detailed and result.exposure_compensation:
            comp_counts = {}
            for comp in result.exposure_compensation:
                comp_counts[comp] = comp_counts.get(comp, 0) + 1
            if comp_counts:
                comp_list = [f"{comp} ({count})" for comp, count in sorted(comp_counts.items(), key=lambda x: x[1], reverse=True)[:5]]
                lines.append(f"Exposure compensation: {', '.join(comp_list)}")
        
        lines.append("")
        
        # Detailed shooting modes (only if detailed analysis)
        if detailed:
            if result.exposure_programs:
                lines.append("SHOOTING MODES")
                lines.append("-" * 40)
                for mode, count in sorted(result.exposure_programs.items(), key=lambda x: x[1], reverse=True):
                    percentage = (count / result.image_files_analyzed) * 100
                    lines.append(f"{mode:<30} {count:>4} ({percentage:.1f}%)")
                lines.append("")
            
            if result.metering_modes:
                lines.append("METERING MODES")
                lines.append("-" * 40)
                for mode, count in sorted(result.metering_modes.items(), key=lambda x: x[1], reverse=True):
                    percentage = (count / result.image_files_analyzed) * 100
                    lines.append(f"{mode:<30} {count:>4} ({percentage:.1f}%)")
                lines.append("")
            
            if result.flash_usage:
                lines.append("FLASH USAGE")
                lines.append("-" * 40)
                for flash, count in sorted(result.flash_usage.items(), key=lambda x: x[1], reverse=True):
                    percentage = (count / result.image_files_analyzed) * 100
                    lines.append(f"{flash:<30} {count:>4} ({percentage:.1f}%)")
                lines.append("")
        
        # Image properties
        if detailed and result.resolutions:
            lines.append("IMAGE RESOLUTIONS")
            lines.append("-" * 40)
            
            # Sort by resolution size (width * height) descending
            def resolution_size(resolution_str):
                try:
                    width, height = map(int, resolution_str.split('x'))
                    return width * height
                except:
                    return 0
            
            sorted_resolutions = sorted(result.resolutions.items(), key=lambda x: resolution_size(x[0]), reverse=True)
            
            # Calculate total images with resolution data
            total_images_with_resolution = sum(result.resolutions.values())
            
            for resolution, count in sorted_resolutions[:10]:
                # Use total images with resolution data for percentage calculation
                percentage = (count / total_images_with_resolution) * 100 if total_images_with_resolution > 0 else 0
                
                # Calculate aspect ratio inline
                try:
                    width, height = map(int, resolution.split('x'))
                    def gcd(a, b):
                        return gcd(b, a % b) if b else a
                    divisor = gcd(width, height)
                    aspect_ratio = f"{width//divisor}:{height//divisor}"
                    
                    # Format with proper spacing
                    lines.append(f"{resolution:<16} ({aspect_ratio:<7}) {count:>5} ({percentage:.1f}%)")
                except:
                    lines.append(f"{resolution:<16} {count:>5} ({percentage:.1f}%)")
            lines.append("")
        

        
        # File types
        lines.append("FILE TYPES")
        lines.append("-" * 40)
        for file_type in ['raw', 'jpeg', 'video']:
            count = result.files_by_type.get(file_type, 0)
            size = result.size_by_type.get(file_type, 0)
            if count > 0:
                lines.append(f"{file_type.upper():<8} {count:>4} files ({format_size(size)})")
        
        lines.append("")
        lines.append("✅ Analysis complete!")
        
        return "\n".join(lines)
    
    @staticmethod
    def format_json(result: AnalysisResult) -> str:
        """Format analysis result as JSON."""
        data = result.to_dict()
        
        # Convert any datetime objects to strings
        def serialize_datetime(obj):
            if hasattr(obj, 'isoformat'):
                return obj.isoformat()
            raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
        
        return json.dumps(data, indent=2, default=serialize_datetime)
    
    @staticmethod
    def format_summary(result: AnalysisResult) -> str:
        """Format a brief summary of analysis results."""
        lines = []
        
        lines.append(f"📁 {result.total_files} files ({format_size(result.total_size)})")
        lines.append(f"📸 {result.image_files_analyzed} images analyzed")
        
        if result.date_range:
            lines.append(f"📅 {result.date_range[0]} to {result.date_range[1]}")
        
        # Top camera
        if result.cameras:
            top_camera = max(result.cameras.items(), key=lambda x: x[1])
            lines.append(f"📷 {top_camera[0]} ({top_camera[1]} photos)")
        
        # Top lens
        if result.lenses:
            top_lens = max(result.lenses.items(), key=lambda x: x[1])
            lines.append(f"🔍 {top_lens[0]} ({top_lens[1]} photos)")
        
        # Settings ranges
        if result.aperture_range:
            lines.append(f"🔆 f/{result.aperture_range[0]:.1f} - f/{result.aperture_range[1]:.1f}")
        
        if result.iso_range:
            lines.append(f"📊 ISO {result.iso_range[0]} - {result.iso_range[1]}")
        
        if result.focal_length_range:
            lines.append(f"📏 {result.focal_length_range[0]:.0f}mm - {result.focal_length_range[1]:.0f}mm")
        
        return " | ".join(lines)