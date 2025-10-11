"""Tests for enhanced analyze command."""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from photo_ingest.analyze_command import AnalyzeCommand, EnhancedAnalysisResult
from photo_ingest.analyzer import AnalysisResult
from photo_ingest.visual_analyzer import PeekResults, PeekProcessingStats
from photo_ingest.config import IngestConfig, FileTypes, PerformanceConfig, DeviceMapping, LLMConfig, PeekConfig
from photo_ingest.file_scanner import ScanResult, FileInfo


class TestAnalyzeCommand:
    """Test enhanced analyze command functionality."""
    
    def test_init_minimal(self):
        """Test initialization with minimal configuration."""
        command = AnalyzeCommand()
        
        assert command.config is None
        assert isinstance(command.file_types, FileTypes)
        assert isinstance(command.performance_config, PerformanceConfig)
        assert command.device_detector is None
        assert command.visual_analyzer is None
    
    def test_init_with_full_config(self):
        """Test initialization with full configuration."""
        config = IngestConfig(
            archive_root="/test/archive",
            devices=DeviceMapping(
                mappings={"Camera1": "C1"},
                exif_identifiers={"C1": {"Make": "Test", "Model": "Camera1"}}
            ),
            llm=LLMConfig(enabled=True),
            peek=PeekConfig(enabled=True, include_visual_analysis=True)
        )
        
        with patch('photo_ingest.analyze_command.DeviceDetector') as mock_detector:
            with patch('photo_ingest.analyze_command.VisualContentAnalyzer') as mock_analyzer:
                command = AnalyzeCommand(config=config)
        
        assert command.config == config
        assert command.device_detector is not None
        assert command.visual_analyzer is not None
        mock_detector.assert_called_once_with(config.devices)
        mock_analyzer.assert_called_once_with(config.peek, config.llm)
    
    def test_init_with_config_llm_disabled(self):
        """Test initialization with LLM disabled."""
        config = IngestConfig(
            archive_root="/test/archive",
            llm=LLMConfig(enabled=False),
            peek=PeekConfig(enabled=True)
        )
        
        command = AnalyzeCommand(config=config)
        assert command.visual_analyzer is None
    
    def test_determine_processing_mode_exif_only(self):
        """Test processing mode determination for EXIF-only."""
        command = AnalyzeCommand()
        
        # Explicit EXIF-only flag
        mode = command._determine_processing_mode(peek_mode=False, exif_only=True)
        assert mode == "exif_only"
        
        # EXIF-only overrides peek
        mode = command._determine_processing_mode(peek_mode=True, exif_only=True)
        assert mode == "exif_only"
    
    def test_determine_processing_mode_peek(self):
        """Test processing mode determination for peek mode."""
        # Mock visual analyzer
        command = AnalyzeCommand()
        command.visual_analyzer = Mock()
        
        mode = command._determine_processing_mode(peek_mode=True, exif_only=False)
        assert mode == "peek"
    
    def test_determine_processing_mode_peek_no_analyzer(self):
        """Test peek mode fallback when no visual analyzer available."""
        command = AnalyzeCommand()
        command.visual_analyzer = None
        
        mode = command._determine_processing_mode(peek_mode=True, exif_only=False)
        assert mode == "exif_only"
    
    def test_determine_processing_mode_auto_visual(self):
        """Test automatic visual analysis mode."""
        config = IngestConfig(
            archive_root="/test",
            peek=PeekConfig(include_visual_analysis=True),
            llm=LLMConfig(enabled=True)
        )
        
        with patch('photo_ingest.analyze_command.VisualContentAnalyzer') as mock_analyzer:
            command = AnalyzeCommand(config=config)
        
        mode = command._determine_processing_mode(peek_mode=False, exif_only=False)
        assert mode == "exif_with_visual"
    
    @patch('photo_ingest.analyze_command.time.time')
    def test_analyze_directory_exif_only(self, mock_time):
        """Test directory analysis in EXIF-only mode."""
        mock_time.side_effect = [0.0, 2.5]  # Start and end times
        
        command = AnalyzeCommand()
        
        # Mock analyzer result
        mock_exif_result = AnalysisResult(
            total_files=10,
            total_size=1000000,
            image_files_analyzed=8,
            scan_time=1.0,
            exif_time=1.0,
            date_range=None,
            files_by_type={'jpeg': 8, 'raw': 2},
            size_by_type={'jpeg': 800000, 'raw': 200000},
            files_by_extension={'jpg': 8, 'nef': 2},
            cameras={'Camera1': 10},
            lenses={'Lens1': 10},
            lens_makes={'Make1': 10},
            aperture_range=(2.8, 5.6),
            iso_range=(100, 800),
            focal_length_range=(24, 70),
            most_used_apertures=[('f/4.0', 5)],
            most_used_isos=[('ISO400', 3)],
            most_used_focal_lengths=[('50mm', 4)],
            common_shutter_speeds=[('1/125', 3)],
            exposure_programs={},
            metering_modes={},
            flash_usage={},
            white_balance={},
            exposure_compensation=[],
            resolutions={'1920x1080': 10},
            color_spaces={},
            files_with_gps=2,
            files_with_artist=0,
            files_with_copyright=0,
            software_used={}
        )
        
        with patch.object(command.analyzer, 'analyze_directory', return_value=mock_exif_result):
            with patch.object(command, '_create_device_breakdown', return_value={}):
                result = command.analyze_directory(
                    Path('/test/dir'),
                    peek_mode=False,
                    exif_only=True,
                    samples=5
                )
        
        assert isinstance(result, EnhancedAnalysisResult)
        assert result.exif_analysis == mock_exif_result
        assert result.visual_analysis is None
        assert result.processing_mode == "exif_only"
        assert result.total_processing_time == 2.5
    
    @patch('photo_ingest.analyze_command.time.time')
    def test_analyze_directory_with_visual_analysis(self, mock_time):
        """Test directory analysis with visual analysis."""
        mock_time.side_effect = [0.0, 3.0]
        
        # Create config with visual analysis enabled
        config = IngestConfig(
            archive_root="/test",
            peek=PeekConfig(enabled=True, include_visual_analysis=True, sample_count=3),
            llm=LLMConfig(enabled=True)
        )
        
        with patch('photo_ingest.analyze_command.VisualContentAnalyzer') as mock_va_class:
            mock_visual_analyzer = Mock()
            mock_va_class.return_value = mock_visual_analyzer
            
            command = AnalyzeCommand(config=config)
        
        # Mock EXIF analysis result
        mock_exif_result = Mock()
        
        # Mock visual analysis result
        mock_visual_result = PeekResults(
            device_analyses={'Camera1': Mock()},
            overall_summary="Test visual summary",
            processing_stats=PeekProcessingStats(
                total_files_scanned=10,
                images_sampled=3,
                processing_time=1.5,
                llm_calls_made=3,
                errors_encountered=0
            )
        )
        
        with patch.object(command.analyzer, 'analyze_directory', return_value=mock_exif_result):
            with patch.object(command, '_create_device_breakdown', return_value={}):
                with patch.object(command, '_group_files_by_device', return_value={'Camera1': [Path('test.jpg')]}):
                    mock_visual_analyzer.analyze_directory.return_value = mock_visual_result
                    
                    result = command.analyze_directory(
                        Path('/test/dir'),
                        peek_mode=True,
                        exif_only=False,
                        samples=5
                    )
        
        assert result.visual_analysis == mock_visual_result
        assert result.processing_mode == "peek"
        
        # Verify sample count was temporarily updated
        mock_visual_analyzer.analyze_directory.assert_called_once()
    
    def test_create_device_breakdown_no_detector(self):
        """Test device breakdown creation without device detector."""
        command = AnalyzeCommand()
        command.device_detector = None
        
        mock_exif_result = Mock()
        result = command._create_device_breakdown(Path('/test'), mock_exif_result)
        
        assert result == {}
    
    def test_create_device_breakdown_with_detector(self):
        """Test device breakdown creation with device detector."""
        command = AnalyzeCommand()
        
        # Mock device detector
        mock_detector = Mock()
        mock_detector.detect_device.return_value = "Camera1"
        command.device_detector = mock_detector
        
        # Mock scanner result
        mock_file_info = FileInfo(
            path=Path('/test/photo.jpg'),
            size=1000000,
            modified_time=datetime.now(),
            file_type='jpeg',
            extension='jpg'
        )
        
        mock_scan_result = ScanResult(
            files=[mock_file_info],
            total_files=1,
            total_size=1000000,
            scan_time=1.0,
            errors=[],
            directories_scanned=1
        )
        
        with patch.object(command.scanner, 'scan_directory', return_value=mock_scan_result):
            # Mock EXIF extraction
            mock_metadata = {
                'Make': 'Test',
                'Model': 'Camera1',
                'LensModel': 'Test Lens',
                'DateTime': datetime(2024, 1, 15, 12, 0, 0),
                'ISO': 400,
                'FNumber': 4.0
            }
            
            with patch.object(command.analyzer, '_extract_basic_exif', return_value=mock_metadata):
                result = command._create_device_breakdown(Path('/test'), Mock())
        
        assert 'Camera1' in result
        device_stats = result['Camera1']
        assert device_stats['file_count'] == 1
        assert device_stats['total_size'] == 1000000
        assert 'Test Camera1' in device_stats['cameras']
        assert 'Test Lens' in device_stats['lenses']
        assert device_stats['iso_range'] == [400, 400]
        assert device_stats['aperture_range'] == [4.0, 4.0]
    
    def test_group_files_by_device_no_detector(self):
        """Test file grouping without device detector."""
        command = AnalyzeCommand()
        command.device_detector = None
        
        # Mock scanner result
        mock_files = [
            FileInfo(Path('/test/photo1.jpg'), 1000, datetime.now(), 'jpeg', 'jpg'),
            FileInfo(Path('/test/photo2.nef'), 2000, datetime.now(), 'raw', 'nef')
        ]
        
        mock_scan_result = ScanResult(
            files=mock_files,
            total_files=2,
            total_size=3000,
            scan_time=1.0,
            errors=[],
            directories_scanned=1
        )
        
        with patch.object(command.scanner, 'scan_directory', return_value=mock_scan_result):
            result = command._group_files_by_device(Path('/test'), 5)
        
        assert 'Unknown' in result
        assert len(result['Unknown']) == 2
    
    def test_group_files_by_device_with_detector(self):
        """Test file grouping with device detector."""
        command = AnalyzeCommand()
        
        # Mock device detector
        mock_detector = Mock()
        mock_detector.detect_device.side_effect = ['Camera1', 'Camera2']
        command.device_detector = mock_detector
        
        # Mock scanner and EXIF extraction
        mock_files = [
            FileInfo(Path('/test/photo1.jpg'), 1000, datetime.now(), 'jpeg', 'jpg'),
            FileInfo(Path('/test/photo2.jpg'), 1000, datetime.now(), 'jpeg', 'jpg')
        ]
        
        mock_scan_result = ScanResult(
            files=mock_files,
            total_files=2,
            total_size=2000,
            scan_time=1.0,
            errors=[],
            directories_scanned=1
        )
        
        with patch.object(command.scanner, 'scan_directory', return_value=mock_scan_result):
            with patch.object(command.analyzer, '_extract_basic_exif', return_value={'Make': 'Test'}):
                result = command._group_files_by_device(Path('/test'), 5)
        
        assert 'Camera1' in result
        assert 'Camera2' in result
        assert len(result['Camera1']) == 1
        assert len(result['Camera2']) == 1
    
    def test_get_supported_formats(self):
        """Test getting supported file formats."""
        file_types = FileTypes(
            raw=['nef', 'cr3'],
            jpeg=['jpg', 'heic'],
            video=['mp4']
        )
        
        command = AnalyzeCommand(file_types=file_types)
        formats = command.get_supported_formats()
        
        expected = ['nef', 'cr3', 'jpg', 'heic', 'mp4']
        assert formats == expected
    
    def test_is_visual_analysis_available(self):
        """Test checking visual analysis availability."""
        # Without visual analyzer
        command = AnalyzeCommand()
        assert not command.is_visual_analysis_available()
        
        # With visual analyzer
        command.visual_analyzer = Mock()
        assert command.is_visual_analysis_available()


