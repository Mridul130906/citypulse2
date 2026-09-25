from datetime import timedelta
import pytest
from backend.config import START, DEFAULTS
from backend.normalization import normalize, timestamp
from backend.persistence import Store
from backend.session import Session
from backend.analysis import area_state, route_delay
from backend import simulators
from backend.summaries import summarize


def weather(**changes):
    return {"observation": "w1", "area": "JAGATPURA", "time": START.isoformat(), "rain": 2, "temperature_c": 30, "condition": "Rain", **changes}


def test_normalization_alias_units_and_timezone():
    event = normalize("weather", weather(area="jagtpura", time="2026-09-25T08:00:00+05:30", rain_unit="cm/hour"), START)
    assert event["zone_id"] == "JAG"
    assert event["observed_at"] == START.isoformat()
    assert event["metrics"]["rain_mm_hour"] == 20
    assert event["is_synthetic"]
    with pytest.raises(ValueError):
        timestamp("2026-09-25T08:00:00")


def test_duplicate_and_quarantine():
    store = Store()
    assert store.ingest("weather", weather(), START)
    assert store.ingest("weather", weather(), START) is None
    assert store.ingest("weather", weather(observation="bad", area="unknown"), START) is None
    assert len(store.events()) == 1
    assert store.db.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0] == 1


def test_complaint_inference_original_and_duplicate():
    store = Store()
    raw = {"ticket": "one", "location": "near jagatpuraa", "submitted": START.isoformat(), "text": "  waterloging  near crossing  "}
    event = store.ingest("complaint", raw, START)
    assert event["event_type"] == "waterlogging"
    assert store.evidence(event["event_id"])["original"] == raw
    assert store.ingest("complaint", {**raw, "ticket": "copy"}, START) is None


def test_out_of_order_observation_not_arrival_freshness():
    store = Store()
    now = START + timedelta(minutes=40)
    event = store.ingest("weather", weather(), now)
    assert "late_arrival" in event["quality_flags"]
    state = area_state(store, "JAG", now, DEFAULTS)
    assert state["feeds"]["weather"]["status"] == "stale"
    assert state["weather"] is None
    assert store.ingest("weather", weather(observation="future", time=(now+timedelta(minutes=1)).isoformat()), now) is None


def test_fixture_supported_rain_insight_and_exact_delay():
    store = Store()
    session = Session(store)
    session.fixture()
    state = area_state(store, "JAG", session.now, DEFAULTS, "demo-east")
    assert state["insight"]["rule"] == "rain_waterlogging_transit"
    assert state["complaints"]["baseline_ready"]
    assert state["route"]["additional_minutes"] == 15
    assert state["route"]["current_minutes"] == 40
    for eid in state["insight"]["supporting_event_ids"]:
        assert store.evidence(eid)["event"]["zone_id"] == "JAG"
    assert area_state(store, "MAL", session.now, DEFAULTS)["insight"]["rule"] == "normal"
    assert "may be linked" in state["insight"]["explanation"]


def test_rain_cycles_keep_weather_changing_and_transit_aligned():
    session = Session(Store())
    session.scenario = "rain"
    session.step(90)  # Second cycle, with rain and its lagged transit delay active.
    readings = []
    for _ in range(4):
        state = area_state(session.store, "JAG", session.now, session.config, "demo-east")
        readings.append(state["weather"]["rain_mm_hour"])
        assert state["feeds"]["weather"]["observed_at"] == session.now.isoformat()
        assert state["route"]["additional_minutes"] == 15
        assert area_state(session.store, "MAL", session.now, session.config)["weather"]["rain_mm_hour"] == 0
        session.step()
    assert all(22 <= value <= 34 for value in readings)
    assert len(set(readings)) > 1


@pytest.mark.parametrize("zone,route", [("JAG", "demo-east"), ("MAL", "demo-east"), ("MAN", "demo-west"), ("VAI", "demo-west"), ("CSC", "demo-east")])
def test_every_neighborhood_has_live_route_delays(zone, route):
    session = Session(Store())
    session.fixture(target_zone=zone)
    state = area_state(session.store, zone, session.now, session.config, route)
    assert state["route"]["available"]
    assert state["route"]["additional_minutes"] == 15
    session.step(21)  # Rain eases in this neighborhood, updating its route estimate.
    state = area_state(session.store, zone, session.now, session.config, route)
    assert state["route"]["additional_minutes"] == 5
    session.step(10)
    state = area_state(session.store, zone, session.now, session.config, route)
    assert state["route"]["additional_minutes"] == 0


