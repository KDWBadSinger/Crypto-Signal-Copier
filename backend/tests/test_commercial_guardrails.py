import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.parser import parse_signal
from app.paper import PaperTradingStore
from app.service import CopierService
from test_desktop_workflow import settings


@pytest.mark.parametrize('text', [
    '#BTC #ETH LONG\nEntry: 100\nSL: 90\nTP: 120',
    '#BTC LONG\nEntry: 100\nSL: 90\nSL: 80\nTP: 120',
    '#BTC LONG\nEntry: 100\nSL: 90\nTP: 120\nRisk: 100%',
])
def test_ambiguous_or_excessive_signals_fail_closed(text):
    with pytest.raises(ValueError): parse_signal(text, source_name='test')


def test_expired_market_order_is_not_filled_on_reconnect(tmp_path):
    paper = PaperTradingStore(tmp_path/'paper.sqlite3')
    paper.reset(Decimal('1000'),10,Decimal('0'),['test'],'expiry')
    signal = parse_signal('#BTC 市价多 100\nSL: 90\nTP: 120', source_name='test')
    paper.enqueue(signal)
    with paper._connect() as conn:
        conn.execute('UPDATE paper_trades SET created_at=?',((datetime.now(UTC)-timedelta(seconds=121)).isoformat(),))
    paper.mark('BTCUSDT',Decimal('100'))
    assert paper.snapshot().trades[0].status == 'rejected'


def test_pushed_tick_exits_paper_trade_without_waiting_for_poll(tmp_path):
    async def run():
        import time
        service = CopierService(settings(tmp_path))
        try:
            service.paper.reset(Decimal('1000'),10,Decimal('0'),['test'],'stream-test')
            signal=parse_signal('#BTC 市价多 100\nSL: 90\nTP: 120',source_name='test')
            service.paper.enqueue(signal)
            service.paper.mark('BTCUSDT',Decimal('100'))
            service.market_feed.connected=True
            await service.market_feed.consume({'arg':{'instType':'USDT-FUTURES','channel':'ticker','instId':'BTCUSDT'},
                'data':[{'ts':str(int(time.time()*1000)),'lastPr':'89','markPrice':'89'}]})
            assert service.paper.snapshot().trades[0].close_reason=='stop_loss'
        finally:
            await service.bitget.close()
    asyncio.run(run())
