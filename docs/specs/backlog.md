# Backlog (Phase 7 and unscheduled)

These items are not tasks yet. Promote one into a spec with full task text before anyone builds it.

- **Re-ordered numbering.** The `M` number becomes a per-shoot sequence (`M0001…`) instead of the camera's
  `DSC_` number. Needs: stable ordering by capture time + subsec, rename only at plan time, and the original
  number kept in the catalog (D-004).
- **LLM enrichment.** One representative frame per sequence (JpgFromRaw preview) → a short description, from a
  background queue after the job is done. Circuit breaker: 2 consecutive failures disable it for the session.
  Never in the ingest path. The archived `llm_client.py` and `visual_analyzer.py` are reference only.
- **Offline reverse geocoding** for shoot naming. The archived `location_service.py` sent coordinates to public
  Nominatim: don't repeat that.
- **Phone imports.** iPhone exports from a folder (`allow_non_card=True`), Live Photo pairing via
  `ContentIdentifier`.
- **Windows read-only companion.** The exFAT drives are reused on Windows (D-002); the catalog snapshot on the
  drive could drive a read-only browser there.
- **XMP sidecar tracking** (`sidecars` table) beyond copy-with-group, if Lightroom's "write XMP" setting gets
  turned on.
- **Rental and borrowed bodies, model variants.** A rented body (for example a Z 8 for one trip) shows up as
  an unknown Model. Today it gets an auto nickname (model without spaces) and an `unknown_body` warning in the
  plan (D-032). Subtask: let `[bodies]` mark a body as `rented = true` with a date range, accept variant Model
  strings (`NIKON Z 6_2` vs `NIKON Z 6II`, firmware renames) as aliases, and add rental frames to the corpus
  wish list (fnd-7). Real trip names stay in `docs/private/`.
- **Shoot names from geo or a local LLM.** `--name` is typed by hand today. Derive a suggested name from GPS
  (offline geocoding, above) or a local model (for example via Ollama) on a representative frame, shown in the
  dry-run plan for the user to accept. Never sent to a public service.
- **Infer the body of model-less videos** more precisely (QuickTime Make/Model tags) instead of the folder vote
  used today (D-032).
