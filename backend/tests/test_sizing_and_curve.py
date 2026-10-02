import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.account_curve import AccountCurve
from app.models import PaperSizingRequest, BitgetAccountSnapshot
from app.paper import PaperTradingStore
from app.parser import parse_signal
from app.service import CopierService
from app.storage import SignalStore
from test_desktop_workflow import settings, event, FakeTelegram


def signal(identifier=1):
    return parse_signal('#BTC 市价多 100\n止损：90\n止盈：120-130',source_name='Test channel',chat_id=-100123,message_id=identifier)


@pytest.mark.parametrize('mode,value,margin', [('fixed_usdt','100','100'),('position_percent','5','50'),('risk','5','10')])
def test_sizing_math_and_persistence(tmp_path,mode,value,margin):
    paper = PaperTradingStore(tmp_path/'p.db')
    paper.reset(D(1000),10,D('.0006'),['Test channel'],'test')
    paper.set_sizing(PaperSizingRequest(sizing_mode=mode,fixed_usdt=value,position_percent='5',leverage=10))
    paper.enqueue(signal())
    # Settings saved after receipt do not mutate a pending order.
    paper.set_sizing(PaperSizingRequest(sizing_mode='fixed_usdt',fixed_usdt=200,leverage=20))
    paper = PaperTradingStore(tmp_path/'p.db')
    paper.mark('BTCUSDT',D(100))
    trade = paper.snapshot().trades[0]
    assert trade.leverage == 10
    assert trade.margin == D(margin)
    assert trade.fees == D(margin)*10*D('.0006')
    assert paper.snapshot().leverage == 20


def test_fixed_margin_insufficient_balance_rejects(tmp_path):
    paper = PaperTradingStore(tmp_path/'p.db')
    paper.reset(D(100),10,D('.0006'),[],'test')
    paper.set_sizing(PaperSizingRequest(sizing_mode='fixed_usdt',fixed_usdt=100))
    paper.enqueue(signal()); paper.mark('BTCUSDT',D(100))
    assert paper.snapshot().trades[0].status == 'rejected'


@pytest.mark.parametrize('reply', [True, False])
def test_split_message_to_paper_fill_without_exchange_api(tmp_path,reply):
    async def run():
        service = CopierService(settings(tmp_path,bitget_api_key=None,bitget_api_secret=None,bitget_api_passphrase=None))
        service.telegram.client = FakeTelegram()
        service.set_manual_review(True)
        service.paper.reset(D(1000),10,D('.0006'),['Test channel'],'split')
        service.paper.set_sizing(PaperSizingRequest(sizing_mode='fixed_usdt',fixed_usdt=100,leverage=10))
        async def price(symbol): return D('.451')
        service.public_price = price
        async def limits(symbol): return 1,20
        service.bitget.symbol_leverage_limits=limits
        try:
            await service.telegram._handle_message(event('#UAI 市價多 0.45091',message_id=7606))
            assert service.paper.snapshot().trades[0].status=='open'
            protection = event('止盈：0.479-0.511-0.551\n止损：0.425',message_id=7608)
            if reply: protection.message.reply_to_msg_id = 7606
            await service.telegram._handle_message(protection)
            trades = service.paper.snapshot().trades
            assert len(trades) == 1
            assert bool(trades[0].take_profits) == reply
            if reply:
                assert trades[0].status == 'open'
                assert abs(trades[0].margin-D(100)) < D('.00000001')
                await service.telegram._handle_message(protection)
                assert len(service.paper.snapshot().trades) == 1
        finally: await service.bitget.close()
    asyncio.run(run())


def test_real_equity_separate_keys_modes_and_not_claimed_profit(tmp_path):
    store = SignalStore(tmp_path/'p.db'); curve = AccountCurve(store)
    cfg = settings(tmp_path,bitget_api_environment='live',bitget_api_key='test-key',bitget_api_secret='dummy',bitget_api_passphrase='dummy')
    now = datetime.now(UTC)
    snap = BitgetAccountSnapshot(environment='live',account_equity_usdt=D(1000),updated_at=now)
    curve.record(cfg,snap)
    curve.record(cfg,snap.model_copy(update={'account_equity_usdt':D(900),'updated_at':now-timedelta(minutes=1)}))
    report = curve.report(cfg)
    assert report['equity_points'][0]['equity'] == '1000'
    assert report['follow_points'] == [] and not report['follow_available']
    assert not curve.report(replace(cfg,bitget_api_key='different'))['equity_points']
    demo = replace(cfg,bitget_api_environment='demo')
    curve.record(demo,snap)
    assert not curve.report(demo)['equity_points']
    assert len(AccountCurve(store).report(cfg)['equity_points']) == 1
