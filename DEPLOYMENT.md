# Deploy CityPulse on Vercel

## Dashboard settings

Import `Mridul130906/citypulse2`, branch `main`.

| Setting | Value |
| --- | --- |
| Application preset | FastAPI |
| Root directory | `./` (the GitHub repository root) |
| Build command | `npm --prefix frontend ci && npm --prefix frontend run build` |
| Output directory | Leave the default; do not set `frontend/dist` |
| Install command | Leave the default |
| Python | 3.12, selected by `pyproject.toml` |

The repository config selects `backend.main:app` as the entry point. Do not
change the root to `backend` or `frontend`. The extra enclosing folders on the
local Windows machine are not part of the GitHub repository.

## Connect the database

1. Open your project in Vercel, then **Storage** and **Create Database**.
2. Select **Neon Postgres** from the Marketplace and review the offered plan.
3. Connect it to this project for **Production**. Vercel's integration normally
   adds `DATABASE_URL`; verify that this environment variable exists in the
   project settings. `POSTGRES_URL` is also supported.
4. Redeploy the latest commit after adding the database. Environment variable
   changes do not update an existing deployment.

If the project does not exist yet, deploy once to create it, then connect Neon
and redeploy. The frontend can load without a database, but its dashboard will
explain that storage must be connected. No database password belongs in Git,
the frontend, or chat. No manual SQL setup is required: the backend creates its
own `citypulse_snapshots` table on the first API request.

If Preview deployments need data, connect a separate Neon branch to Preview.
The default snapshot key also separates Vercel's `production` and `preview`
environments. All previews in the same database share the preview snapshot;
set `CITYPULSE_SESSION_KEY` explicitly if they need independent sessions.

## Verify after deployment

1. Open `https://YOUR-PROJECT.vercel.app/api/health`; it should return
   `{"status":"ok","is_synthetic":true}`. This checks database access too.
2. Open the site and click **Start Exploring**.
3. In **Simulation controls**, select Jagatpura and the East connector, then
   **Load 15-minute demo**. Confirm a 40-minute estimate and +15-minute delay.
4. Reload the page or open another tab. The saved fixture should be shared.
5. Click **Start** and verify that the simulated minute increases.

## Hosting behavior

Local hosting still uses SQLite and its background scheduler. On Vercel,
`VERCEL=1` enables request-driven operation and disables the background loop.
The browser polls a combined dashboard endpoint every two seconds while the
dashboard is visible. It does not hold a Vercel function open for an SSE stream.

Postgres holds a compressed SQLite snapshot, and a transaction-level advisory
lock serializes access across function instances. This preserves the existing
ingestion and analysis rules with only a few remote database queries per request.
It is a small shared demonstration, not a high-traffic database architecture.

After more than 30 seconds without an API request, elapsed idle time is skipped.
The next visitor resumes from saved simulation time. Short gaps advance at most
20 simulated minutes per request to bound execution time. Only the latest 240
simulated minutes of events and quarantine are retained on Vercel. Local storage
continues to keep history until reset. All visitors share simulation controls,
including Reset; the project stores only synthetic observations.

## Troubleshooting

- **No FastAPI entry point found:** ensure the latest commit is deployed, the
  root is `./`, and `pyproject.toml` is in that root.
- **Connect Neon Postgres...:** add `DATABASE_URL` to the deployment's environment
  and redeploy.
- **Storage temporarily unavailable:** check that the database is active, its
  connection URL is correct, and its role can create and update the snapshot
  table. Do not post the URL in build logs or chat.
- **Frontend 404:** restore the build command above and remove any output
  directory override. The build must create `frontend/dist`.

Official references: [Vercel FastAPI](https://vercel.com/docs/frameworks/backend/fastapi)
and [Vercel Marketplace storage](https://vercel.com/docs/marketplace-storage).
