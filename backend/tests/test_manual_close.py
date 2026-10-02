import asyncio
from decimal import Decimal as D
import pytest
from app.paper import PaperTradingStore
from app.models import PaperSizingRequest
from app.uta_executor import UtaExecutor
from app.uta_risk import UtaRiskLimits
from app.bitget import BitgetError, BitgetOrderUncertain
from test_uta_executor import setup,sample


def paper(tmp_path):
    store=PaperTradingStore(tmp_path/'paper.db')
    store.reset(D(1000),10,D('.0006'))
    store.set_sizing(PaperSizingRequest(sizing_mode='fixed_usdt',fixed_usdt=10,leverage=10))
    store.enqueue(sample()); store.mark('BTCUSDT',D(100))
    return store


def test_paper_single_close_idempotent_and_keeps_simulation(tmp_path):
    store=paper(tmp_path); trade=store.snapshot().trades[0]
    result=store.close_positions({'BTCUSDT':D(110)},trade.id)
    assert result.lifecycle=='running' and result.auto_execute
    assert result.trades[0].status=='closed'
    assert result.trades[0].close_reason=='manual_close'
    assert result.trades[0].realized_pnl==D(10)
    assert result.trades[0].fees==D('.126')
    again=store.close_positions({},trade.id)
    assert again.equity==result.equity


def test_paper_global_close_cancels_pending_and_pauses(tmp_path):
    store=paper(tmp_path)
    pending=sample(2).model_copy(update={'symbol':'ETHUSDT'})
    store.enqueue(pending)
    result=store.close_positions({'BTCUSDT':D(110)})
    assert not result.auto_execute and result.lifecycle=='running'
    assert {t.status for t in result.trades}=={'closed','rejected'}
    store.mark('ETHUSDT',D(100))
    assert not store.active_symbols()


def test_uta_manual_close_and_repeat(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        row=await engine.start(sample(),limits=UtaRiskLimits(),requested_leverage=10)
        await engine.request_manual_close(row['signal_id'])
        result=engine.all()[0]
        assert result['state']=='closed' and not exchange.plans
        reductions=[k['payload'] for m,p,k in exchange.calls if p.endswith('/place-order') and k['payload'].get('reduceOnly')=='yes']
        assert len(reductions)==1
        assert D(reductions[0]['qty'])==D(row['payload']['filled_qty'])
        await engine.request_manual_close(row['signal_id'])
        assert len(exchange.orders)==2
    asyncio.run(run())


def test_uta_timeout_after_accepted_close_recovers_without_duplicate(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        await engine.start(sample(),limits=UtaRiskLimits(),requested_leverage=10)
        original=exchange._request
        async def uncertain(method,path,**kwargs):
            response=await original(method,path,**kwargs)
            if path.endswith('/place-order') and kwargs.get('payload',{}).get('reduceOnly')=='yes':
                raise BitgetOrderUncertain('reply lost after accepted')
            return response
        exchange._request=uncertain
        await engine.request_manual_close()
        assert engine.all()[0]['state']=='needs_reconciliation'
        restored=UtaExecutor(engine.gateway,engine.store,lambda:None)
        assert (await restored.resume(sample().id))['state']=='closed'
        assert len(exchange.orders)==2 and not exchange.plans
    asyncio.run(run())


def test_mixed_position_is_never_closed(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        await engine.start(sample(),limits=UtaRiskLimits(),requested_leverage=10)
        exchange.positions[0]['total']='99'
        await engine.request_manual_close()
        assert engine.all()[0]['state']=='needs_reconciliation'
        assert len(exchange.orders)==1 and exchange.plans
    asyncio.run(run())


def test_global_intents_saved_before_execution_and_continues_after_error(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        row=await engine.start(sample(),limits=UtaRiskLimits(),requested_leverage=10)
        engine.save('second','ETHUSDT','protected',dict(row['payload']))
        attempted=[]
        async def fail(sid):
            assert all(r['payload']['manual_close_requested'] for r in engine.all())
            attempted.append(sid)
            raise BitgetError('isolated error')
        engine._advance=fail
        await engine.request_manual_close()
        assert set(attempted)=={row['signal_id'],'second'}
        assert all(r['state']=='needs_reconciliation' for r in engine.all())
    asyncio.run(run())


def test_confirmed_partial_close_reduces_only_remaining_on_recovery(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        row=await engine.start(sample(),limits=UtaRiskLimits(),requested_leverage=10)
        original=exchange._request; first=True
        async def partial(method,path,**kwargs):
            nonlocal first
            result=await original(method,path,**kwargs)
            payload=kwargs.get('payload',{})
            if path.endswith('/place-order') and payload.get('reduceOnly')=='yes' and first:
                first=False
                order=exchange.orders[payload['clientOid']]
                half=D(payload['qty'])/2
                order.update(orderStatus='cancelled',cumExecQty=str(half))
                exchange.positions[0]['total']=str(half)
                exchange.fill_rows[order['orderId']][0]['execQty']=str(half)
            return result
        exchange._request=partial
        await engine.request_manual_close()
        assert engine.all()[0]['payload']['manual_close_attempt']==1
        restored=UtaExecutor(engine.gateway,engine.store,lambda:None)
        assert (await restored.resume(row['signal_id']))['state']=='closed'
        reductions=[k['payload'] for m,p,k in exchange.calls if p.endswith('/place-order') and k['payload'].get('reduceOnly')=='yes']
        assert len(reductions)==2
        assert D(reductions[1]['qty'])==D(reductions[0]['qty'])/2
    asyncio.run(run())


def test_close_api_confirmation_and_live_host_guard(tmp_path,monkeypatch):
    from contextlib import asynccontextmanager
    from fastapi.testclient import TestClient
    from app import main
    from app.service import CopierService
    from test_desktop_workflow import settings
    service=CopierService(settings(tmp_path))
    @asynccontextmanager
    async def lifespan(_):
        yield
        await service.bitget.close()
    monkeypatch.setattr(main,'service',service)
    monkeypatch.setattr(main.app.router,'lifespan_context',lifespan)
    with TestClient(main.app) as client:
        assert client.post('/api/uta/close-positions',json={'confirmation':'no'}).status_code==400
        assert client.post('/api/uta/close-positions',json={'confirmation':'确认实盘平仓'}).status_code==409
        assert client.post('/api/paper/close-positions',json={'confirmation':'no'}).status_code==400


def test_global_paper_no_quote_pauses_but_keeps_open_protection(tmp_path):
    from app.service import CopierService
    from test_desktop_workflow import settings
    async def run():
        service=CopierService(settings(tmp_path)); service.paper=paper(tmp_path)
        service.paper.enqueue(sample(2).model_copy(update={'symbol':'ETHUSDT'}))
        async def unavailable(symbol): raise BitgetError('offline')
        service.public_price=unavailable
        try:
            with pytest.raises(BitgetError): await service.close_paper_positions()
            account=service.paper.snapshot()
            assert not account.auto_execute
            assert {t.status for t in account.trades}=={'open','rejected'}
        finally: await service.bitget.close()
    asyncio.run(run())
