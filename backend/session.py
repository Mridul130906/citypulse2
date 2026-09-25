import asyncio
import json
from datetime import timedelta
from . import simulators
from .config import START, DEFAULTS, SOURCES, AREAS
from .normalization import timestamp


class Session:
    def __init__(self, store, *, resume=False):
        self.store = store
        self.config = dict(DEFAULTS)
        self.tick = 0
        self.phase_start = 0
        self.scenario = "normal"
        self.target_zone = "JAG"
        self.running = True
        self.speed = 2
        self.disabled = []
        self.seed = 42
        self.revision = 0
        self.last_received = {}
        row = store.db.execute("SELECT body FROM session WHERE id=1").fetchone()
        if row:
            saved = json.loads(row[0])
            for key in ("tick", "phase_start", "scenario", "target_zone", "speed", "disabled", "seed", "revision", "last_received", "config"):
                if key in saved:
                    setattr(self, key, saved[key])
            self.running = bool(saved.get("running", False)) if resume else False
            self.config = {**DEFAULTS, **self.config}
        else:
            self.emit()

    @property
    def now(self):
        return START + timedelta(minutes=self.tick)

    def state(self):
        return {"tick": self.tick, "phase_start": self.phase_start, "scenario": self.scenario, "target_zone": self.target_zone, "running": self.running, "speed": self.speed, "disabled": self.disabled, "seed": self.seed, "revision": self.revision, "clock": self.now.isoformat(), "last_received": self.last_received, "config": self.config, "label": "Simulation — synthetic data"}

    def emit(self):
        phase = self.tick - self.phase_start
        # Repeat the rainfall demonstration with all related feeds on the same clock.
        if self.scenario == "rain":
            phase %= 60
        for source, generator in [("weather", simulators.weather), ("complaint", simulators.complaints), ("transit", simulators.transit)]:
            if source in self.disabled or (self.scenario == "missing" and source == "weather" and phase >= 10):
                continue
            rows = generator(self.now, self.tick, phase, self.scenario, self.config, self.seed, self.target_zone)
            if source == "complaint":
                rows += [{"ticket": f"heartbeat-{self.tick}-{a['zone_id']}", "location": a["zone_id"], "submitted": self.now.isoformat(), "text": "Complaint feed operational", "feed_heartbeat": True} for a in AREAS]
            # Heartbeat tracks operational availability separately from observations.
            self.last_received[source] = self.now.isoformat()
            for raw in rows:
                self.store.ingest(source, raw, self.now)
        self.revision += 1
        self.store.save(self.state())

    def step(self, count=1):
        for _ in range(count):
            self.tick += 1
            self.emit()

    def reset(self, target_zone=None):
        self.store.reset()
        self.tick = self.phase_start = 0
        self.disabled = []
        self.scenario = "normal"
        if target_zone:
            self.target_zone = target_zone
        self.last_received = {}
        self.emit()

    def fixture(self, target_zone=None):
        self.config = dict(DEFAULTS)
        self.reset(target_zone=target_zone or self.target_zone)
        self.step(65)
        self.scenario = "rain"
        self.phase_start = self.tick
        self.step(30)
        self.running = False
        self.revision += 1
        self.store.save(self.state())

    async def run(self):
        elapsed = 0.0
        while True:
            await asyncio.sleep(.25)
            if not self.running:
                elapsed = 0
                continue
            elapsed += .25 * self.speed
            if elapsed >= 1:
                count = int(elapsed)
                elapsed -= count
                self.step(count)
