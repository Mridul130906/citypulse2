from datetime import datetime, timezone

AREAS = [{"zone_id": z, "name": n} for z, n in [
    ("JAG", "Jagatpura"), ("MAL", "Malviya Nagar"), ("MAN", "Mansarovar"),
    ("VAI", "Vaishali Nagar"), ("CSC", "C-Scheme")]]
ROUTES = [
    {"route_id": "demo-east", "name": "East connector", "zone_ids": ["JAG", "MAL", "CSC"], "segments": [
        {"segment_id": "east-1", "zone_id": "JAG", "baseline": 10},
        {"segment_id": "east-2", "zone_id": "MAL", "baseline": 8},
        {"segment_id": "east-3", "zone_id": "CSC", "baseline": 7}]},
    {"route_id": "demo-west", "name": "West loop", "zone_ids": ["MAN", "VAI", "CSC"], "segments": [
        {"segment_id": "west-1", "zone_id": "MAN", "baseline": 12},
        {"segment_id": "west-2", "zone_id": "VAI", "baseline": 9},
        {"segment_id": "west-3", "zone_id": "CSC", "baseline": 7}]}]
START = datetime(2026, 9, 25, 2, 30, tzinfo=timezone.utc)
DEFAULTS = {"window_minutes": 30, "lag_minutes": 15, "rain_threshold": 15,
            "weather_cadence": 1, "transit_cadence": 1, "complaint_lag": 5, "transit_lag": 10,
            "complaint_probability": .85, "background_complaint_cadence": 15}
SOURCES = ("weather", "complaint", "transit")
