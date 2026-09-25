from datetime import timedelta, timezone
from .normalization import timestamp
from .config import ROUTES


def summarize(state):
    feeds = state["feeds"]
    weather = state["weather"]
    parts = []
    if weather and feeds["weather"]["status"] == "fresh":
        rate = weather["rain_mm_hour"] / 60
        parts.append(f"{state['area']}: {weather['condition']} with rainfall of {rate:.3f} mm/min." if rate else f"{state['area']}: No rainfall is currently observed.")
    else:
        parts.append(f"{state['area']}: Current weather data is unavailable.")
    complaints = state["complaints"]
    window = round((timestamp(state["insight"]["time_window"]["end"]) - timestamp(state["insight"]["time_window"]["start"])).total_seconds() / 60)
    count, previous = complaints["count"], complaints["previous_count"]
    if feeds["complaint"]["status"] != "fresh":
        parts.append(f"There are {count} recorded civic reports in the last {window} simulated minutes, but the complaint feed is incomplete.")
    elif complaints["baseline_ready"]:
        change = "increased" if count > previous else "decreased" if count < previous else "stayed unchanged"
        parts.append(f"Civic reports {change} from {previous} to {count} across consecutive {window}-minute windows.")
    else:
        parts.append(f"There are {count} civic reports in the last {window} simulated minutes; more history is needed to assess a rise.")
    rule = state["insight"]["rule"]
    required = ("weather", "complaint", "transit") if rule == "rain_waterlogging_transit" else ("complaint", "transit")
    if rule in ("rain_waterlogging_transit", "obstruction_transit") and all(feeds[source]["status"] == "fresh" for source in required):
        parts.append(state["insight"]["explanation"])
    elif state["transit"]["slow_segments"]:
        parts.append("Slower transport is observed, but the available data does not establish its cause.")
    text = " ".join(parts)
    route = state["route"]
    if route:
        if route["available"]:
            delay = route["additional_minutes"]
            name = next((r["name"] for r in ROUTES if r["route_id"] == route["route_id"]), "Selected route")
            text += f" {name} is estimated at {route['current_minutes']:g} minutes versus its usual {route['baseline_minutes']:g}."
            text += f" Allow approximately {delay:g} extra minutes for this simulated journey." if delay else " No additional route delay is observed."
        else:
            text += " A complete route estimate is unavailable because required segment observations are missing or stale."
    else:
        text += f" {state['transit']['slow_segments']} observed transport segment(s) in this area are slower than baseline."
    unavailable = [f"{key} ({value['status']})" for key, value in state["feeds"].items() if value["status"] != "fresh"]
    if unavailable:
        text += " Data limitation: " + ", ".join(unavailable) + "."
    at = timestamp(state["insight"]["time_window"]["end"]).astimezone(timezone(timedelta(hours=5, minutes=30)))
    text += f" Assessed at {at:%H:%M} IST (simulated time)."
    return text
