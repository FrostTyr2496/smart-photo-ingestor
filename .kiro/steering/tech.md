---
inclusion: always
---
# Tech Stack & Commands

## Stack
- Python 3.11+ (macOS M1 Max)
- Database: SQLite3 (local state & deduplication index)
- Local LLM: Ollama HTTP API (optional visual/metadata fallback)

## Exact Commands (Do Not Guess)
- Run single test: `pytest tests/test_<module>.py -v`
- Run non-LLM fast tests: `pytest -k "not ollama and not llm"`
- Dry-run pipeline: `python -m dry_run`

## Core Technical Rules
- Deterministic heuristics (EXIF, file size, partial SHA-256) ALWAYS run before any LLM/visual call.
- Pipeline must degrade gracefully when Ollama is offline (`test_without_llm.py` must always pass).
- Never mutate or delete source files on mounted volumes (`/Volumes/*`) during dry-run or scan phases.