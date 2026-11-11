"""Session fixture for the realfiles tests: the local corpus under testdata/corpus (gitignored).

Every test in this folder skips when the corpus is missing or empty, so `uv run pytest -q` stays green on a clean
checkout. Real photos never go into git.
"""

from pathlib import Path

import pytest

CORPUS_DIR = Path(__file__).resolve().parents[2] / "testdata" / "corpus"
MEDIA_SUFFIXES = {".nef", ".jpg", ".jpeg", ".tif", ".dng", ".mp4", ".mov", ".heic", ".lrv"}


@pytest.fixture(scope="session")
def corpus() -> Path:
    """The corpus root. Skips the test when the directory is missing or holds no media files."""
    if not CORPUS_DIR.is_dir():
        pytest.skip("testdata/corpus is absent (copy sample files there; see docs/private/CORPUS.md)")
    if not any(p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES for p in CORPUS_DIR.rglob("*")):
        pytest.skip("testdata/corpus is empty")
    return CORPUS_DIR


@pytest.fixture(scope="session")
def media_files(corpus: Path) -> list[Path]:
    """Every media file under the corpus, sorted."""
    return sorted(p for p in corpus.rglob("*") if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES)
