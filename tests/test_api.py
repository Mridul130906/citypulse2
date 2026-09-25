import pytest
from fastapi.testclient import TestClient
from backend.persistence import Store
from backend.session import Session
from backend import main


@pytest.fixture
def client(monkeypatch):
    store = Store()
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "session", Session(store))
    return TestClient(main.app)


def test_api_fixture_and_evidence(client):
    assert client.get('/api/health').json()['is_synthetic']
    assert len(client.get('/api/areas').json()) == 5
    assert len(client.get('/api/routes').json()) == 2
    assert client.post('/api/simulation', json={'action':'fixture'}).status_code == 200
    state = client.get('/api/state/JAG?route=demo-east').json()
    assert state['route']['additional_minutes'] == 15
    assert '15 extra minutes' in state['summary']
    assert state['insight']['rule'] == 'rain_waterlogging_transit'
    eid = state['insight']['supporting_event_ids'][0]
    assert client.get('/api/evidence/'+eid).json()['original']
    assert client.get('/api/insights/JAG').json()['rule'] == state['insight']['rule']


def test_api_degradation_and_validation(client):
    client.post('/api/simulation', json={'action':'fixture'})
    client.post('/api/simulation', json={'disabled':['weather','transit']})
    state = client.get('/api/state/JAG?route=demo-east').json()
    assert state['feeds']['weather']['status'] == 'unavailable'
    assert 'unavailable' in state['summary']
    client.post('/api/simulation', json={'action':'advance','minutes':15})
    state = client.get('/api/state/JAG?route=demo-east').json()
    assert not state['route']['available']
    assert state['weather'] is None
    assert client.get('/api/state/JAG?route=demo-west').status_code == 400
    assert client.get('/api/state/UNKNOWN').status_code == 404
    assert client.get('/api/events?limit=-1').status_code == 422
    assert client.get('/api/events?since=invalid').status_code == 422
    assert client.post('/api/simulation', json={'speed':999}).status_code == 422


def test_filtered_pagination(client):
    client.post('/api/simulation', json={'action':'fixture'})
    one = client.get('/api/events?zone_id=JAG&source=weather&limit=2').json()
    two = client.get('/api/events?zone_id=JAG&source=weather&limit=2&offset=2').json()
    assert one['has_more']
    assert len(one['items']) == 2
    assert not {e['event_id'] for e in one['items']} & {e['event_id'] for e in two['items']}
    assert all(e['zone_id'] == 'JAG' and e['source_type'] == 'weather' for e in one['items'])
