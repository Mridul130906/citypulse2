"""Raw adapters. No scenario information enters the normalized envelope."""
import hashlib
import re
from datetime import datetime, timezone
from .config import AREAS, ROUTES


def timestamp(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result.astimezone(timezone.utc)


def zone(value):
    key = re.sub(r"[^a-z]", "", str(value).lower())
    aliases = {"jagatpuraa": "JAG", "jagatpura": "JAG", "jagtpura": "JAG", "malviyanagr": "MAL"}
    for a in AREAS:
        aliases[re.sub(r"[^a-z]", "", a["name"].lower())] = a["zone_id"]
        aliases[a["zone_id"].lower()] = a["zone_id"]
    for alias in sorted(aliases, key=len, reverse=True):
        if key == alias or (len(alias) > 4 and alias in key):
            return aliases[alias]
    raise ValueError("Unknown or missing area")


def normalize(source, raw, received):
    flags = []
    if source == "weather":
        ident, place, time = raw["observation"], raw["area"], raw["time"]
        rain = float(raw["rain"])
        unit = raw.get("rain_unit", "mm/hour")
        if unit == "cm/hour":
            rain *= 10
            flags.append("unit_converted")
        elif unit != "mm/hour":
            raise ValueError("Unsupported rainfall unit")
        temp = float(raw["temperature_c"])
        if not 0 <= rain <= 300 or not -30 <= temp <= 65:
            raise ValueError("Invalid weather values")
        metrics = {"rain_mm_hour": rain, "temperature_c": temp, "condition": raw["condition"]}
        kind, description, severity = "weather", raw["condition"], 3 if rain >= 15 else 1
    elif source == "complaint":
        ident, place, time = raw["ticket"], raw.get("location"), raw["submitted"]
        description = " ".join(str(raw.get("text", "")).split())
        if not description:
            raise ValueError("Missing complaint text")
        clean = description.lower().replace("waterloging", "waterlogging").replace("blok", "block")
        kind = raw.get("category")
        valid = {"waterlogging", "road_obstruction", "potholes", "traffic_signal"}
        if raw.get("feed_heartbeat") is True:
            kind = "feed_status"
        elif kind not in valid:
            kind = next((k for k, terms in {"waterlogging": ["waterlog", "flood"], "road_obstruction": ["block", "obstruct"], "potholes": ["pothole"], "traffic_signal": ["signal"]}.items() if any(t in clean for t in terms)), "other")
            flags.append("category_inferred")
        severity = int(raw.get("severity", 2))
        metrics = {"category": kind, "count": 0 if kind == "feed_status" else 1}
    elif source == "transit":
        ident, place, time = raw["id"], raw["zone"], raw["epoch"]
        baseline, observed, speed = map(float, [raw["travel"]["base_seconds"], raw["travel"]["actual_seconds"], raw["speed_kph"]])
        route = next(r for r in ROUTES if r["route_id"] == raw["route"])
        segment = next(s for s in route["segments"] if s["segment_id"] == raw["segment"])
        if zone(place) != segment["zone_id"] or abs(baseline / 60 - segment["baseline"]) > .01:
            raise ValueError("Segment definition mismatch")
        if not 0 < baseline <= 21600 or not 0 < observed <= 21600 or not 0 < speed <= 150:
            raise ValueError("Invalid travel values")
        # Each fictional segment has a baseline speed of 30 km/h.
        if abs(speed - 30 * baseline / observed) > 1:
            raise ValueError("Speed inconsistent with segment duration")
        metrics = {"route_id": raw["route"], "segment_id": raw["segment"], "baseline_minutes": baseline / 60, "observed_minutes": observed / 60, "speed_kph": speed, "congestion": raw["congestion"]}
        kind, description, severity = "transit", "Simulated segment observation", 3 if observed > baseline * 1.3 else 1
    else:
        raise ValueError("Unknown source")
    if ident is None or not str(ident).strip():
        raise ValueError("Missing source observation ID")
    observed = timestamp(time)
    if observed > received:
        raise ValueError("Future observation")
    if (received - observed).total_seconds() > 120:
        flags.append("late_arrival")
    if not 1 <= severity <= 5:
        raise ValueError("Severity outside range")
    zid = zone(place)
    # Complaint fingerprints also suppress copied reports with new ticket numbers.
    signature = f"{source}:{ident}" if source != "complaint" else f"{source}:{zid}:{observed.isoformat()}:{description.lower()}"
    return {"event_id": hashlib.sha256(signature.encode()).hexdigest()[:24], "source_event_id": str(ident), "source_type": source, "zone_id": zid, "observed_at": observed.isoformat(), "received_at": received.isoformat(), "event_type": kind, "severity": severity, "metrics": metrics, "description": description, "quality_flags": flags, "is_synthetic": True}
