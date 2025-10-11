"""Tests for LLM client functionality."""

import pytest
import json
import base64
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

from photo_ingest.llm_client import (
    LLMClient, VisionLLMClient, OllamaProvider, ImageAnalysisResult
)
from photo_ingest.config import LLMConfig
from photo_ingest.visual_analyzer import ProcessedImage


class TestOllamaProvider:
    """Test Ollama provider implementation."""
    
    def test_init_without_requests(self):
        """Test initialization without requests library."""
        config = LLMConfig(enabled=True)
        
        with patch('photo_ingest.llm_client.REQUESTS_AVAILABLE', False):
            with pytest.raises(ImportError, match="requests library is required"):
                OllamaProvider(config)
    
    @patch('photo_ingest.llm_client.REQUESTS_AVAILABLE', True)
    @patch('photo_ingest.llm_client.requests.Session')
    def test_init_with_requests(self, mock_session):
        """Test successful initialization."""
        config = LLMConfig(enabled=True, endpoint="http://localhost:11434/api/generate")
        provider = OllamaProvider(config)
        
        assert provider.config == config
        assert provider.session is not None
        mock_session.assert_called_once()
    
    @patch('photo_ingest.llm_client.REQUESTS_AVAILABLE', True)
    @patch('photo_ingest.llm_client.requests.Session')
    def test_call_text_api_success(self, mock_session):
        """Test successful text API call."""
        config = LLMConfig(enabled=True, model="test-model")
        
        # Mock successful response
        mock_response = Mock()
        mock_response.json.return_value = {"response": "Generated text response"}
        mock_response.raise_for_status.return_value = None
        
        mock_session_instance = Mock()
        mock_session_instance.post.return_value = mock_response
        mock_session.return_value = mock_session_instance
        
        provider = OllamaProvider(config)
        result = provider.call_text_api("Test prompt")
        
        assert result == "Generated text response"
        
        # Verify API call
        mock_session_instance.post.assert_called_once()
        call_args = mock_session_instance.post.call_args
        assert call_args[0][0] == config.endpoint
        
        # Check payload
        payload = call_args[1]['json']
        assert payload['model'] == "test-model"
        assert payload['prompt'] == "Test prompt"
        assert payload['stream'] is False
    
    @patch('photo_ingest.llm_client.REQUESTS_AVAILABLE', True)
    @patch('photo_ingest.llm_client.requests.Session')
    @patch('photo_ingest.llm_client.time.sleep')
    def test_call_text_api_retry_logic(self, mock_sleep, mock_session):
        """Test retry logic on API failures."""
        config = LLMConfig(enabled=True, retry_attempts=2, retry_delay=1.0)
        
        # Mock failed responses then success
        mock_response_fail = Mock()
        mock_response_fail.raise_for_status.side_effect = Exception("API Error")
        
        mock_response_success = Mock()
        mock_response_success.json.return_value = {"response": "Success after retry"}
        mock_response_success.raise_for_status.return_value = None
        
        mock_session_instance = Mock()
        mock_session_instance.post.side_effect = [mock_response_fail, mock_response_success]
        mock_session.return_value = mock_session_instance
        
        provider = OllamaProvider(config)
        result = provider.call_text_api("Test prompt")
        
        assert result == "Success after retry"
        assert mock_session_instance.post.call_count == 2
        mock_sleep.assert_called_once_with(1.0)  # First retry delay
    
    @patch('photo_ingest.llm_client.REQUESTS_AVAILABLE', True)
    @patch('photo_ingest.llm_client.requests.Session')
    @patch('photo_ingest.llm_client.time.sleep')
    def test_call_text_api_max_retries_exceeded(self, mock_sleep, mock_session):
        """Test behavior when max retries are exceeded."""
        config = LLMConfig(enabled=True, retry_attempts=1, retry_delay=0.5)
        
        # Mock all responses to fail
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = Exception("Persistent API Error")
        
        mock_session_instance = Mock()
        mock_session_instance.post.return_value = mock_response
        mock_session.return_value = mock_session_instance
        
        provider = OllamaProvider(config)
        
        with pytest.raises(Exception, match="Ollama API call failed after 2 attempts"):
            provider.call_text_api("Test prompt")
        
        assert mock_session_instance.post.call_count == 2  # Initial + 1 retry
        mock_sleep.assert_called_once_with(0.5)
    
    @patch('photo_ingest.llm_client.REQUESTS_AVAILABLE', True)
    @patch('photo_ingest.llm_client.requests.Session')
    @patch('photo_ingest.llm_client.time.time')
    def test_call_vision_api_success(self, mock_time, mock_session):
        """Test successful vision API call."""
        mock_time.side_effect = [0.0, 1.5]  # Start and end times
        
        config = LLMConfig(enabled=True, vision_model="test-vision-model")
        
        # Mock successful response
        mock_response = Mock()
        mock_response.json.return_value = {"response": "This is a landscape photo"}
        mock_response.raise_for_status.return_value = None
        
        mock_session_instance = Mock()
        mock_session_instance.post.return_value = mock_response
        mock_session.return_value = mock_session_instance
        
        provider = OllamaProvider(config)
        
        # Create test image
        test_image = ProcessedImage(
            image_data=b'fake_image_data',
            format='jpeg',
            resolution=(1024, 768),
            file_size=1024
        )
        
        result = provider.call_vision_api(test_image, "Describe this image")
        
        assert isinstance(result, ImageAnalysisResult)
        assert result.visual_description == "This is a landscape photo"
        assert result.confidence > 0.0
        assert result.processing_time == 1.5
        assert result.error is None
        
        # Verify API call
        call_args = mock_session_instance.post.call_args
        payload = call_args[1]['json']
        assert payload['model'] == "test-vision-model"
        assert payload['prompt'] == "Describe this image"
        assert len(payload['images']) == 1
        
        # Verify image was base64 encoded
        expected_b64 = base64.b64encode(b'fake_image_data').decode('utf-8')
        assert payload['images'][0] == expected_b64
    
    @patch('photo_ingest.llm_client.REQUESTS_AVAILABLE', True)
    @patch('photo_ingest.llm_client.requests.Session')
    @patch('photo_ingest.llm_client.time.time')
    def test_call_vision_api_failure(self, mock_time, mock_session):
        """Test vision API call failure."""
        mock_time.side_effect = [0.0, 1.0]
        
        config = LLMConfig(enabled=True, retry_attempts=0)  # No retries
        
        # Mock failed response
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = Exception("Vision API Error")
        
        mock_session_instance = Mock()
        mock_session_instance.post.return_value = mock_response
        mock_session.return_value = mock_session_instance
        
        provider = OllamaProvider(config)
        
        test_image = ProcessedImage(image_data=b'test', format='jpeg')
        result = provider.call_vision_api(test_image, "Test prompt")
        
        assert isinstance(result, ImageAnalysisResult)
        assert result.visual_description == ""
        assert result.confidence == 0.0
        assert result.processing_time == 1.0
        assert "Vision API call failed" in result.error
    
    def test_calculate_confidence(self):
        """Test confidence calculation logic."""
        config = LLMConfig(enabled=True)
        
        with patch('photo_ingest.llm_client.REQUESTS_AVAILABLE', True):
            with patch('photo_ingest.llm_client.requests.Session'):
                provider = OllamaProvider(config)
        
        # Empty description
        assert provider._calculate_confidence("") == 0.0
        
        # Short generic description
        confidence = provider._calculate_confidence("This photo appears to be an image.")
        assert 0.0 <= confidence <= 1.0
        
        # Longer description with photography terms
        detailed_desc = "This is a beautiful portrait photograph with excellent lighting and composition, showing a subject against a blurred background."
        confidence = provider._calculate_confidence(detailed_desc)
        assert confidence > 0.5
        
        # Very detailed description
        very_detailed = "This landscape photograph captures a stunning mountain vista during golden hour. The composition follows the rule of thirds with the horizon line positioned in the lower third. The foreground features detailed rock formations while the background shows snow-capped peaks. The lighting creates dramatic shadows and highlights that enhance the three-dimensional quality of the scene."
        confidence = provider._calculate_confidence(very_detailed)
        assert confidence > 0.7


