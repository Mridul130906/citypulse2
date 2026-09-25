# CityPulse — The Live Civic Health Dashboard

**AmiHacks Track B · Simulation — synthetic data.**

CityPulse combines three independent synthetic streams into a neighborhood dashboard: weather, anonymous civic reports, and fictional transport routes. It demonstrates continuous ingestion, normalization, persistent storage, explainable anomaly detection, cross-feed connections, live updates, and graceful degradation. Nothing shown represents actual Jaipur conditions. No API keys, live APIs, map tiles, external fonts, paid services, or LLMs are used.

## Run on Windows PowerShell

For Vercel hosting, see [DEPLOYMENT.md](DEPLOYMENT.md). It documents the FastAPI
entry point, frontend build, Neon Postgres connection, and the request-driven
simulation behavior used on Vercel. The instructions below describe local hosting.

Prerequisites: standard CPython **3.11–3.13** from python.org and Node.js **22+** with npm. Avoid MSYS/MinGW Python, which cannot use the standard Windows Pydantic wheels.

From the project directory:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Set-Location frontend
npm.cmd ci
npm.cmd run build
Set-Location ..
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. FastAPI serves the production frontend and API together. Build the frontend before starting the backend. Use **one backend worker**, because the shared scheduler is owned by that process. Stop the service with Ctrl+C.

If PowerShell blocks environment activation, use the environment interpreter directly; no execution-policy change is needed:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

On the machine where this project was built, a project-local standard Python runtime was provisioned because the system Python was MSYS. The ready-to-use command there is:

```powershell
.\.runtime\python\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

That ignored runtime is a local convenience, not a distributable dependency. Use the standard virtual-environment instructions on another machine. Installations need internet access; after installing and building, the core app works offline.

For the exact Python versions verified here, replace `-r requirements.txt` with `-r requirements-lock.txt`. The frontend's `npm.cmd ci` already uses its lockfile.

For frontend development, keep the backend running and use a second terminal:

```powershell
Set-Location frontend
npm.cmd run dev
```

Open the localhost URL Vite prints (normally http://127.0.0.1:5173). Vite proxies `/api` to port 8000, including SSE. Production uses the same-origin FastAPI server.

## Architecture

```text
Shared scenario clock (seed 42)
    ├── weather simulator: area/string timestamps/mm per hour
    ├── civic simulator: messy tickets/location text/irregular reports
    └── transit simulator: route + segment/epoch time/seconds
                    ↓
       normalization + validation + duplicate suppression
                    ↓
        SQLite events + original records + quarantine
                    ↓
      rolling event-time analysis → structured insights
                    ↓
       deterministic summaries + route time calculation
                    ↓
          FastAPI snapshot API + SSE revision notices
                    ↓
              React/TypeScript dashboard
