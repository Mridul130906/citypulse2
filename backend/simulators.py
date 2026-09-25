"""Independent raw formats and cadences, sharing only a synthetic scenario clock."""
import random
from datetime import timedelta
from .config import AREAS, ROUTES


def weather(now, tick, phase, scenario, config, seed, target_zone="JAG"):
    if tick % config["weather_cadence"]:
        return []
    rng = random.Random(seed + tick)
    rows = []
    for area in AREAS:
        rain = round(rng.uniform(22, 34), 1) if scenario == "rain" and area["zone_id"] == target_zone and 10 <= phase < 50 else 0
        if scenario == "rain" and area["zone_id"] == target_zone and 50 <= phase < 60:
            rain = round(rng.uniform(2, 10), 1)
        rows.append({"observation": f"w-{tick}-{area['zone_id']}", "area": area["name"].upper(), "time": now.isoformat(), "rain": rain, "temperature_c": round(29 + rng.random() * 3, 1), "condition": "Heavy rain" if rain >= 15 else "Light rain" if rain else "Clear"})
    return rows


def complaints(now, tick, phase, scenario, config, seed, target_zone="JAG"):
    rng = random.Random(seed * 17 + tick)
    rows = []
    for area in AREAS:
        active_rain = scenario == "rain" and area["zone_id"] == target_zone and 10 + config["complaint_lag"] <= phase < 55
        obstruction = scenario == "obstruction" and area["zone_id"] == target_zone and 10 <= phase < 55
        if not ((active_rain or obstruction) and rng.random() < config["complaint_probability"] or (tick % config["background_complaint_cadence"] == 0 and rng.random() < .8)):
            continue
        kind = "waterlogging" if active_rain else "road_obstruction" if obstruction else "potholes"
        text = {"waterlogging": "waterloging near the crossing", "road_obstruction": "Road blok near junction", "potholes": "Pothole on local street"}[kind]
        row = {"ticket": f"c-{tick}-{area['zone_id']}", "location": "jagtpura" if area["zone_id"] == "JAG" else area["name"].lower(), "submitted": (now - timedelta(minutes=8 if tick % 13 == 0 else 0)).isoformat(), "text": text, "severity": 3, "category": kind if tick % 3 else None}
        rows.append(row)
        if tick % 7 == 0:
            rows.append(dict(row))
        if tick % 19 == 0:
            rows.append({**row, "ticket": row["ticket"] + "-bad", "location": None})
    return rows


def transit(now, tick, phase, scenario, config, seed, target_zone="JAG"):
    if tick % config["transit_cadence"]:
        return []
    rows = []
    for route in ROUTES:
        for segment in route["segments"]:
            delay = 0
            if segment["zone_id"] == target_zone:
                if scenario == "rain" and 10 + config["transit_lag"] <= phase < 50:
                    delay = 15
                elif scenario == "obstruction" and 15 <= phase < 55:
                    delay = 12
                elif scenario == "rain" and 50 <= phase < 60:
                    delay = 5
            if scenario == "rush" and 10 <= phase < 55:
                delay = 4
            base = segment["baseline"]
            rows.append({"id": f"t-{tick}-{segment['segment_id']}", "route": route["route_id"], "segment": segment["segment_id"], "zone": segment["zone_id"], "epoch": now.timestamp(), "travel": {"base_seconds": base * 60, "actual_seconds": (base + delay) * 60}, "speed_kph": round(30 * base / (base + delay), 2), "congestion": "high" if delay else "low"})
    return rows
