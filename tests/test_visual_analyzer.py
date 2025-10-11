"""Tests for visual content analyzer."""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from io import BytesIO

from photo_ingest.visual_analyzer import (
    ImageProcessor, VisualContentAnalyzer, ProcessedImage,
    ImageAnalysis, DeviceAnalysis, PeekResults, PeekProcessingStats
)
from photo_ingest.llm_client import ImageAnalysisResult
from photo_ingest.config import PeekConfig, LLMConfig


class TestImageProcessor:
    """Test image processor functionality."""
    
    def test_init_without_pillow(self):
        """Test initialization without Pillow."""
        with patch('photo_ingest.visual_analyzer.PILLOW_AVAILABLE', False):
            with pytest.raises(ImportError, match="Pillow is required"):
                ImageProcessor()
    
    @patch('photo_ingest.visual_analyzer.PILLOW_AVAILABLE', True)
    def test_init_with_pillow(self):
        """Test successful initialization."""
        processor = ImageProcessor(max_resolution=1024)
        assert processor.max_resolution == 1024
    
    @patch('photo_ingest.visual_analyzer.PILLOW_AVAILABLE', True)
    @patch('photo_ingest.visual_analyzer.Image')
    def test_prepare_for_analysis_jpeg(self, mock_image):
        """Test preparing JPEG image for analysis."""
        # Mock PIL Image
        mock_img = Mock()
        mock_img.mode = 'RGB'
        mock_img.width = 2000
        mock_img.height = 1500
        mock_img.size = (2000, 1500)  # Add size property
        
        # Mock downscaled image
        mock_downscaled = Mock()
        mock_downscaled.width = 1024
        mock_downscaled.height = 768
        mock_downscaled.save = Mock()  # Mock save method
        
        mock_img.resize.return_value = mock_downscaled
        mock_image.open.return_value.__enter__.return_value = mock_img
        mock_image.Resampling.LANCZOS = 1  # Mock the resampling constant
        
        # Mock save to buffer
        mock_buffer = Mock()
        mock_buffer.getvalue.return_value = b'fake_jpeg_data'
        
        with patch('photo_ingest.visual_analyzer.BytesIO', return_value=mock_buffer):
            processor = ImageProcessor(max_resolution=1024)
            result = processor.prepare_for_analysis(Path('test.jpg'))
        
        assert result is not None
        assert result.format == "jpeg"
        assert result.resolution == (1024, 768)
        assert result.file_size > 0
    
    @patch('photo_ingest.visual_analyzer.PILLOW_AVAILABLE', True)
    @patch('photo_ingest.visual_analyzer.Image')
    def test_prepare_for_analysis_raw_file(self, mock_image):
        """Test preparing RAW file for analysis."""
        processor = ImageProcessor()
        
        # Mock extract_raw_preview to return None (no preview)
        with patch.object(processor, 'extract_raw_preview', return_value=None):
            # Mock regular image processing
            mock_img = Mock()
            mock_img.mode = 'RGB'
            mock_img.width = 1024
            mock_img.height = 768
            mock_image.open.return_value.__enter__.return_value = mock_img
            
            with patch('photo_ingest.visual_analyzer.BytesIO'):
                result = processor.prepare_for_analysis(Path('test.nef'))
        
        # Should fall back to regular processing
        mock_image.open.assert_called_once()
    
    @patch('photo_ingest.visual_analyzer.PILLOW_AVAILABLE', True)
    def test_is_raw_file(self):
        """Test RAW file detection."""
        processor = ImageProcessor()
        
        assert processor._is_raw_file(Path('test.nef'))
        assert processor._is_raw_file(Path('test.cr3'))
        assert processor._is_raw_file(Path('test.NEF'))  # Case insensitive
        assert not processor._is_raw_file(Path('test.jpg'))
        assert not processor._is_raw_file(Path('test.png'))
    
    @patch('photo_ingest.visual_analyzer.PILLOW_AVAILABLE', True)
    @patch('photo_ingest.visual_analyzer.Image')
    def test_downscale_image(self, mock_image):
        """Test image downscaling."""
        processor = ImageProcessor(max_resolution=1024)
        
        # Mock large image
        mock_img = Mock()
        mock_img.size = (2000, 1500)
        mock_downscaled = Mock()
        mock_img.resize.return_value = mock_downscaled
        
        result = processor.downscale_image(mock_img, 1024)
        
        # Should resize to maintain aspect ratio
        mock_img.resize.assert_called_once_with((1024, 768), mock_image.Resampling.LANCZOS)
        assert result == mock_downscaled
    
    @patch('photo_ingest.visual_analyzer.PILLOW_AVAILABLE', True)
    @patch('photo_ingest.visual_analyzer.Image')
    def test_downscale_image_no_resize_needed(self, mock_image):
        """Test downscaling when image is already small enough."""
        processor = ImageProcessor(max_resolution=1024)
        
        # Mock small image
        mock_img = Mock()
        mock_img.size = (800, 600)
        
        result = processor.downscale_image(mock_img, 1024)
        
        # Should not resize
        mock_img.resize.assert_not_called()
        assert result == mock_img