```

`backend/config.py`: zone and route definitions, thresholds, cadences.
`backend/simulators.py`: independent raw generators.
`backend/normalization.py`: common event envelope and validation.
`backend/persistence.py`: indexed SQLite storage, raw evidence, quarantine.
`backend/session.py`: shared clock, scheduling, controls, persistence.
`backend/analysis.py`: observation-only analytical rules and route estimates.
`backend/summaries.py`: deterministic, evidence-grounded wording.
`backend/main.py`: API, SSE, lifecycle, production frontend serving.
`frontend/src`: React interface and responsive Tailwind/CSS styling.

The analysis module never reads scenario names, scenario phases, or hidden generator state. Complaint feed health is represented by normalized `feed_status` heartbeat observations, excluded from complaint counts and activity timelines. This lets analysis distinguish an empty complaint window from a collection outage.

SQLite persists to `backend/citypulse.sqlite`. Override with `CITYPULSE_DB` if needed. Indexes cover `(zone, observed)` and `(source, observed)`. Event IDs and `(source, source_id)` are unique. A restart restores the clock and stored data **paused**, with no wall-clock catch-up. Reset clears this simulation's observations and quarantine. There is no user-data input form.

## Geographic model

| Zone | Area |
| --- | --- |
| JAG | Jagatpura |
| MAL | Malviya Nagar |
| MAN | Mansarovar |
| VAI | Vaishali Nagar |
| CSC | C-Scheme |

The **East connector** has ordered zones JAG → MAL → CSC, with segment baselines 10 + 8 + 7 = **25 minutes**. The **West loop** has MAN → VAI → CSC with baselines 12 + 9 + 7 = **28 minutes**. Both are fictional demonstration routes, not official Jaipur services. The schematic conveys relationships, not geographic distances.

## Simulator behavior

One simulation minute is advanced per tick. Default speed is two simulation minutes per real second. Weather emits every simulation minute; transit every minute; complaints arrive irregularly using reproducible seeded random decisions. The backend runs independently of browser tabs. Opening another tab only subscribes to the same session.

Scenario time is measured from the moment a scenario is selected:

| Scenario | Behavior |
| --- | --- |
| Normal day | Clear weather, baseline transit, sparse unrelated pothole reports |
| Heavy rain in Jagatpura | Repeating 60-minute cycle: normal first 10 minutes; varying 22–34 mm/h rain from minute 10; waterlogging reports after 5-minute lag; 15-minute additional JAG segment time after 10-minute lag; rain varies between 2–10 mm/h and transit eases at minute 50; complaints stop at 55; the cycle restarts at 60 |
| Rush-hour congestion | From minutes 10–54, each route segment takes four extra minutes; no rain |
| Road obstruction | JAG obstruction complaints from minute 10; a 12-minute observed segment delay from minute 15; no rain; settles at 55 |
| Missing / delayed feed | Weather stops after minute 10; other feeds continue; old weather observations become stale |

Normal-day background reports also occur in other areas during disruptions. Complaint records include spelling and capitalization variations, copied duplicates, missing categories, occasional unusable locations, and timestamps eight minutes behind arrival time. All originals are preserved, including quarantined raw records.

The **Load 15-minute demo** control resets the data, produces 65 normal baseline minutes, runs the rainfall scenario for 30 more minutes, selects the East connector, and pauses the clock. It ingests real simulator outputs through the normal pipeline. The resulting 40-minute observed route duration minus the 25-minute baseline is exactly **15 minutes**. It is not a hardcoded summary or UI number.

Cadences, lag allowances, analysis windows, and scenario lags can be configured in `config.py`. The controls API also accepts `weather_cadence`, `transit_cadence`, `complaint_lag`, `transit_lag`, `window_minutes`, `lag_minutes`, `complaint_probability`, and `background_complaint_cadence` within validated bounds. The presentation fixture uses default configuration for repeatability.

## Normalization rules

Every accepted observation has `event_id`, `source_event_id`, `source_type`, `zone_id`, `observed_at`, `received_at`, `event_type`, `severity`, `metrics`, `description`, `quality_flags`, and `is_synthetic: true`.

- Area aliases and known misspellings map to stable IDs, including embedded location text.
- ISO timestamps must contain a timezone; transit accepts Unix seconds. Storage uses aware UTC; the UI formats Asia/Kolkata. Naive and future timestamps are rejected.
- Weather accepts mm/hour or converts cm/hour; transit seconds become minutes. Metric names carry units.
- Missing required fields, unknown zones/routes/segments, invalid severity, physically invalid values, and inconsistent speed/duration go to quarantine without stopping ingestion.
- Segment distance is implicit in its baseline at 30 km/h; reported speed must agree with that distance and observed duration within 1 km/h.
- Complaint whitespace is normalized, and categories are inferred from known words when absent or invalid. Original text is preserved.
- Repeated source IDs are suppressed. Copied complaint reports with the same zone, observation time, and normalized description are also suppressed, even if their ticket IDs differ.
- Late arrivals are flagged after two minutes. Queries and freshness use **observation time**, never arrival time. Out-of-order events can contribute only if their observation times still fall in the relevant window.

## Explainable analysis

Default event-time windows are `(now − 30 minutes, now]` and the preceding equal-length window. Rules run separately per zone.

- **Heavy rain:** latest fresh rainfall is at least 15 mm/h.
- **Complaint spike:** at least two complete windows of normalized complaint feed heartbeats; current count is at least `max(4, 2 × preceding count)`. Otherwise show **Building baseline**. Feed gaps interrupt baseline coverage. This is a simple preceding-window baseline, not a seasonal or statistical model.
- **Transit slowdown:** latest fresh segment duration is at least 1.3 times its baseline.
- **Possible rain-related disruption:** heavy rain + a complaint spike + at least three waterlogging reports with matching rainfall and slow transit. Rain must precede each matching report by no more than the configured 15-minute lag allowance, and a matching slow transit observation must follow the report within that allowance.
- **Possible obstruction-related disruption:** at least two matching obstruction reports preceding slower transport within the lag allowance.
- **Transport disruption:** slower transit without sufficient matching evidence of a specific link. Never automatically attribute it to rain.

Each insight contains its rule, zone, window, signals, supporting event IDs, explanation, freshness, and availability. Possible links are not proof of causality; there are no probability claims or numerical health scores.

Weather observations become stale after `2 × cadence + 1` minutes; transit segments follow the same rule. Complaint feed heartbeat availability is distinct from the last complaint time. Feed disabling is reported immediately, while previously observed evidence remains valid until its observation-time expiry. The clock pauses freshness too; service connection status remains independent.

## Route delay

For every distinct segment on a selected route, use its **latest fresh** transit observation. Sum each segment once:

```text
additional_delay = max(0, sum(observed segment minutes) − sum(baseline segment minutes))
```

Weather and complaints add no extra penalties. If any segment is missing or stale, the full route estimate is unavailable; the known baseline is still shown. Route observation time is the oldest observation used in the complete estimate. No route selected means an area-level count of observed slow segments, not a personal lateness prediction.

## API

Interactive documentation: **http://127.0.0.1:8000/docs**.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Service health and synthetic-data marker |
| GET | `/api/areas` | Stable zone definitions |
| GET | `/api/routes` | Fictional ordered routes and segments |
| GET | `/api/state/{zone_id}?route=demo-east` | Dashboard snapshot, summary, freshness, optional delay |
| GET | `/api/overview` | Compact zone overview |
| GET | `/api/events` | Filters: `zone_id`, `source`, `since`, `until`; pagination: `limit` (1–200), `offset`; returns `has_more` |
| GET | `/api/insights/{zone_id}` | Current structured insight and supporting event IDs |
| GET | `/api/evidence/{event_id}` | Normalized observation plus original raw record |
| GET | `/api/simulation` | Shared state and simulated clock |
| POST | `/api/simulation` | Start/pause, reset, scenario, speed, enabled feeds, advance, demo fixture, numeric configuration |
| GET | `/api/live` | SSE revision notifications, retry hint, keepalive comments |

Examples:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/simulation -Method Post -ContentType 'application/json' -Body '{"action":"fixture"}'
Invoke-RestMethod 'http://127.0.0.1:8000/api/state/JAG?route=demo-east'
Invoke-RestMethod http://127.0.0.1:8000/api/simulation -Method Post -ContentType 'application/json' -Body '{"disabled":["weather"],"running":true}'
```

