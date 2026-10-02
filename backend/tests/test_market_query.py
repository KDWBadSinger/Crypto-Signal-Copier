from contextlib import asynccontextmanager

import httpx
from fastapi.testclient import TestClient

from app.service import CopierService
from test_desktop_workflow import settings


def test_market_lookup_public_sorted_precision_and_errors(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_DATABASE_PATH', str(tmp_path/'api.sqlite3'))
    from app import main
    service = CopierService(settings(tmp_path))
    requests = []
    malformed = False
    def transport(request):
        requests.append(request)
        assert request.method == 'GET'
        assert 'ACCESS-KEY' not in request.headers
        if request.url.path.endswith('/tickers'):
            data = [{'symbol': 'UAIUSDT', 'lastPr': '.39748', 'high24h': '.4123', 'low24h': '.3891', 'change24h': '.01'}]
        else:
            assert request.url.params['symbol'] == 'UAIUSDT'
            assert request.url.params['granularity'] == '15m'
            data = [['broken']] if malformed else [
                ['1750000900000', '0', '0', '0', '.39748'],
                ['1750000000000', '0', '0', '0', '.3942'],
            ]
        return httpx.Response(200, json={'code': '00000', 'data': data})
    monkeypatch.setattr(main, 'service', service)
    @asynccontextmanager
    async def lifespan(_):
        await service.bitget._http.aclose()
        service.bitget._http = httpx.AsyncClient(base_url='https://api.bitget.com',transport=httpx.MockTransport(transport))
        yield
        await service.bitget.close()
    monkeypatch.setattr(main.app.router, 'lifespan_context', lifespan)
    with TestClient(main.app) as client:
        for code in ['uai', 'UAIUSDT', ' uai/usdt ']:
            response = client.get('/api/market/query', params={'symbol': code})
            assert response.status_code == 200
            payload = response.json()
            assert payload['symbol'] == 'UAIUSDT'
            assert payload['last_price'] == '0.39748'
            assert [p['close'] for p in payload['points']] == ['0.3942', '0.39748']
            assert payload['points'][0]['timestamp'] < payload['points'][1]['timestamp']
        before = len(requests)
        assert client.get('/api/market/query', params={'symbol': '<script>'}).status_code == 400
        assert len(requests) == before
        assert client.get('/api/market/query', params={'symbol': 'MISSINGCOIN'}).status_code == 404
        malformed = True
        assert client.get('/api/market/query', params={'symbol': 'UAI'}).status_code == 503

