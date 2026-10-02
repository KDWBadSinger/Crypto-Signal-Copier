import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.parser import parse_signal
from app.paper import PaperTradingStore
from app.models import PaperSizingRequest
from app.uta import build_order_preview
from app.uta_executor import UtaExecutor
from app.uta_risk import UtaRiskLimits
from app.bitget import BitgetOrderUncertain, BitgetError
from app.service import CopierService
from app.entry_guard import EntryGuardSettings
from test_uta_executor import setup, sample
from test_uta import INSTRUMENT
from test_desktop_workflow import settings,event,FakeTelegram


def intent():
    return parse_signal('#BTC 市價多 100',source_name='test',chat_id=-1,message_id=1,
                        market_price=D(100),allow_pending=True)


def test_pending_not_a_fake_complete_signal():
    s=intent()
    assert s.awaiting_protection and not s.take_profits and s.stop_loss==0
    p=build_order_preview(s,INSTRUMENT,margin=10,leverage=30,market_price=100,hold_mode='one_way_mode',account_scope='test')
    assert 'takeProfit' not in p['payload']
    assert D('96.6')<D(p['payload']['stopLoss'])<100
    with pytest.raises(BitgetError):
        build_order_preview(s,INSTRUMENT,margin=10,leverage=30,market_price=111,hold_mode='one_way_mode',account_scope='test')


def paper(tmp_path):
    store=PaperTradingStore(tmp_path/'paper.db')
    store.reset(D(1000),10,D('.0006'))
    store.set_sizing(PaperSizingRequest(sizing_mode='fixed_usdt',fixed_usdt=10,leverage=10))
    store.enqueue(intent()); store.mark('BTCUSDT',D(100))
    return store


def test_paper_immediate_protection_then_40_40_20(tmp_path):
    store=paper(tmp_path)
    trade=store.snapshot().trades[0]
    assert trade.status=='open' and trade.awaiting_protection
    assert trade.margin==10
    assert D(90)<trade.stop_loss<D(91)
    assert store.receive_protection(sample(),D(100))
    for price,remaining in [(120,'.6'),(130,'.2'),(140,'0')]:
        store.mark('BTCUSDT',D(price))
        assert store.snapshot().trades[0].remaining_size==D(remaining)
    assert store.snapshot().trades[0].status=='closed'


def test_paper_timeout_survives_restart_and_late_reply(tmp_path):
    store=paper(tmp_path)
    with store._connect() as c:
        c.execute('UPDATE paper_trades SET protection_deadline=?',((datetime.now(UTC)-timedelta(seconds=1)).isoformat(),))
    store=PaperTradingStore(store.path)
    assert not store.receive_protection(sample(),D(100))
    assert store.snapshot().trades[0].close_reason=='protection_timeout'
    assert not store.receive_protection(sample(),D(100))


