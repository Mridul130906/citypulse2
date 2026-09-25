"""Request-driven hosting for the small, shared CityPulse demonstration.

Postgres stores a compressed SQLite snapshot. A transaction lock serializes
requests across Vercel instances, preserving the existing analysis and ingestion
code without thousands of database round trips for each demo fixture.
"""
import logging
import os
import time
import zlib
from contextvars import ContextVar
from datetime import timedelta

from starlette.responses import JSONResponse
from .persistence import Store
from .session import Session

request_state = ContextVar("citypulse_request_state", default=None)
logger = logging.getLogger(__name__)
RETENTION_MINUTES = 240
IDLE_SECONDS = 30
MAX_CATCHUP_TICKS = 20


def restore(payload):
    store = Store()
    if payload:
        store.db.deserialize(zlib.decompress(payload))
    return store, Session(store, resume=True)


def advance(session, previous, remainder, now):
    elapsed = max(0, now - previous)
    # No unbounded replay after a cold start or a long absence. The next
    # visiting browser resumes the simulation from its saved time.
    if not session.running or elapsed > IDLE_SECONDS:
        return 0.0
    progress = elapsed * session.speed + remainder
    ticks = min(int(progress), MAX_CATCHUP_TICKS)
    if ticks:
        session.step(ticks)
    return progress % 1


def serialize(store, session):
    cutoff = (session.now - timedelta(minutes=RETENTION_MINUTES)).isoformat()
    store.db.execute("DELETE FROM events WHERE observed < ?", (cutoff,))
    store.db.execute("DELETE FROM quarantine WHERE received < ?", (cutoff,))
    store.save(session.state())
    store.db.execute("VACUUM")
    return zlib.compress(store.db.serialize())


class ServerlessMiddleware:
    def __init__(self, app, enabled=False):
        self.app = app
        self.enabled = enabled

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if not self.enabled or scope["type"] != "http" or not path.startswith("/api/"):
            return await self.app(scope, receive, send)
        if path in ("/api/areas", "/api/routes"):
            return await self.app(scope, receive, send)
        if path == "/api/live":
            return await JSONResponse({"transport": "poll", "interval_ms": 2000})(scope, receive, send)
        database_url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
        if not database_url:
            return await JSONResponse(
                {"detail": "Connect Neon Postgres in Vercel Storage, then redeploy with DATABASE_URL configured."},
                status_code=503,
            )(scope, receive, send)

        store = None
        token = None
        messages = []

        async def buffer(message):
            messages.append(message)

        try:
            import psycopg
            async with await psycopg.AsyncConnection.connect(database_url, connect_timeout=10) as conn:
                await conn.execute("SET LOCAL lock_timeout = '10s'")
                await conn.execute("SET LOCAL statement_timeout = '20s'")
                # One global lock also protects concurrent first-time schema creation.
                await conn.execute("SELECT pg_advisory_xact_lock(73921406)")
                await conn.execute("""CREATE TABLE IF NOT EXISTS citypulse_snapshots (
                    name TEXT PRIMARY KEY, payload BYTEA NOT NULL,
                    updated DOUBLE PRECISION NOT NULL, remainder DOUBLE PRECISION NOT NULL
                )""")
                key = os.getenv("CITYPULSE_SESSION_KEY") or os.getenv("VERCEL_ENV", "development")
                cursor = await conn.execute(
                    "SELECT payload, updated, remainder FROM citypulse_snapshots WHERE name = %s", (key,)
                )
                row = await cursor.fetchone()
                now = time.time()
                store, session = restore(bytes(row[0]) if row else None)
                remainder = advance(session, row[1], row[2], now) if row else 0.0
                token = request_state.set((store, session))
                await self.app(scope, receive, buffer)
                # Never persist changes from an unsuccessful handler.
                status = next(m["status"] for m in messages if m["type"] == "http.response.start")
                if status < 400:
                    if scope["method"] != "GET":
                        remainder = 0.0
                    payload = serialize(store, session)
                    await conn.execute("""INSERT INTO citypulse_snapshots VALUES (%s, %s, %s, %s)
                        ON CONFLICT (name) DO UPDATE SET payload = EXCLUDED.payload,
                        updated = EXCLUDED.updated, remainder = EXCLUDED.remainder""",
                        (key, payload, now, remainder))
            # Send only after the transaction commits, so controls are durable
            # before the browser issues its next refresh.
        except Exception:
            # Do not log connection exceptions: they can contain credentials.
            logger.error("CityPulse storage transaction failed; verify database connectivity and permissions.")
            return await JSONResponse(
                {"detail": "Simulation storage is temporarily unavailable. Verify the database connection and retry."},
                status_code=503,
            )(scope, receive, send)
        finally:
            if token is not None:
                request_state.reset(token)
            if store is not None:
                store.db.close()
        for message in messages:
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [(b"cache-control", b"no-store")]
            await send(message)