class TestEnhancedAnalysisResult:
    """Test enhanced analysis result data class."""
    
    def test_creation(self):
        """Test creating enhanced analysis result."""
        mock_exif_result = Mock()
        mock_visual_result = Mock()
        
        result = EnhancedAnalysisResult(
            exif_analysis=mock_exif_result,
            visual_analysis=mock_visual_result,
            device_breakdown={'Camera1': {'file_count': 10}},
            processing_mode="peek",
            total_processing_time=5.0
        )
        
        assert result.exif_analysis == mock_exif_result
        assert result.visual_analysis == mock_visual_result
        assert result.device_breakdown['Camera1']['file_count'] == 10
        assert result.processing_mode == "peek"
        assert result.total_processing_time == 5.0
    
    def test_to_dict_exif_only(self):
        """Test converting to dictionary for EXIF-only analysis."""
        mock_exif_result = Mock()
        mock_exif_result.to_dict.return_value = {'test': 'exif_data'}
        
        result = EnhancedAnalysisResult(
            exif_analysis=mock_exif_result,
            visual_analysis=None,
            device_breakdown={'Camera1': {'file_count': 5}},
            processing_mode="exif_only",
            total_processing_time=2.0
        )
        
        data = result.to_dict()
        
        assert data['exif_analysis'] == {'test': 'exif_data'}
        assert data['device_breakdown'] == {'Camera1': {'file_count': 5}}
        assert data['processing_mode'] == "exif_only"
        assert data['total_processing_time'] == 2.0
        assert 'visual_analysis' not in data
    
    def test_to_dict_with_visual_analysis(self):
        """Test converting to dictionary with visual analysis."""
        # Mock EXIF result
        mock_exif_result = Mock()
        mock_exif_result.to_dict.return_value = {'exif': 'data'}
        
        # Mock visual analysis components
        mock_image_analysis = Mock()
        mock_image_analysis.file_path = Path('/test/photo.jpg')
        mock_image_analysis.visual_description = "Test description"
        mock_image_analysis.confidence = 0.8
        mock_image_analysis.processing_time = 1.0
        mock_image_analysis.error = None
        
        mock_device_analysis = Mock()
        mock_device_analysis.device_code = "Camera1"
        mock_device_analysis.sampled_files = [Path('/test/photo.jpg')]
        mock_device_analysis.visual_analyses = [mock_image_analysis]
        mock_device_analysis.device_summary = "Device summary"
        
        mock_processing_stats = PeekProcessingStats(
            total_files_scanned=10,
            images_sampled=3,
            processing_time=2.0,
            llm_calls_made=3,
            errors_encountered=0
        )
        
        mock_visual_result = Mock()
        mock_visual_result.device_analyses = {'Camera1': mock_device_analysis}
        mock_visual_result.overall_summary = "Overall summary"
        mock_visual_result.processing_stats = mock_processing_stats
        
        result = EnhancedAnalysisResult(
            exif_analysis=mock_exif_result,
            visual_analysis=mock_visual_result,
            device_breakdown={},
            processing_mode="peek",
            total_processing_time=3.0
        )
        
        data = result.to_dict()
        
        assert 'visual_analysis' in data
        visual_data = data['visual_analysis']
        assert visual_data['overall_summary'] == "Overall summary"
        assert 'Camera1' in visual_data['device_analyses']
        
        device_data = visual_data['device_analyses']['Camera1']
        assert device_data['device_code'] == "Camera1"
        assert device_data['device_summary'] == "Device summary"
        assert len(device_data['visual_analyses']) == 1
        
        image_data = device_data['visual_analyses'][0]
        assert image_data['file_path'] == '/test/photo.jpg'
        assert image_data['visual_description'] == "Test description"
        assert image_data['confidence'] == 0.8