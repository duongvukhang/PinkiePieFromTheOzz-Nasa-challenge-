## Current phase: 1 (Identity matching, motion fitting, classification, consensus, streak/tracklet, A-vs-B comparison implemented and verified)
## Last session: Gemini 3.8 Flash (High) | 2026-11-12 | Resolve injection-recovery test escalation (Argon #1)
Done: Applied Argon item #1 to `tests/science/test_injection_recovery_and_false_positives.py` (skipped center grid slot `k=4` in `_make_static_anchors` keeping 8 perimeter anchors `0..7`, and propagated fitted `(ra_ref_deg, dec_ref_deg)` from `ref_epoch_mjd_tdb` to `mjd0` with `pmra_arcsec_yr`/`pmdec_arcsec_yr` before matching). `science/sphx_core` untouched.
Files touched: `tests/science/test_injection_recovery_and_false_positives.py`, `docs/STATUS.md`
Verified by: `.venv/bin/pytest tests/science/test_injection_recovery_and_false_positives.py -v --tb=short` (3 passed in 142.30s); `.venv/bin/pytest tests/science tests/unit -v --tb=short` (44 passed, 2 expected fails in `test_sso_and_planetx.py` for unimplemented `sphx_core.orbits`/`planetx` in 196.03s); `.venv/bin/ruff check` & `ruff format --check` on modified test file (clean).
Lines before -> after: `tests/science/test_injection_recovery_and_false_positives.py` `173 -> 178` (+5 net).
## Next task: Implement `sphx_core.orbits` & `sphx_core.planetx` (§E, §I) — Gemini 3.8 Flash High
## Open questions:
- Confirm `[verify]` items against live IRSA SPHEREx docs when collection/MEF schemas are finalized (SPHEREx MEF extension names, Horizons SPHEREx ID).
- Run `make setup` / `pip install -e ./backend` on a machine with setuptools/network so `sphx.providers` entry-point metadata includes all providers.
- Run `docker compose config` and full Compose stack on a machine with Docker Compose v2 installed.
## Escalations: None
