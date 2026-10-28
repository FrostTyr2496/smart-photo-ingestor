---
inclusion: always
---
# smart-photo-ingestor Architecture & Rules

## Module Map (Do Not Search Blindly)
-Ingress/EXIF: `file_scanner.py`, `exif_processor.py`, `device_detector.py`, `location_service.py`
-Core Logic: `deduplication.py`, `analyzer.py`, `visual_analyzer.py`, `llm_client.py` (Ollama)
-Storage/IO: `database.py` (SQLite state/index), `file_operations.py`, `dry_run.py`
-Tests: `tests/test_<module>.py` (mirrors root modules 1:1)

## Hardware & Data Safety Constraints (CRITICAL)
- Source media includes Nikon Z6 & Z6II RAW (.NEF) and sidecar (.xmp) files across external volumes (`/Volumes/*`).
- NEVER delete or mutate source files in `file_scanner.py`, `deduplication.py`, or `dry_run.py`. Physical writes belong strictly in `file_operations.py`.
- Deduplication MUST use tiered deterministic checks first (File size -> EXIF `DateTimeOriginal` + `CameraSerialNumber` -> partial 64KB hash -> full SHA-256) before any visual/LLM call. Nikon filenames (`_DSCxxxx.NEF`) roll over and collide—never dedupe by filename alone.
- `llm_client.py` / `visual_analyzer.py` must remain strictly optional with circuit-breaker timeouts so batch runs degrade gracefully when Ollama is offline (`tests/test_without_llm.py` must always pass).

## Test Commands
- Fast unit test (default): `pytest tests/test_<module>.py -v`
- Offline/Heuristic suite: `pytest -k "not ollama and not llm"`
- Do NOT run `test_verbose_ollama.py` unless explicitly editing `llm_client.py`.