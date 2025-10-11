"""LLM client with extensible provider support and vision capabilities."""

import json
import time
import base64
import logging
from typing import Dict, Any, Optional, Protocol
from dataclasses import dataclass
from abc import ABC, abstractmethod

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

from .config import LLMConfig
# ProcessedImage will be imported when needed to avoid circular import

# Set up logging
logger = logging.getLogger(__name__)


@dataclass
class ImageAnalysisResult:
    """Result from image analysis."""
    visual_description: str
    confidence: float
    processing_time: float
    error: Optional[str] = None


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""
    
    @abstractmethod
    def call_text_api(self, prompt: str) -> str:
        """Make text generation API call."""
        pass
    
    @abstractmethod
    def call_vision_api(self, image: 'ProcessedImage', prompt: str) -> ImageAnalysisResult:
        """Make vision API call."""
        pass


class OllamaProvider(LLMProvider):
    """Ollama-specific implementation."""
    
    def __init__(self, config: LLMConfig, verbose: bool = False):
        """Initialize Ollama provider.
        
        Args:
            config: LLM configuration
            verbose: Enable verbose logging
        """
        if not REQUESTS_AVAILABLE:
            raise ImportError("requests library is required for Ollama integration")
        
        self.config = config
        self.verbose = verbose
        self.session = requests.Session()
        
        # Set appropriate timeouts for different operations
        # Text generation: 60 seconds
        # Vision analysis: 120 seconds (images take longer to process)
        self.text_timeout = 60
        self.vision_timeout = 120
    
    def call_text_api(self, prompt: str) -> str:
        """Make text generation API call to Ollama.
        
        Args:
            prompt: Text prompt
            
        Returns:
            Generated text response
            
        Raises:
            Exception: If API call fails after retries
        """
        payload = {
            "model": self.config.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "top_p": 0.9
            }
        }
        
        if self.verbose:
            logger.info(f"Making text API call to {self.config.endpoint}")
            logger.info(f"Model: {self.config.model}")
            logger.info(f"Prompt length: {len(prompt)} characters")
        
        for attempt in range(self.config.retry_attempts + 1):
            try:
                if self.verbose:
                    logger.info(f"Attempt {attempt + 1}/{self.config.retry_attempts + 1}")
                
                start_time = time.time()
                response = self.session.post(
                    self.config.endpoint,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=self.text_timeout
                )
                response.raise_for_status()
                
                result = response.json()
                response_text = result.get("response", "").strip()
                
                if self.verbose:
                    elapsed = time.time() - start_time
                    logger.info(f"Text API call completed in {elapsed:.2f}s")
                    logger.info(f"Response length: {len(response_text)} characters")
                
                return response_text
                
            except Exception as e:
                if self.verbose:
                    logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")
                
                if attempt == self.config.retry_attempts:
                    raise Exception(f"Ollama API call failed after {self.config.retry_attempts + 1} attempts: {str(e)}")
                
                # Wait before retry
                retry_delay = self.config.retry_delay * (2 ** attempt)
                if self.verbose:
                    logger.info(f"Retrying in {retry_delay:.1f} seconds...")
                time.sleep(retry_delay)
    
    def call_vision_api(self, image: 'ProcessedImage', prompt: str) -> ImageAnalysisResult:
        """Make vision API call to Ollama.
        
        Args:
            image: Processed image data
            prompt: Vision analysis prompt
            
        Returns:
            ImageAnalysisResult with analysis
        """
        start_time = time.time()
        
        # Encode image to base64
        image_b64 = base64.b64encode(image.image_data).decode('utf-8')
        
        if self.verbose:
            logger.info(f"Making vision API call to {self.config.endpoint}")
            logger.info(f"Vision model: {self.config.vision_model}")
            logger.info(f"Image size: {len(image.image_data)} bytes")
            logger.info(f"Image resolution: {image.resolution}")
            logger.info(f"Base64 size: {len(image_b64)} characters")
            logger.info(f"Prompt: {prompt[:100]}..." if len(prompt) > 100 else f"Prompt: {prompt}")
        
        payload = {
            "model": self.config.vision_model,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False,
            "options": {
                "temperature": 0.7,
                "top_p": 0.9
            }
        }
        
        for attempt in range(self.config.retry_attempts + 1):
            try:
                if self.verbose:
                    logger.info(f"Vision attempt {attempt + 1}/{self.config.retry_attempts + 1}")
                    logger.info(f"Sending request to Ollama (timeout: {self.vision_timeout}s)...")
                
                api_start_time = time.time()
                response = self.session.post(
                    self.config.endpoint,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=self.vision_timeout
                )
                response.raise_for_status()
                
                if self.verbose:
                    logger.info(f"Received response from Ollama in {time.time() - api_start_time:.2f}s")
                
                result = response.json()
                description = result.get("response", "").strip()
                
                processing_time = time.time() - start_time
                
                if self.verbose:
                    logger.info(f"Vision analysis completed in {processing_time:.2f}s total")
                    logger.info(f"Response: {description[:200]}..." if len(description) > 200 else f"Response: {description}")
                
                # Simple confidence scoring based on response length and content
                confidence = self._calculate_confidence(description)
                
                return ImageAnalysisResult(
                    visual_description=description,
                    confidence=confidence,
                    processing_time=processing_time
                )
                
            except requests.exceptions.Timeout as e:
                if self.verbose:
                    logger.warning(f"Vision API timeout after {self.vision_timeout}s on attempt {attempt + 1}")
                
                if attempt == self.config.retry_attempts:
                    processing_time = time.time() - start_time
                    return ImageAnalysisResult(
                        visual_description="",
                        confidence=0.0,
                        processing_time=processing_time,
                        error=f"Vision API timeout after {self.vision_timeout}s"
                    )
                
                # Wait before retry
                retry_delay = self.config.retry_delay * (2 ** attempt)
                if self.verbose:
                    logger.info(f"Retrying vision API call in {retry_delay:.1f} seconds...")
                time.sleep(retry_delay)
                
            except Exception as e:
                if self.verbose:
                    logger.warning(f"Vision attempt {attempt + 1} failed: {str(e)}")
                
                if attempt == self.config.retry_attempts:
                    processing_time = time.time() - start_time
                    return ImageAnalysisResult(
                        visual_description="",
                        confidence=0.0,
                        processing_time=processing_time,
                        error=f"Vision API call failed: {str(e)}"
                    )
                
                # Wait before retry
                retry_delay = self.config.retry_delay * (2 ** attempt)
                if self.verbose:
                    logger.info(f"Retrying vision API call in {retry_delay:.1f} seconds...")
                time.sleep(retry_delay)
    
    def _calculate_confidence(self, description: str) -> float:
        """Calculate confidence score based on response quality.
        
        Args:
            description: Generated description
            
        Returns:
            Confidence score between 0.0 and 1.0
        """
        if not description:
            return 0.0
        
        # Simple heuristics for confidence
        base_confidence = 0.5
        
        # Longer descriptions generally indicate better analysis
        length_bonus = min(len(description) / 200, 0.3)
        
        # Presence of specific photography terms
        photo_terms = ['portrait', 'landscape', 'macro', 'subject', 'lighting', 'composition', 'background']
        term_bonus = sum(0.02 for term in photo_terms if term.lower() in description.lower())
        
        # Avoid overly generic responses
        generic_phrases = ['this photo', 'the image', 'appears to be', 'seems to']
        generic_penalty = sum(0.05 for phrase in generic_phrases if phrase.lower() in description.lower())
        
        confidence = base_confidence + length_bonus + term_bonus - generic_penalty
        return max(0.0, min(1.0, confidence))


