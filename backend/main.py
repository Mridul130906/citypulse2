import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .config import AREAS, ROUTES, SOURCES
from .persistence import Store
from .session import Session
from .analysis import area_state
from .summaries import summarize
from .normalization import timestamp

store = Store(os.getenv("CITYPULSE_DB", str(Path(__file__).parent / "citypulse.sqlite")))
session = Session(store)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(session.run())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    store.save(session.state())


app = FastAPI(title="CityPulse — Simulation", lifespan=lifespan)


def check_zone(zid):
    if zid not in [a["zone_id"] for a in AREAS]:
        raise HTTPException(404, "Unknown area")


def snapshot(zid, route=None):
    check_zone(zid)
    if route and not any(r["route_id"] == route and zid in r["zone_ids"] for r in ROUTES):
        raise HTTPException(400, "Choose a route passing through the area")
    result = area_state(store, zid, session.now, session.config, route)
    for source in SOURCES:
        heartbeat = session.last_received.get(source)
        result["feeds"][source]["observation_status"] = result["feeds"][source]["status"]
        result["feeds"][source]["available"] = True
        if source in session.disabled or not heartbeat or (session.now - timestamp(heartbeat)).total_seconds() > 120:
            result["feeds"][source]["available"] = False
            if source in session.disabled:
                result["feeds"][source]["status"] = "unavailable"
            elif not heartbeat:
                result["feeds"][source]["status"] = "missing"
            elif result["feeds"][source]["status"] == "fresh":
                result["feeds"][source]["status"] = "unavailable"
        elif source == "complaint":
            result["feeds"][source]["status"] = "fresh"
        result["feeds"][source]["last_received"] = heartbeat
    result["summary"] = summarize(result)
    result["simulation"] = session.state()
    return result


@app.get("/api/health")
async def health():
    return {"status": "ok", "is_synthetic": True}


@app.get("/api/areas")
async def areas():
    return AREAS


@app.get("/api/routes")
async def routes():
    return ROUTES


@app.get("/api/state/{zone_id}")
async def state(zone_id: str, route: str | None = None):
    return snapshot(zone_id, route)


@app.get("/api/overview")
async def overview():
    return [{"zone_id": a["zone_id"], "name": a["name"], "rule": snapshot(a["zone_id"])["insight"]["rule"]} for a in AREAS]


@app.get("/api/events")
async def events(zone_id: str | None = None, source: Literal['weather', 'complaint', 'transit'] | None = None, since: str | None = None, until: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    if zone_id:
        check_zone(zone_id)
    try:
        start = timestamp(since).isoformat() if since else None
        end = timestamp(until).isoformat() if until else None
    except ValueError as error:
        raise HTTPException(422, str(error))
    rows = store.events(zone_id, source, start, end, limit + 1, offset)
    return {"items": rows[:limit], "limit": limit, "offset": offset, "has_more": len(rows) > limit}


@app.get("/api/evidence/{event_id}")
async def evidence(event_id: str):
    record = store.evidence(event_id)
    if not record:
        raise HTTPException(404, "Event not found")
    return record


@app.get("/api/insights/{zone_id}")
async def insights(zone_id: str):
    return snapshot(zone_id)["insight"]


@app.get("/api/simulation")
async def simulation():
    return session.state()


class Controls(BaseModel):
    action: Literal["update", "reset", "fixture", "advance"] = "update"
    running: bool | None = None
    scenario: Literal["normal", "rain", "rush", "obstruction", "missing"] | None = None
    target_zone: str | None = None
    speed: float | None = Field(None, ge=.25, le=10)
    disabled: list[Literal["weather", "complaint", "transit"]] | None = None
    minutes: int = Field(1, ge=1, le=120)
    window_minutes: int | None = Field(None, ge=10, le=60)
    lag_minutes: int | None = Field(None, ge=1, le=30)
    weather_cadence: int | None = Field(None, ge=1, le=10)
    transit_cadence: int | None = Field(None, ge=1, le=5)
    complaint_lag: int | None = Field(None, ge=0, le=20)
    transit_lag: int | None = Field(None, ge=0, le=20)
    complaint_probability: float | None = Field(None, ge=.1, le=1)
    background_complaint_cadence: int | None = Field(None, ge=1, le=30)


@app.post("/api/simulation")
async def control(body: Controls):
    if body.target_zone is not None:
        check_zone(body.target_zone)
        session.target_zone = body.target_zone
    if body.action == "reset":
        session.reset(body.target_zone)
    elif body.action == "fixture":
        session.fixture(body.target_zone)
    elif body.action == "advance":
        session.step(body.minutes)
    if body.scenario is not None:
        session.scenario = body.scenario
        session.phase_start = session.tick
    for key in ("running", "speed", "disabled"):
        if getattr(body, key) is not None:
            setattr(session, key, getattr(body, key))
    for key in session.config:
        if hasattr(body, key) and getattr(body, key) is not None:
            session.config[key] = getattr(body, key)
    session.revision += 1
    store.save(session.state())
    return session.state()


@app.get("/api/live")
async def live():
    async def stream():
        previous = -1
        heartbeat = 0
        while True:
            if previous != session.revision:
                previous = session.revision
                yield f"id: {previous}\nretry: 1500\ndata: {json.dumps({'revision': previous})}\n\n"
            else:
                heartbeat += 1
                if heartbeat % 20 == 0:
                    yield ": heartbeat\n\n"
            await asyncio.sleep(.5)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if dist.exists():
    app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
