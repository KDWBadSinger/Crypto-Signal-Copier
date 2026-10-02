import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.paper import PaperTradingStore, PaperTradingError
from app.parser import parse_signal
from app.service import CopierService
from app.bitget import BitgetError
from test_paper import _signal
from test_desktop_workflow import settings


def new_store(tmp_path, fee='0.0006'):
    store = PaperTradingStore(tmp_path / 'paper.sqlite3')
    store.reset(Decimal('1000'), 10, Decimal(fee), ['test'], '我的收益测试')
    return store


def test_restart_preserves_id_trades_and_auto_follow(tmp_path):
    store = new_store(tmp_path)
    store.enqueue(_signal())
    store.mark('BTCUSDT', Decimal('105'))
    store.runtime_start()
    store._runtime_tick -= timedelta(seconds=12)
    store.runtime_stop()
    reloaded = PaperTradingStore(store.path)
    reloaded.runtime_start()
    assert reloaded.snapshot().simulation_id == '我的收益测试'
    assert reloaded.should_auto_execute('test')
    assert reloaded.snapshot().trades[0].status == 'open'
    assert 12 <= reloaded.snapshot().active_seconds < 13
    assert not reloaded.enqueue(_signal())


def test_settlement_closes_charges_fees_freezes_and_archives(tmp_path):
    store = new_store(tmp_path)
    store.enqueue(_signal())
    store.mark('BTCUSDT', Decimal('105'))
    trade = store.snapshot().trades[0]
    account = store.settle({'BTCUSDT': Decimal('110')})
    assert account.lifecycle == 'stopped'
    assert account.unrealized_pnl == 0
    assert account.used_margin == 0
    assert account.fees_paid == (Decimal('105') + Decimal('110')) * trade.size * Decimal('0.0006')
    assert account.equity == account.initial_balance + account.realized_pnl - account.fees_paid
    assert not store.should_auto_execute('test')
    frozen = store.report('我的收益测试')
    store.mark('BTCUSDT', Decimal('200'))
    store.settle({})
    assert store.report('我的收益测试') == frozen
    with pytest.raises(PaperTradingError): store.enqueue(_signal())
    with pytest.raises(PaperTradingError): store.set_auto_execute(True)
    with pytest.raises(PaperTradingError): store.reset(Decimal('1000'), 10, Decimal('0'), [], '我的收益测试')
    store.reset(Decimal('500'), 5, Decimal('0'), ['test'], '第二次测试')
    assert not store.snapshot().trades
    assert store.report('我的收益测试') == frozen
    assert len(store.reports()) == 1


def test_cannot_reset_running_or_settle_with_missing_prices(tmp_path):
    store = new_store(tmp_path)
    store.enqueue(_signal())
    store.mark('BTCUSDT', Decimal('105'))
    with pytest.raises(PaperTradingError): store.reset(Decimal('20'), 1, Decimal('0'))
    with pytest.raises(PaperTradingError): store.settle({})
    assert store.snapshot().lifecycle == 'running'
    assert store.snapshot().trades[0].status == 'open'
    assert not store.reports()


def test_stop_cancels_unfilled_orders_without_price(tmp_path):
    store = new_store(tmp_path)
    store.enqueue(_signal())
    result = store.settle({})
    assert result.trades[0].status == 'rejected'
    assert result.equity == Decimal('1000')


def test_market_entry_fills_without_exact_price_match_and_rejects_chasing(tmp_path):
    store = new_store(tmp_path)
    signal = parse_signal('#BTC 市价多 100\n止损：90\n止盈：120-130', source_name='test')
    store.enqueue(signal)
    store.mark('BTCUSDT', Decimal('101'))
    assert store.snapshot().trades[0].entry_price == Decimal('101')
    signal.id = 'another'
    store.enqueue(signal)
    store.mark('BTCUSDT', Decimal('104'))
    assert next(t for t in store.snapshot().trades if t.signal_id == 'another').status == 'rejected'


def test_daily_points_persist_and_offline_gaps_are_not_fabricated(tmp_path, monkeypatch):
    import app.paper as module
    class Clock(datetime):
        value = datetime(2026, 9, 1, 12, tzinfo=UTC)
        @classmethod
        def now(cls, tz=None): return cls.value
    monkeypatch.setattr(module, 'datetime', Clock)
    store = new_store(tmp_path)
    store.runtime_start()
    store.enqueue(_signal())
    store.mark('BTCUSDT', Decimal('105'))
    Clock.value += timedelta(seconds=5)
    store.heartbeat(market_ok=True)
    store.runtime_stop()
    Clock.value = datetime(2026, 9, 3, 12, tzinfo=UTC)
    store.runtime_start()
    store.mark('BTCUSDT', Decimal('110'))
    store.heartbeat(market_ok=True)
    points = store.report()['daily']
    assert len(points) == 3
    assert points[1]['equity'] is None
    assert points[2]['daily_profit'] is None
    assert store.snapshot().active_seconds == 5
    assert PaperTradingStore(store.path).report()['daily'] == points


def test_background_prices_and_stop_use_no_private_exchange_calls(tmp_path):
    async def run():
        service = CopierService(settings(tmp_path))
        service.paper.reset(Decimal('1000'), 10, Decimal('0'), ['test'], 'no-key')
        calls = []
        async def public_price(symbol):
            calls.append(symbol)
            return Decimal('105')
        async def forbidden(*args, **kwargs): pytest.fail('Private exchange operation called')
        service.bitget.market_price = public_price
        service.bitget.place_order = forbidden
        service.bitget.set_cross_leverage = forbidden
        service.paper.enqueue(_signal())
        task = asyncio.create_task(service._paper_market_loop())
        try:
            await asyncio.sleep(0.03)
            assert service.paper.snapshot().trades[0].status == 'open'
            await service.stop_paper()
            assert service.paper.snapshot().lifecycle == 'stopped'
            assert calls == ['BTCUSDT', 'BTCUSDT']
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await service.bitget.close()
    asyncio.run(run())