class VisionLLMClient:
    """Client for vision-capable LLM models."""
    
    def __init__(self, config: LLMConfig, verbose: bool = False):
        """Initialize vision LLM client.
        
        Args:
            config: LLM configuration
            verbose: Enable verbose logging
        """
        self.config = config
        self.verbose = verbose
        self.provider = self._create_provider()
    
    def analyze_image(self, image: 'ProcessedImage', prompt: str) -> ImageAnalysisResult:
        """Send image and prompt to vision LLM for analysis.
        
        Args:
            image: Processed image data
            prompt: Analysis prompt
            
        Returns:
            ImageAnalysisResult with visual analysis
        """
        if not self.provider:
            return ImageAnalysisResult(
                visual_description="Vision analysis not available",
                confidence=0.0,
                processing_time=0.0,
                error="LLM provider not configured"
            )
        
        return self.provider.call_vision_api(image, prompt)
    
    def generate_text_summary(self, prompt: str) -> str:
        """Generate text summary using LLM.
        
        Args:
            prompt: Text prompt
            
        Returns:
            Generated summary text
        """
        if not self.provider:
            return "Text generation not available"
        
        return self.provider.call_text_api(prompt)
    
    def _create_provider(self) -> Optional[LLMProvider]:
        """Factory method for LLM providers.
        
        Returns:
            LLM provider instance or None if not configured
        """
        if not self.config.enabled:
            return None
        
        if self.config.provider == "ollama":
            return OllamaProvider(self.config, self.verbose)
        
        # Future: Add support for other providers
        # elif self.config.provider == "openai":
        #     return OpenAIProvider(self.config)
        
        raise ValueError(f"Unsupported LLM provider: {self.config.provider}")


