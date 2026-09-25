# Verification

Verified locally on Windows, 25 September 2026.

- **Backend:** `python -m pytest -q` — **18 passed**. One upstream Starlette warning about future `httpx` test-client deprecation; no failing tests.
- **Frontend:** `npm.cmd run build` — TypeScript check and Vite production build passed. Final assets are served by FastAPI at `http://127.0.0.1:8000`.
- **Runtime:** standard CPython 3.13.2 in ignored `.runtime/python`; system MSYS Python was unsuitable for Pydantic's Windows binary wheels. Exact installed Python dependencies are recorded in `requirements-lock.txt`; npm dependencies are locked in `frontend/package-lock.json`.
- **Browser:** landing page and Start Exploring; normal dashboard; area and route selectors; simulation controls; deterministic rainfall fixture with 28 mm/h rainfall, 14 deduplicated reports, and a 25-minute baseline / 40-minute observed route / 15-minute additional delay; weather disable and live observation expiry; restart/reconnection to the paused shared session; responsive narrow layout without horizontal overflow.
- **Analysis:** evidence IDs resolve to normalized observations and original raw records; deterministic and API tests verify geography, timing, no rain attribution for congestion, baseline gaps, late/malformed records, duplicate suppression, and unavailable estimates when route segments are missing or stale.
- **Final UI checks:** original complaint / normalized event evidence dialog opened and dismissed using Escape; all supporting records are inspectable. Desktop (1440px) and mobile (390px) viewport checks found no horizontal overflow. No browser console errors were reported after final verification.

The app is an educational localhost service, not a production deployment. Long-duration load testing and real-world model validation are outside scope. No live Jaipur data was used.
