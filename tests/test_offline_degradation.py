"""Must-pass offline suite: the pipeline must work when Ollama is off or unreachable.

Run on its own (fast, no network, no real LLM, no files outside tmp_path):

    pytest tests/test_offline_degradation.py -v      # or: pytest -m offline

These tests replace the nonexistent `test_without_llm.py` that older docs/steering referred to.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests
from PIL import Image

import photo_ingest.analyzer as analyzer_module
from photo_ingest.analyzer import PhotoAnalyzer
from photo_ingest.config import FileTypes, LLMConfig, PerformanceConfig
from photo_ingest.llm_client import LLMClient, OllamaProvider

pytestmark = pytest.mark.offline


def _make_jpegs(directory: Path, count: int = 2) -> list:
    paths = []
    for i in range(count):
        path = directory / f"IMG_{i}.jpg"
        exif = Image.Exif()
        exif[0x010F] = "TestMake"  # Make: Pillow-only reading yields {} for JPEGs with no EXIF
        Image.new("RGB", (64, 48), (i * 40, 80, 120)).save(path, exif=exif)
        paths.append(path)
    return paths


def _unreachable_ollama_config() -> LLMConfig:
    # No retries and no sleeping, so a refused connection fails fast.
    return LLMConfig(enabled=True, provider="ollama", retry_attempts=0, retry_delay=0)


class TestLLMDisabledOrUnreachable:
    def test_disabled_llm_returns_none_summary(self):
        client = LLMClient(LLMConfig(enabled=False))
        assert client.generate_summary({"total_files": 1}) is None

    def test_unreachable_ollama_summary_returns_none_not_raise(self):
        client = LLMClient(_unreachable_ollama_config())
        with patch.object(
            requests.Session, "post", side_effect=requests.exceptions.ConnectionError("refused")
        ):
            assert client.generate_summary({"total_files": 1}) is None

    def test_unreachable_ollama_vision_returns_error_result_not_raise(self):
        provider = OllamaProvider(_unreachable_ollama_config())
        with patch.object(
            requests.Session, "post", side_effect=requests.exceptions.ConnectionError("refused")
        ):
            image = SimpleNamespace(image_data=b"\xff\xd8\xff", resolution="64x48")
            result = provider.call_vision_api(image, "describe")
        assert result.error
        assert result.confidence == 0.0


class TestMetadataPipelineWithoutLLM:
    def test_analyze_directory_works_with_no_llm(self, tmp_path):
        _make_jpegs(tmp_path, 2)
        analyzer = PhotoAnalyzer(FileTypes(), PerformanceConfig(parallel_workers=1), detailed=True)
        result = analyzer.analyze_directory(tmp_path)
        assert result.image_files_analyzed == 2

    def test_batch_metadata_falls_back_to_pillow_when_exiftool_missing(self, tmp_path):
        paths = _make_jpegs(tmp_path, 2)
        analyzer = PhotoAnalyzer(FileTypes(), PerformanceConfig(parallel_workers=1), detailed=True)
        with patch.object(analyzer_module, "_find_exiftool", return_value=None):
            results = analyzer.extract_metadata_batch(paths)
        assert set(results) == set(paths)
        assert all(meta.get("ImageWidth") == 64 for meta in results.values())
