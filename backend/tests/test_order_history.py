import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from app.bitget import BitgetError
from app.paper import PaperTradingStore, PaperTradingError
from app.storage import SignalStore
from app.uta_executor import UtaExecutor
from app.uta_risk import UtaRiskLimits
from app.uta_runtime import UtaRuntime
from app.service import CopierService
from test_management import opened
from test_desktop_workflow import settings, event, FakeTelegram
from test_uta_executor import setup, sample
from test_uta_lifecycle import trigger, message


def evidence(mid=2):
    return {'chat_id': -100123, 'message_id': mid, 'source_name': '测试博主',
            'text': '稳健带成本损', 'sent_at': '2026-10-02T00:10:42+00:00'}


def test_paper_history_is_immutable_durable_and_idempotent(tmp_path):
    paper, signal = opened(tmp_path)
    trade = paper.snapshot().trades[0]
    msg = evidence()
    paper.manage(-100123, 2, trade.id, ['breakeven'], D(110), message=msg)
    first = paper.order_detail(trade.id)
    assert [e['action'] for e in first['events']] == ['entry_pending', 'opened', 'stop_adjusted']
    assert first['events'][-1]['sources'][0] == msg
    assert first['signal']['stop_loss'] == '90'
    msg['text'] = 'edited message'
    restored = PaperTradingStore(paper.path)
    restored.manage(-100123, 2, trade.id, ['breakeven'], D(110), message=msg)
    assert restored.order_detail(trade.id) == first
    restored.mark('BTCUSDT', restored.snapshot().trades[0].stop_loss)
    last = restored.order_detail(trade.id)['events'][-1]
    assert last['action'] == 'closed'
    assert last['actor'] == 'system'
    assert last['sources'][0]['text'] == '稳健带成本损'
    assert last['sources'][0]['sent_at'] == evidence()['sent_at']


def test_paper_targets_keep_original_evidence_after_stop_update(tmp_path):
    paper, signal = opened(tmp_path)
    trade = paper.snapshot().trades[0]
    paper.manage(-100123, 2, trade.id, ['breakeven'], D(110), message=evidence())
    paper.mark('BTCUSDT', D(120))
    last = paper.order_detail(trade.id)['events'][-1]
    assert last['action'] == 'reduced'
    assert last['sources'][0]['message_id'] == signal.source_message_id
    assert last['sources'][0]['sent_at'] is None


def test_paper_manual_close_archive_and_rollback(tmp_path):
    paper, _ = opened(tmp_path, '空', 5)
    trade = paper.snapshot().trades[0]
    before = paper.order_detail(trade.id)
    with pytest.raises(PaperTradingError):
        paper.manage(-100123, 2, trade.id, ['breakeven', 'runner'], D(90), message=evidence())
    assert paper.order_detail(trade.id) == before
    paper.close_positions({'BTCUSDT': D(95)}, trade.id)
    closed = paper.order_detail(trade.id)['events'][-1]
    assert closed['actor'] == 'user' and closed['sources'] == []
    paper.settle({})
    paper.reset(D(1000), 10, D('.001'), simulation_id='next')
    assert paper.report('management')['order_history'][trade.id]['events'][-1] == closed


def test_paper_cancel_pending_records_once(tmp_path):
    paper, signal = opened(tmp_path)
    later = signal.model_copy(update={'id': 'pending'})
    paper.enqueue(later)
    paper.pause_entries_for_close_all()
    paper.pause_entries_for_close_all()
    rows = paper.order_detail('paper_pending')['events']
    assert [row['action'] for row in rows] == ['entry_pending', 'cancelled']


def test_uta_logs_confirmed_management_and_fills_after_restart(tmp_path, monkeypatch):
    async def run():
        engine, exchange = setup(tmp_path, monkeypatch)
        signal = sample()
        await engine.start(signal, limits=UtaRiskLimits(), requested_leverage=25)
        msg = {**message('breakeven'), 'source_name': '测试博主', 'text': '稳健带成本损', 'sent_at': evidence()['sent_at']}
        await engine.manage(msg, signal.id)
        payload = engine.all()[0]['payload']
        assert any('止损线更新至' in item['detail'] and item['sources'][0]['text'] == msg['text'] for item in payload['order_events'])
        count = len(payload['order_events'])
        await engine.manage(msg, signal.id)
        assert len(engine.all()[0]['payload']['order_events']) == count
        engine = UtaExecutor(engine.gateway, engine.store, lambda: None)
        trigger(exchange, 'sl')
        result = await engine.resume(signal.id)
        fills = [e for e in result['payload']['order_events'] if e['action'] == 'fill']
        stop = next(e for e in fills if '止损' in e['detail'])
        assert stop['sources'][0]['sent_at'] == msg['sent_at']
        assert result['state'] == 'closed'
        assert result['payload']['entry_signal']['stop_loss'] == '90'
        before = result['payload']['order_events']
        assert (await engine.resume(signal.id))['payload']['order_events'] == before
    asyncio.run(run())


def test_live_source_selection_persists_and_does_not_authorize(tmp_path, monkeypatch):
    engine, exchange = setup(tmp_path, monkeypatch)
    runtime = UtaRuntime(exchange, engine.store)
    runtime.set_sources([])
    restored = UtaRuntime(exchange, engine.store)
    assert restored.selected_sources() == []
    assert not restored.enabled and not restored.management_authorized
    with pytest.raises(BitgetError, match='频道未选'):
        asyncio.run(restored.ingest(sample(), 10))
    assert not exchange.calls


def test_history_endpoints_and_source_validation(tmp_path, monkeypatch):
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
        assert client.get('/api/paper/orders/missing/history').status_code == 404
        assert client.get('/api/uta/orders/missing/history').status_code == 404
        assert client.post('/api/uta/sources', json={'chat_ids': [-999]}).status_code == 400
        assert client.post('/api/uta/sources', json={'chat_ids': [-100123]}).json()['selected_sources'] == [-100123]
        service.paper.reset(D(1000), 25, D('.001'), simulation_id='legacy')
        signal = sample()
        service.paper.enqueue(signal)
        result = client.get(f'/api/paper/orders/paper_{signal.id}/history').json()
        assert not result['legacy'] and result['signal']['symbol'] == 'BTCUSDT'
        with service.paper._connect() as conn:
            conn.execute("UPDATE paper_trades SET signal_snapshot='{}', order_events='[]'")
        result = client.get(f'/api/paper/orders/paper_{signal.id}/history').json()
        assert result['legacy'] and result['events'] == []


def test_live_telegram_evidence_includes_original_sent_time(tmp_path):
    async def run():
        service = CopierService(settings(tmp_path))
        service.telegram.client = FakeTelegram()
        received = []
        async def ingest(signal): received.append(signal)
        service.telegram.on_signal = ingest
        original = event()
        await service.telegram._handle_message(original)
        assert received[0].source_messages[0]['sent_at'] == original.message.date.isoformat()
        assert received[0].source_messages[0]['text'] == original.raw_text
        await service.bitget.close()
    asyncio.run(run())