def test_uta_immediate_reply_updates_existing_order(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        row=await engine.start(intent(),limits=UtaRiskLimits(),requested_leverage=10)
        assert len(exchange.orders)==1 and len(exchange.plans)==1
        assert row['payload']['signal']['awaiting_protection']
        deadline=row['payload']['protection_deadline']
        engine=UtaExecutor(engine.gateway,engine.store,lambda:None)
        assert (await engine.resume(intent().id))['state']=='protected'
        assert engine.all()[0]['payload']['protection_deadline']==deadline
        row=await engine.receive_protection(sample())
        assert len(exchange.orders)==1 and len(exchange.plans)==4
        assert row['payload']['target_quantities']==['0.096','0.096','0.048']
        assert not row['payload']['signal']['awaiting_protection']
        assert not await engine.receive_protection(sample())
        assert len(exchange.orders)==1
    asyncio.run(run())


def test_uta_timeout_exit_once_after_restart(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        row=await engine.start(intent(),limits=UtaRiskLimits(),requested_leverage=10)
        row['payload']['protection_deadline']=(datetime.now(UTC)-timedelta(seconds=1)).isoformat()
        engine.save(row['signal_id'],row['symbol'],row['state'],row['payload'])
        engine=UtaExecutor(engine.gateway,engine.store,lambda:None)
        with pytest.raises(BitgetOrderUncertain): await engine.resume(intent().id)
        assert len(exchange.orders)==2
        assert (await engine.resume(intent().id))['state']=='closed'
        assert not exchange.plans
        assert not await engine.receive_protection(sample())
        assert len(exchange.orders)==2
    asyncio.run(run())


def test_invalid_reply_keeps_temporary_stop_and_deadline(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        row=await engine.start(intent(),limits=UtaRiskLimits(),requested_leverage=10)
        stop=exchange.plans[0]['stopLoss']; deadline=row['payload']['protection_deadline']
        bad=sample().model_copy(update={'stop_loss':D(115)})
        with pytest.raises(ValueError): await engine.receive_protection(bad)
        assert exchange.plans[0]['stopLoss']==stop
        assert engine.all()[0]['payload']['protection_deadline']==deadline
    asyncio.run(run())


def test_nil_live_duplicate_fragment_and_replies_only_one_paper_position(tmp_path):
    async def run():
        service=CopierService(settings(tmp_path)); service.telegram.client=FakeTelegram()
        service.paper_guard.configure(EntryGuardSettings(mode='off'))  # isolate correlation/temporary protection
        service.paper.reset(D(1000),10,D('.0006'),['Test channel'])
        service.paper.set_sizing(PaperSizingRequest(sizing_mode='fixed_usdt',fixed_usdt=10,leverage=10))
        async def quote(symbol): return D('.09085')
        service.public_price=quote
        async def limits(symbol): return 1,20
        service.bitget.symbol_leverage_limits=limits
        try:
            for mid in (1,2): await service.telegram._handle_message(event('#NIL 市價多 進場0.9085',message_id=mid))
            trades=service.paper.snapshot().trades
            assert len(trades)==1 and trades[0].status=='open'
            assert service.store.get_message(-100123,2)['duplicate_of']==1
            for mid,parent in ((3,2),(4,1)):
                incoming=event('止盈：0.09474-0.10388\n止損：0.08687',message_id=mid)
                incoming.message.reply_to_msg_id=parent
                await service.telegram._handle_message(incoming)
            trades=service.paper.snapshot().trades
            assert len(trades)==1 and not trades[0].awaiting_protection
            assert trades[0].stop_loss==D('.08687')
            assert '0.9085' in service.store.latest().raw_text
        finally: await service.bitget.close()
    asyncio.run(run())


def test_no_anchor_or_excess_deviation_never_opens(tmp_path):
    async def run():
        service=CopierService(settings(tmp_path)); service.telegram.client=FakeTelegram()
        service.paper.reset(D(1000),10,D('.0006'),['Test channel'])
        async def quote(symbol): return D('.12')
        service.public_price=quote
        try:
            await service.telegram._handle_message(event('#NIL 市價多 0.9085',message_id=1))
            assert not service.paper.snapshot().trades
        finally: await service.bitget.close()
    asyncio.run(run())


def test_uta_timeout_lost_receipt_never_reduces_twice(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        row=await engine.start(intent(),limits=UtaRiskLimits(),requested_leverage=10)
        row['payload']['protection_deadline']=(datetime.now(UTC)-timedelta(seconds=1)).isoformat()
        engine.save(row['signal_id'],row['symbol'],row['state'],row['payload'])
        request=exchange._request
        async def lose(method,path,**kwargs):
            result=await request(method,path,**kwargs)
            if path.endswith('/place-order') and kwargs.get('payload',{}).get('reduceOnly')=='yes':
                raise BitgetOrderUncertain('response lost after fill')
            return result
        exchange._request=lose
        with pytest.raises(BitgetOrderUncertain): await engine.resume(intent().id)
        engine=UtaExecutor(engine.gateway,engine.store,lambda:None)
        assert (await engine.resume(intent().id))['state']=='closed'
        assert len(exchange.orders)==2
    asyncio.run(run())


def test_small_tp3_rejected_with_temp_stop_unchanged(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        row=await engine.start(intent(),limits=UtaRiskLimits(),requested_leverage=10)
        row['payload']['preview']['min_notional']='10'
        engine.save(row['signal_id'],row['symbol'],row['state'],row['payload'])
        with pytest.raises(BitgetError): await engine.receive_protection(sample())
        assert len(exchange.plans)==1
        assert engine.all()[0]['payload']['signal']['awaiting_protection']
    asyncio.run(run())


@pytest.mark.parametrize('prefix',['不要','暫不','如果'])
def test_conditional_entry_not_immediate(prefix):
    with pytest.raises(ValueError):
        parse_signal(prefix+' #BTC 市價多 100',source_name='test',market_price=D(100),allow_pending=True)


def test_partial_too_small_for_40_40_20_retains_preset_stop(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch); exchange.phase='partially_filled'
        await engine.start(sample(),limits=UtaRiskLimits(),requested_leverage=10)
        order=next(iter(exchange.orders.values()))
        order.update(orderStatus='cancelled',cumExecQty='.03')
        exchange.plans[0]['qty']='.03'; exchange.positions[0]['total']='.03'
        with pytest.raises(BitgetOrderUncertain): await engine.resume(sample().id)
        assert len(exchange.plans)==1 and exchange.plans[0]['stopLoss']=='90'
    asyncio.run(run())