class TestVisionLLMClient:
    """Test vision LLM client."""
    
    def test_init_enabled(self):
        """Test initialization with LLM enabled."""
        config = LLMConfig(enabled=True, provider="ollama")
        
        with patch('photo_ingest.llm_client.OllamaProvider') as mock_provider:
            client = VisionLLMClient(config)
        
        assert client.config == config
        mock_provider.assert_called_once_with(config)
    
    def test_init_disabled(self):
        """Test initialization with LLM disabled."""
        config = LLMConfig(enabled=False)
        client = VisionLLMClient(config)
        
        assert client.provider is None
    
    def test_analyze_image_no_provider(self):
        """Test image analysis when no provider is configured."""
        config = LLMConfig(enabled=False)
        client = VisionLLMClient(config)
        
        test_image = ProcessedImage(image_data=b'test', format='jpeg')
        result = client.analyze_image(test_image, "Test prompt")
        
        assert isinstance(result, ImageAnalysisResult)
        assert result.visual_description == "Vision analysis not available"
        assert result.confidence == 0.0
        assert "not configured" in result.error
    
    def test_analyze_image_with_provider(self):
        """Test image analysis with provider."""
        config = LLMConfig(enabled=True)
        
        # Mock provider
        mock_provider = Mock()
        expected_result = ImageAnalysisResult(
            visual_description="Test analysis",
            confidence=0.8,
            processing_time=1.0
        )
        mock_provider.call_vision_api.return_value = expected_result
        
        with patch('photo_ingest.llm_client.OllamaProvider', return_value=mock_provider):
            client = VisionLLMClient(config)
        
        test_image = ProcessedImage(image_data=b'test', format='jpeg')
        result = client.analyze_image(test_image, "Test prompt")
        
        assert result == expected_result
        mock_provider.call_vision_api.assert_called_once_with(test_image, "Test prompt")
    
    def test_generate_text_summary_no_provider(self):
        """Test text summary generation when no provider is configured."""
        config = LLMConfig(enabled=False)
        client = VisionLLMClient(config)
        
        result = client.generate_text_summary("Test prompt")
        assert result == "Text generation not available"
    
    def test_generate_text_summary_with_provider(self):
        """Test text summary generation with provider."""
        config = LLMConfig(enabled=True)
        
        # Mock provider
        mock_provider = Mock()
        mock_provider.call_text_api.return_value = "Generated summary"
        
        with patch('photo_ingest.llm_client.OllamaProvider', return_value=mock_provider):
            client = VisionLLMClient(config)
        
        result = client.generate_text_summary("Test prompt")
        
        assert result == "Generated summary"
        mock_provider.call_text_api.assert_called_once_with("Test prompt")