The browser fetches an initial snapshot, then polls `/api/dashboard/{zone_id}` every two seconds while the dashboard is visible. A successful refresh restores the connection indicator after an interruption. The local `/api/live` SSE endpoint remains available to other clients; Vercel returns a polling transport hint there. Simulation controls affect every connected client. Vercel retains four simulated hours of historical observations; local history remains accessible until reset.

## Verification

```powershell
python -m pytest -q
Set-Location frontend
npm.cmd run build
```

Tests cover normalization, timezone handling, unit conversion, duplicates, original preservation, malformed records, late data, geographic matching, time windows, baseline gaps, supported rainfall insights, the exact 15-minute fixture, segment deduplication, non-weather congestion, missing feeds, incomplete routes, physically invalid transit, analysis independence, and session restoration. See `VERIFICATION.md` for execution results and browser checks.

## Presentation walkthrough

1. Open the landing page, point out **Simulation — synthetic data**, then **Start Exploring**.
2. Open **Simulation controls**, choose **Normal day**, and reset if needed. Explain “Building baseline” instead of claiming an early spike.
3. Select Jagatpura and the fictional **East connector**. Use **Load 15-minute demo** to reproducibly fast-forward through baseline, rain onset, lagged complaints, and slowdown. The session pauses at minute 95 for explanation.
4. Show the varying **mm/min** rain reading, elevated reports, and the route **25-minute baseline → 40-minute current estimate → +15 minutes**. Open a supporting record in **Why am I seeing this?** to compare the original and normalized fields.
5. Explain that these events **may be linked** because geography, timestamps, categories, and delay overlap. They do not prove a cause.
6. Disable **Weather**. Its feed becomes unavailable immediately and the summary discloses the limitation. Press **Start**; after its freshness threshold, weather no longer supports a current rainfall claim. Transit can still independently produce a valid estimate.
7. Optionally disable **Transit** and let four simulation minutes pass: the full route estimate becomes unavailable rather than retaining an unsupported number. Restore the demo at any time.

## Limitations

This is a local, single-process educational application with no authentication and shared controls; keep it bound to localhost. It is not a real civic reporting service, traffic predictor, medical/safety tool, or official transport source. There is no collection of personal information. Reports and routes are generated, and the spatial model is deliberately small. Rules are explainable heuristics, not validated causal models. Data grows in SQLite until reset; production-scale retention and multi-process scheduling are outside this demo. Dependency installation is the only network requirement. After a server restart the session is paused until resumed.