def test_repeated_segments_never_double_counted():
    store = Store()
    session = Session(store)
    session.fixture()
    result = route_delay(store, "demo-east", session.now, DEFAULTS)
    assert len(result["supporting_event_ids"]) == 3
    assert result["additional_minutes"] == 15
    assert len(store.events(source="transit")) > 100


@pytest.mark.parametrize("scenario,expected", [("rush", "transit_disruption"), ("obstruction", "obstruction_transit")])
def test_non_weather_disruption(scenario, expected):
    store = Store()
    session = Session(store)
    session.step(65)
    session.scenario, session.phase_start = scenario, session.tick
    session.step(30)
    state = area_state(store, "JAG", session.now, DEFAULTS)
    assert state["insight"]["rule"] == expected
    assert "rain" not in state["insight"]["explanation"].lower()


def test_baseline_building_and_gap():
    store = Store()
    session = Session(store)
    session.step(20)
    assert not area_state(store, "JAG", session.now, DEFAULTS)["complaints"]["baseline_ready"]
    session.step(45)
    assert area_state(store, "JAG", session.now, DEFAULTS)["complaints"]["baseline_ready"]
    session.disabled = ["complaint"]
    session.step(5)
    assert not area_state(store, "JAG", session.now, DEFAULTS)["complaints"]["baseline_ready"]


def test_missing_feed_and_stale_route():
    store = Store()
    session = Session(store)
    session.fixture()
    session.disabled = ["weather", "transit"]
    session.step(15)
    state = area_state(store, "JAG", session.now, DEFAULTS, "demo-east")
    assert state["weather"] is None
    assert not state["route"]["available"]
    assert state["route"]["additional_minutes"] is None


def test_missing_single_segment_blocks_estimate():
    store = Store()
    rows = simulators.transit(START, 0, 30, "rain", DEFAULTS, 42)
    for raw in rows:
        if raw["segment"] != "east-2":
            store.ingest("transit", raw, START)
    result = route_delay(store, "demo-east", START, DEFAULTS)
    assert result["missing_segments"] == ["east-2"]
    assert result["current_minutes"] is None


def test_time_window_excludes_old_correlations():
    store = Store()
    session = Session(store)
    session.fixture()
    session.scenario = "normal"  # Stop new rain cycles while old evidence expires.
    session.step(80)
    state = area_state(store, "JAG", session.now, DEFAULTS)
    assert state["insight"]["rule"] == "normal"


def test_bad_transit_quarantined():
    store = Store()
    row = simulators.transit(START, 0, 0, "normal", DEFAULTS, 42)[0]
    assert store.ingest("transit", {**row, "speed_kph": -10}, START) is None
    assert store.ingest("transit", {**row, "speed_kph": 140}, START) is None


def test_analysis_independent_of_generator_state():
    store = Store()
    session = Session(store)
    session.fixture()
    session.scenario = "normal"
    assert area_state(store, "JAG", session.now, DEFAULTS)["insight"]["rule"] == "rain_waterlogging_transit"


def test_pause_and_persistence(tmp_path):
    path = str(tmp_path / "test.sqlite")
    store = Store(path)
    session = Session(store)
    session.step(5)
    restored = Session(Store(path))
    assert restored.tick == 5
    assert not restored.running
    assert area_state(store, "JAG", session.now, DEFAULTS)["feeds"]["weather"]["status"] == "fresh"


def test_summary_connects_observed_feeds_and_route_without_lateness_prediction():
    session = Session(Store())
    session.fixture()
    state = area_state(session.store, "JAG", session.now, session.config, "demo-east")
    text = summarize(state)
    assert f"{state['weather']['rain_mm_hour'] / 60:.3f} mm/min" in text
    assert f"from {state['complaints']['previous_count']} to {state['complaints']['count']}" in text
    assert "may be linked" in text
    assert "40 minutes versus its usual 25" in text
    assert "15 extra minutes" in text
    assert "chance" not in text and "arrive late" not in text
    state["feeds"]["weather"]["status"] = "unavailable"
    text = summarize(state)
    assert "Current weather data is unavailable" in text
    assert "may be linked" not in text
    assert "mm/min" not in text


def test_summary_does_not_invent_a_rise_without_baseline_or_cause():
    session = Session(Store())
    state = area_state(session.store, "JAG", session.now, session.config, "demo-east")
    text = summarize(state)
    assert "more history is needed" in text
    assert "No additional route delay" in text
    assert "may be linked" not in text
    session.disabled = ["transit"]
    session.step(5)
    state = area_state(session.store, "JAG", session.now, session.config, "demo-east")
    assert "complete route estimate is unavailable" in summarize(state)