class TestVisualContentAnalyzer:
    """Test visual content analyzer."""
    
    def test_init(self):
        """Test analyzer initialization."""
        peek_config = PeekConfig(sample_count=3, max_resolution=512)
        llm_config = LLMConfig(enabled=True)
        
        with patch('photo_ingest.visual_analyzer.VisionLLMClient') as mock_client:
            analyzer = VisualContentAnalyzer(peek_config, llm_config)
        
        assert analyzer.config == peek_config
        assert analyzer.llm_config == llm_config
        assert analyzer.vision_client is not None
    
    def test_init_llm_disabled(self):
        """Test analyzer initialization with LLM disabled."""
        peek_config = PeekConfig()
        llm_config = LLMConfig(enabled=False)
        
        analyzer = VisualContentAnalyzer(peek_config, llm_config)
        assert analyzer.vision_client is None
    
    def test_sample_images(self):
        """Test image sampling."""
        peek_config = PeekConfig(
            sample_count=3,
            supported_formats=["jpg", "jpeg", "nef"]
        )
        llm_config = LLMConfig(enabled=False)
        
        analyzer = VisualContentAnalyzer(peek_config, llm_config)
        
        files = [
            Path('photo1.jpg'),
            Path('photo2.nef'),
            Path('photo3.jpg'),
            Path('photo4.png'),  # Not supported
            Path('photo5.jpeg'),
            Path('video.mp4')    # Not supported
        ]
        
        # Mock random.sample to return predictable results
        with patch('photo_ingest.visual_analyzer.random.sample') as mock_sample:
            mock_sample.return_value = [Path('photo1.jpg'), Path('photo2.nef'), Path('photo5.jpeg')]
            
            result = analyzer._sample_images(files, 3)
        
        # Should only consider supported formats
        expected_supported = [Path('photo1.jpg'), Path('photo2.nef'), Path('photo3.jpg'), Path('photo5.jpeg')]
        mock_sample.assert_called_once_with(expected_supported, 3)
        assert len(result) == 3
    
    def test_sample_images_no_supported_formats(self):
        """Test sampling when no supported formats are found."""
        peek_config = PeekConfig(supported_formats=["jpg"])
        llm_config = LLMConfig(enabled=False)
        
        analyzer = VisualContentAnalyzer(peek_config, llm_config)
        
        files = [Path('video.mp4'), Path('document.pdf')]
        result = analyzer._sample_images(files, 3)
        
        assert result == []
    
    @patch('photo_ingest.visual_analyzer.time.time')
    def test_analyze_directory_no_visual_analysis(self, mock_time):
        """Test directory analysis without visual analysis."""
        mock_time.side_effect = [0.0, 1.0]  # Start and end times
        
        peek_config = PeekConfig(include_visual_analysis=False)
        llm_config = LLMConfig(enabled=False)
        
        analyzer = VisualContentAnalyzer(peek_config, llm_config)
        
        device_groups = {
            'Camera1': [Path('photo1.jpg'), Path('photo2.jpg')],
            'Camera2': [Path('photo3.nef')]
        }
        
        with patch.object(analyzer, '_sample_images') as mock_sample:
            mock_sample.side_effect = [
                [Path('photo1.jpg')],  # Camera1 sample
                [Path('photo3.nef')]   # Camera2 sample
            ]
            
            result = analyzer.analyze_directory(device_groups)
        
        assert isinstance(result, PeekResults)
        assert len(result.device_analyses) == 2
        assert 'Camera1' in result.device_analyses
        assert 'Camera2' in result.device_analyses
        
        # Check processing stats
        assert result.processing_stats.total_files_scanned == 3
        assert result.processing_stats.images_sampled == 2
        assert result.processing_stats.llm_calls_made == 0
        assert result.processing_stats.processing_time == 1.0
    
    @patch('photo_ingest.visual_analyzer.time.time')
    def test_analyze_directory_with_visual_analysis(self, mock_time):
        """Test directory analysis with visual analysis."""
        mock_time.side_effect = [0.0, 0.5, 1.0, 2.0]  # Various time points
        
        peek_config = PeekConfig(include_visual_analysis=True, sample_count=1)
        llm_config = LLMConfig(enabled=True)
        
        # Mock vision client
        mock_vision_client = Mock()
        mock_analysis_result = ImageAnalysisResult(
            visual_description="A beautiful landscape photo",
            confidence=0.8,
            processing_time=1.0
        )
        mock_vision_client.analyze_image.return_value = mock_analysis_result
        
        with patch('photo_ingest.visual_analyzer.VisionLLMClient', return_value=mock_vision_client):
            analyzer = VisualContentAnalyzer(peek_config, llm_config)
        
        device_groups = {'Camera1': [Path('photo1.jpg')]}
        
        # Mock image processor
        mock_processed_image = ProcessedImage(
            image_data=b'fake_data',
            format='jpeg',
            resolution=(1024, 768),
            file_size=1024
        )
        
        with patch.object(analyzer, '_sample_images', return_value=[Path('photo1.jpg')]):
            with patch.object(analyzer.image_processor, 'prepare_for_analysis', return_value=mock_processed_image):
                with patch.object(analyzer, '_generate_device_summary', return_value="Test device summary"):
                    with patch.object(analyzer, '_generate_overall_summary', return_value="Test overall summary"):
                        result = analyzer.analyze_directory(device_groups)
        
        assert len(result.device_analyses) == 1
        device_analysis = result.device_analyses['Camera1']
        assert len(device_analysis.visual_analyses) == 1
        
        visual_analysis = device_analysis.visual_analyses[0]
        assert visual_analysis.visual_description == "A beautiful landscape photo"
        assert visual_analysis.confidence == 0.8
        
        # Should have made LLM calls
        assert result.processing_stats.llm_calls_made > 0
        
        # Verify vision client was called
        mock_vision_client.analyze_image.assert_called_once()


