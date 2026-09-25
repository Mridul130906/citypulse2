import asyncio
import psycopg
from fastapi.testclient import TestClient
from backend import main
from backend.analysis import area_state
from backend.serverless import ServerlessMiddleware, advance, restore, serialize, request_state


def test_snapshot_restores_demo_and_shared_controls():
    store, session = restore(None)
    session.fixture()
    payload = serialize(store, session)
    other_store, other_session = restore(payload)
    state = area_state(other_store, 'JAG', other_session.now, other_session.config, 'demo-east')
    assert state['route']['additional_minutes'] == 15
    assert not other_session.running
    event_id = state['insight']['supporting_event_ids'][0]
    assert other_store.evidence(event_id)['original']
    other_session.running = True
    _, third_session = restore(serialize(other_store, other_session))
    assert third_session.running


def test_request_clock_fraction_pause_and_idle():
    _, session = restore(None)
    session.speed = .25
    remainder = advance(session, 100, 0, 102)
    assert session.tick == 0 and remainder == .5
    remainder = advance(session, 102, remainder, 104)
    assert session.tick == 1 and remainder == 0
    assert advance(session, 104, remainder, 1000) == 0
    assert session.tick == 1
    session.running = False
    advance(session, 1000, 0, 1002)
    assert session.tick == 1
    session.running = True
    session.speed = 10
    advance(session, 1002, 0, 1030)
    assert session.tick == 21  # Bounded work even at maximum speed.


def test_retention_preserves_recent_observations():
    store, session = restore(None)
    session.step(245)
    restored, _ = restore(serialize(store, session))
    from datetime import timedelta
    cutoff = (session.now - timedelta(minutes=240)).isoformat()
    assert restored.db.execute('SELECT COUNT(*) FROM events WHERE observed < ?', (cutoff,)).fetchone()[0] == 0
    assert restored.events('JAG', 'weather')


def test_missing_database_is_actionable_and_static_routes_work(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('POSTGRES_URL', raising=False)
    client = TestClient(ServerlessMiddleware(main.app, enabled=True))
    response = client.get('/api/dashboard/JAG')
    assert response.status_code == 503
    assert 'DATABASE_URL' in response.json()['detail']
    assert client.get('/api/areas').status_code == 200
    assert client.get('/api/live').json()['transport'] == 'poll'


def test_request_context_isolates_sessions():
    async def read_fixture(zone):
        store, session = restore(None)
        session.fixture(zone)
        token = request_state.set((store, session))
        try:
            await asyncio.sleep(0)
            result = await main.dashboard(zone)
            assert result['state']['simulation']['target_zone'] == zone
            assert len(result['overview']) == 5
        finally:
            request_state.reset(token)
    async def run():
        await asyncio.gather(read_fixture('JAG'), read_fixture('MAL'))
    asyncio.run(run())
    assert request_state.get() is None


def test_middleware_commits_before_returning_and_restores_next_request(monkeypatch):
    """Exercise the ASGI/transaction boundary with an in-memory Postgres stand-in.

    Snapshot encoding and the app are real; network SQL still needs a deployed
    database smoke test.
    """
    saved = {}
    fail_commit = False

    class Connection:
        def __init__(self):
            self.pending = dict(saved)
            self.row = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, kind, value, traceback):
            if kind is None:
                if fail_commit:
                    raise RuntimeError('simulated commit failure')
                saved.update(self.pending)

        async def execute(self, sql, args=None):
            if sql.startswith('SELECT payload'):
                self.row = self.pending.get(args[0])
            elif sql.startswith('INSERT INTO citypulse_snapshots'):
                self.pending[args[0]] = args[1:]
            return self

        async def fetchone(self):
            return self.row

    async def connect(*args, **kwargs):
        return Connection()

    monkeypatch.setattr(psycopg.AsyncConnection, 'connect', connect)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://test.invalid/test')
    monkeypatch.setenv('CITYPULSE_SESSION_KEY', 'test')
    client = TestClient(ServerlessMiddleware(main.app, enabled=True))
    response = client.post('/api/simulation', json={'action': 'fixture'})
    assert response.status_code == 200
    assert saved['test'][0]
    response = client.get('/api/dashboard/JAG?route=demo-east')
    assert response.json()['state']['route']['additional_minutes'] == 15
    assert response.headers['cache-control'] == 'no-store'
    before = saved['test']
    fail_commit = True
    assert client.post('/api/simulation', json={'action': 'reset'}).status_code == 503
    assert saved['test'] == before
    fail_commit = False
    assert client.get('/api/simulation').json()['tick'] == 95
    assert request_state.get() is None