class LLMClient:
    """Main LLM client with extensible provider support."""
    
    def __init__(self, config: LLMConfig):
        """Initialize LLM client.
        
        Args:
            config: LLM configuration
        """
        self.config = config
        self.provider = self._create_provider()
    
    def generate_summary(self, exif_stats: Dict[str, Any]) -> Optional[str]:
        """Generate summary with retry logic.
        
        Args:
            exif_stats: EXIF statistics dictionary
            
        Returns:
            Generated summary or None if failed
        """
        if not self.provider:
            return None
        
        try:
            prompt = self.build_prompt(exif_stats)
            return self.provider.call_text_api(prompt)
        except Exception as e:
            print(f"DEBUG: LLM summary generation failed: {e}")
            return None
    
    def build_prompt(self, exif_stats: Dict[str, Any]) -> str:
        """Build prompt from EXIF statistics.
        
        Args:
            exif_stats: EXIF statistics dictionary
            
        Returns:
            Formatted prompt string
        """
        if self.config.prompt_template:
            # For custom templates, prepare the data properly
            template_data = dict(exif_stats)
            # Convert cameras dict to string for template substitution
            if 'cameras' in template_data and isinstance(template_data['cameras'], dict):
                template_data['cameras'] = ", ".join(template_data['cameras'].keys())
            return self.config.prompt_template.format(**template_data)
        
        # Default prompt template
        cameras = ", ".join(exif_stats.get('cameras', {}).keys())
        lenses = ", ".join(list(exif_stats.get('lenses', {}).keys())[:3])  # Top 3 lenses
        date_range = exif_stats.get('date_range', ('Unknown', 'Unknown'))
        file_count = exif_stats.get('total_files', 0)
        iso_range = exif_stats.get('iso_range', (0, 0))
        gps_info = f"{exif_stats.get('files_with_gps', 0)} files with GPS" if exif_stats.get('files_with_gps', 0) > 0 else "No GPS data"
        
        return f"""Based on the following photo session statistics, write 2-4 sentences summarizing the shoot:

Cameras: {cameras}
Lenses: {lenses}
Date Range: {date_range[0]} to {date_range[1]}
Total Files: {file_count}
ISO Range: {iso_range[0]} - {iso_range[1]}
Location: {gps_info}

Focus on the type of photography, conditions, and any notable equipment or settings used."""
    
    def _create_provider(self) -> Optional[LLMProvider]:
        """Factory method for LLM providers.
        
        Returns:
            LLM provider instance or None if not configured
        """
        if not self.config.enabled:
            return None
        
        if self.config.provider == "ollama":
            return OllamaProvider(self.config)
        
        raise ValueError(f"Unsupported LLM provider: {self.config.provider}")