class TestDataClasses:
    """Test data classes."""
    
    def test_processed_image(self):
        """Test ProcessedImage data class."""
        image = ProcessedImage(
            image_data=b'test_data',
            format='jpeg',
            resolution=(1024, 768),
            file_size=1024
        )
        
        assert image.image_data == b'test_data'
        assert image.format == 'jpeg'
        assert image.resolution == (1024, 768)
        assert image.file_size == 1024
    
    def test_image_analysis(self):
        """Test ImageAnalysis data class."""
        analysis = ImageAnalysis(
            file_path=Path('test.jpg'),
            visual_description='A test image',
            confidence=0.9,
            processing_time=1.5,
            error=None
        )
        
        assert analysis.file_path == Path('test.jpg')
        assert analysis.visual_description == 'A test image'
        assert analysis.confidence == 0.9
        assert analysis.processing_time == 1.5
        assert analysis.error is None
    
    def test_device_analysis(self):
        """Test DeviceAnalysis data class."""
        analysis = DeviceAnalysis(
            device_code='Camera1',
            sampled_files=[Path('test1.jpg'), Path('test2.jpg')],
            visual_analyses=[],
            device_summary='Test summary'
        )
        
        assert analysis.device_code == 'Camera1'
        assert len(analysis.sampled_files) == 2
        assert analysis.device_summary == 'Test summary'
    
    def test_peek_results(self):
        """Test PeekResults data class."""
        stats = PeekProcessingStats(
            total_files_scanned=10,
            images_sampled=5,
            processing_time=2.5,
            llm_calls_made=3,
            errors_encountered=1
        )
        
        results = PeekResults(
            device_analyses={},
            overall_summary='Overall test summary',
            processing_stats=stats
        )
        
        assert results.overall_summary == 'Overall test summary'
        assert results.processing_stats.total_files_scanned == 10
        assert results.processing_stats.images_sampled == 5