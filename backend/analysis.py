import hashlib
from datetime import timedelta
from .config import AREAS, ROUTES, SOURCES
from .normalization import timestamp


def age(event, now):
    return (now - timestamp(event["observed_at"])).total_seconds() / 60


def freshness(store, zid, now, config):
    result = {}
    for source in SOURCES:
        rows = store.events(zid, source, until=now.isoformat(), limit=1)
        # Complaints are irregular. Zero reports is meaningful when the ingestion feed is alive.
        ttl = {"weather": config["weather_cadence"] * 2 + 1, "transit": config["transit_cadence"] * 2 + 1, "complaint": 30}[source]
        result[source] = {"status": "missing" if not rows else "fresh" if age(rows[0], now) <= ttl else "stale", "observed_at": rows[0]["observed_at"] if rows else None, "age_minutes": round(age(rows[0], now), 1) if rows else None}
    return result


def route_delay(store, rid, now, config):
    route = next(r for r in ROUTES if r["route_id"] == rid)
    latest = {}
    for event in store.events(source="transit", since=(now - timedelta(minutes=config["transit_cadence"] * 2 + 1)).isoformat(), until=now.isoformat()):
        m = event["metrics"]
        if m["route_id"] == rid:
            latest.setdefault(m["segment_id"], event)
    missing = [s["segment_id"] for s in route["segments"] if s["segment_id"] not in latest]
    baseline = sum(s["baseline"] for s in route["segments"])
    current = sum(latest[s["segment_id"]]["metrics"]["observed_minutes"] for s in route["segments"]) if not missing else None
    return {"route_id": rid, "available": not missing, "baseline_minutes": baseline, "current_minutes": current, "additional_minutes": max(0, round(current - baseline, 1)) if current is not None else None, "observed_at": min(e["observed_at"] for e in latest.values()) if latest else None, "missing_segments": missing, "supporting_event_ids": [e["event_id"] for e in latest.values()]}


def area_state(store, zid, now, config, rid=None):
    window = config["window_minutes"]
    start = now - timedelta(minutes=window)
    events = store.events(zid, since=(start + timedelta(microseconds=1)).isoformat(), until=now.isoformat())
    feeds = freshness(store, zid, now, config)
    weather = next((e for e in events if e["source_type"] == "weather"), None)
    complaints = [e for e in events if e["source_type"] == "complaint" and e["event_type"] != "feed_status"]
    previous = store.events(zid, "complaint", since=(start - timedelta(minutes=window) + timedelta(microseconds=1)).isoformat(), until=start.isoformat())
    heartbeats = [e for e in previous + events if e["event_type"] == "feed_status"]
    previous = [e for e in previous if e["event_type"] != "feed_status"]
    # Normalized heartbeats distinguish zero reports from gaps in complaint collection.
    baseline_ready = len({e["observed_at"] for e in heartbeats}) >= window * 2
    threshold = max(4, 2 * len(previous))
    spike = baseline_ready and len(complaints) >= threshold
    latest_segments = {}
    for e in events:
        if e["source_type"] == "transit" and age(e, now) <= config["transit_cadence"] * 2 + 1:
            latest_segments.setdefault(e["metrics"]["segment_id"], e)
    slow = [e for e in latest_segments.values() if e["metrics"]["observed_minutes"] >= e["metrics"]["baseline_minutes"] * 1.3]
    heavy = bool(weather and feeds["weather"]["status"] == "fresh" and weather["metrics"]["rain_mm_hour"] >= config["rain_threshold"])
    wet = [e for e in complaints if e["event_type"] == "waterlogging"]
    blocks = [e for e in complaints if e["event_type"] == "road_obstruction"]
    lag = timedelta(minutes=config["lag_minutes"])
    rain_evidence = [e for e in events if e["source_type"] == "weather" and e["metrics"]["rain_mm_hour"] >= config["rain_threshold"]]
    linked_wet = [c for c in wet if any(timestamp(w["observed_at"]) <= timestamp(c["observed_at"]) <= timestamp(w["observed_at"]) + lag for w in rain_evidence)]
    wet_matches = [c for c in linked_wet if any(timestamp(c["observed_at"]) <= timestamp(t["observed_at"]) <= timestamp(c["observed_at"]) + lag for t in slow)]
    block_matches = [c for c in blocks if any(timestamp(c["observed_at"]) <= timestamp(t["observed_at"]) <= timestamp(c["observed_at"]) + lag for t in slow)]
    signals = []
    if heavy:
        signals.append("Heavy rainfall")
    if spike:
        signals.append("Elevated complaints")
    if slow:
        signals.append("Slower transport")
    rule, explanation, support = "normal", "No disruption rule is currently triggered by the available observations.", []
    if heavy and spike and len(wet_matches) >= 3 and slow:
        rule = "rain_waterlogging_transit"
        explanation = "Heavy rain, increased waterlogging complaints, and slower transport may be linked. The observations overlap in this area; this does not establish a cause."
        support = rain_evidence + wet_matches + slow
    elif len(block_matches) >= 2 and slow:
        rule, explanation, support = "obstruction_transit", "Road obstruction complaints coincide with slower transport and may be linked.", block_matches + slow
    elif slow:
        rule, explanation, support = "transit_disruption", "Transport is slower than its simulated baseline. Available evidence does not establish why.", slow
    elif heavy:
        rule, explanation, support = "heavy_rain", "Heavy rainfall is being observed. There is not yet enough matching evidence to link it with transport disruption.", [weather]
    name = next(a["name"] for a in AREAS if a["zone_id"] == zid)
    insight = {"insight_id": hashlib.sha256(f"{zid}:{rule}:{now.isoformat()}".encode()).hexdigest()[:16], "area": name, "zone_id": zid, "time_window": {"start": start.isoformat(), "end": now.isoformat()}, "detected_signals": signals, "supporting_event_ids": list(dict.fromkeys(e["event_id"] for e in support)), "explanation": explanation, "data_availability": feeds, "rule": rule, "is_synthetic": True}
    return {"zone_id": zid, "area": name, "feeds": feeds, "weather": weather["metrics"] if weather and feeds["weather"]["status"] == "fresh" else None, "complaints": {"count": len(complaints), "previous_count": len(previous), "baseline_ready": baseline_ready, "spike": spike, "threshold": threshold, "trend": "Building baseline" if not baseline_ready else "Elevated" if spike else "Within baseline"}, "transit": {"slow_segments": len(slow), "observed_segments": len(latest_segments)}, "route": route_delay(store, rid, now, config) if rid else None, "insight": insight, "events": [e for e in events if e["event_type"] != "feed_status"][:18]}