class TestLLMClient:
    """Test main LLM client."""
    
    def test_init_unsupported_provider(self):
        """Test initialization with unsupported provider."""
        # Since Pydantic validates the provider, we need to bypass validation
        config = LLMConfig(enabled=True, provider="ollama")
        config.provider = "unsupported"  # Set after validation
        
        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            LLMClient(config)
    
    def test_generate_summary_disabled(self):
        """Test summary generation when LLM is disabled."""
        config = LLMConfig(enabled=False)
        client = LLMClient(config)
        
        result = client.generate_summary({"test": "data"})
        assert result is None
    
    def test_generate_summary_success(self):
        """Test successful summary generation."""
        config = LLMConfig(enabled=True, provider="ollama")
        
        # Mock provider
        mock_provider = Mock()
        mock_provider.call_text_api.return_value = "Generated summary"
        
        with patch('photo_ingest.llm_client.OllamaProvider', return_value=mock_provider):
            client = LLMClient(config)
        
        exif_stats = {
            'cameras': {'Camera1': 10},
            'lenses': {'Lens1': 5, 'Lens2': 5},
            'date_range': ('2024-01-01', '2024-01-02'),
            'total_files': 10,
            'iso_range': (100, 800),
            'files_with_gps': 3
        }
        
        result = client.generate_summary(exif_stats)
        assert result == "Generated summary"
        
        # Verify prompt was built and API was called
        mock_provider.call_text_api.assert_called_once()
        call_args = mock_provider.call_text_api.call_args[0][0]
        assert "Camera1" in call_args
        assert "2024-01-01 to 2024-01-02" in call_args
    
    def test_generate_summary_exception(self):
        """Test summary generation with exception."""
        config = LLMConfig(enabled=True, provider="ollama")
        
        # Mock provider that raises exception
        mock_provider = Mock()
        mock_provider.call_text_api.side_effect = Exception("API Error")
        
        with patch('photo_ingest.llm_client.OllamaProvider', return_value=mock_provider):
            client = LLMClient(config)
        
        result = client.generate_summary({"test": "data"})
        assert result is None
    
    def test_build_prompt_default_template(self):
        """Test building prompt with default template."""
        config = LLMConfig(enabled=True, provider="ollama")
        
        with patch('photo_ingest.llm_client.OllamaProvider'):
            client = LLMClient(config)
        
        exif_stats = {
            'cameras': {'Nikon D850': 15, 'Canon 5D': 5},
            'lenses': {'24-70mm': 10, '85mm': 8, '16-35mm': 2},
            'date_range': ('2024-01-15', '2024-01-16'),
            'total_files': 20,
            'iso_range': (200, 1600),
            'files_with_gps': 5
        }
        
        prompt = client.build_prompt(exif_stats)
        
        assert "Nikon D850, Canon 5D" in prompt
        assert "24-70mm, 85mm, 16-35mm" in prompt
        assert "2024-01-15 to 2024-01-16" in prompt
        assert "Total Files: 20" in prompt
        assert "ISO Range: 200 - 1600" in prompt
        assert "5 files with GPS" in prompt
    
    def test_build_prompt_custom_template(self):
        """Test building prompt with custom template."""
        custom_template = "Custom prompt with {cameras} and {total_files} files"
        config = LLMConfig(enabled=True, provider="ollama", prompt_template=custom_template)
        
        with patch('photo_ingest.llm_client.OllamaProvider'):
            client = LLMClient(config)
        
        exif_stats = {
            'cameras': {'Camera1': 10},
            'total_files': 10
        }
        
        prompt = client.build_prompt(exif_stats)
        assert prompt == "Custom prompt with Camera1 and 10 files"


class TestImageAnalysisResult:
    """Test ImageAnalysisResult data class."""
    
    def test_creation(self):
        """Test creating ImageAnalysisResult."""
        result = ImageAnalysisResult(
            visual_description="A beautiful landscape",
            confidence=0.85,
            processing_time=2.3,
            error=None
        )
        
        assert result.visual_description == "A beautiful landscape"
        assert result.confidence == 0.85
        assert result.processing_time == 2.3
        assert result.error is None
    
    def test_creation_with_error(self):
        """Test creating ImageAnalysisResult with error."""
        result = ImageAnalysisResult(
            visual_description="",
            confidence=0.0,
            processing_time=0.5,
            error="Processing failed"
        )
        
        assert result.visual_description == ""
        assert result.confidence == 0.0
        assert result.error == "Processing failed"