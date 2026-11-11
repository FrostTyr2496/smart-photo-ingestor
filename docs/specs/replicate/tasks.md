# Spec: replicate (Phase 6) — 12 TB master reconcile, fixity, dual copy

Goal: when the 12 TB master is plugged in, copy every asset it lacks (verified), snapshot the catalog onto it,
and audit a sample of what it already holds. After the full dedupe, the policy becomes dual copy (D-003).

Rules:
- `docs/steering/task-execution.md` applies.
- rep-0 details the placeholders.

- [ ] 0. **[stronger]** Replication design (user approval required)
  - ID: rep-0
  - Blocks on: idx-7
  - Write `docs/reviews/<date>-replication.md` covering:
    - the `pending_replication` query (assets without a verified MASTER instance);
    - source choice (any online verified instance; prefer WORKING);
    - the MASTER folder layout (mirror WORKING paths? legacy content per idx-7);
    - catalog snapshot placement (`snapshot()` to `<MASTER>/PhotoVault/catalog-<date>.sqlite` and to WORKING,
      keeping the last N);
    - the fixity audit sample (oldest `verified_at` first, ~1 % per mount);
    - the `mismatch`/`missing` handling and reporting;
    - the expected runtime on a spinning 12 TB.
  - Then expand the placeholders below.
  - Verify: the user approves.

- [ ] 1. **[user]** Assign the 12 TB as MASTER (`volumes assign … --role MASTER --label 12TB_MASTER`) (ID rep-1)
- [ ] 2. Placeholder: `replicate` job using `copy_verified` (ID rep-2)
- [ ] 3. Placeholder: catalog snapshot after every job (ID rep-3)
- [ ] 4. Placeholder: sampled fixity audit (ID rep-4)
- [ ] 5. **[user]** Switch policy to `{ WORKING = 1, MASTER = 1 }` once dedupe is done (D-003) (ID rep-5)
