from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi.testclient import TestClient

from app.service import CopierService
from app.bitget import BitgetError
from test_desktop_workflow import settings
from test_paper import _signal


def test_simulation_create_stop_archive_and_error_routes(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_DATABASE_PATH', str(tmp_path / 'api.sqlite3'))
    from app import main
    service = CopierService(settings(tmp_path))
    monkeypatch.setattr(main, 'service', service)
    @asynccontextmanager
    async def lifespan(_):
        yield
        await service.bitget.close()
    monkeypatch.setattr(main.app.router, 'lifespan_context', lifespan)
    with TestClient(main.app) as client:
        payload = {'simulation_id':'测试-ID', 'initial_balance':'1000', 'selected_sources':['test']}
        created = client.post('/api/paper/account/reset', json=payload)
        assert created.status_code == 200
        assert created.json()['simulation_id'] == '测试-ID'
        assert client.post('/api/paper/account/reset', json=payload).status_code == 409
        service.paper.enqueue(_signal())
        service.paper.mark('BTCUSDT', Decimal('105'))
        async def offline(symbol): raise BitgetError('offline')
        service.bitget.market_price = offline
        assert client.post('/api/paper/account/stop').status_code == 409
        assert client.get('/api/paper/account?refresh=false').json()['lifecycle'] == 'running'
        async def price(symbol): return Decimal('110')
        service.bitget.market_price = price
        stopped = client.post('/api/paper/account/stop')
        assert stopped.json()['lifecycle'] == 'stopped'
        assert client.post('/api/paper/account/stop').json() == stopped.json()
        assert client.get('/api/paper/report').json()['daily'][0]['balance']
        assert len(client.get('/api/paper/reports').json()) == 1
        payload['simulation_id'] = '新-ID'
        assert client.post('/api/paper/account/reset', json=payload).status_code == 200
        archived = client.get('/api/paper/report', params={'simulation_id':'测试-ID'}).json()
        assert archived['account']['lifecycle'] == 'stopped'
        assert client.get('/api/paper/report?simulation_id=missing').status_code == 404
