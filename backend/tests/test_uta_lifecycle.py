import asyncio
import time
from decimal import Decimal as D

import pytest

from app.bitget import BitgetOrderUncertain, BitgetError
from app.uta_executor import UtaExecutor
from app.uta_risk import UtaRiskLimits
from app.uta_runtime import UtaRuntime
from app.uta import UtaReadiness
from test_uta_executor import setup,sample


def trigger(exchange,leg):
    plan=next(p for p in exchange.plans if p.get('takeProfit')==leg or (leg=='sl' and p.get('stopLoss')))
    exchange.plans.remove(plan); exchange.history.append({**plan,'status':'success'})
    oid='child-'+plan['orderId']; qty=plan['qty']
    exchange.children[plan['orderId']]=[{'subOrderId':oid,'symbol':'BTCUSDT','side':'sell','status':'filled','cumExecQty':qty}]
    exchange.positions[0]['total']=str(D(exchange.positions[0]['total'])-D(qty))
    exchange.fill_rows[oid]=[{'execId':'fill-'+oid,'orderId':oid,'symbol':'BTCUSDT','execQty':qty,
        'execPnl':'1','feeDetail':[{'feeCoin':'USDT','fee':'.01'}],'createdTime':str(int(time.time()*1000))}]


def message(action,identifier=88):
    return {'chat_id':-1,'message_id':identifier,'management':{'actions':[action],'symbol':'BTCUSDT','ambiguous':False}}


def test_tp_resize_restart_full_settlement_and_orphan_cleanup(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        await engine.start(sample(),limits=UtaRiskLimits(),requested_leverage=10)
        assert len(exchange.plans)==4 # exactly one SL; no duplicate preset
        trigger(exchange,'120')
        result=await engine.resume(sample().id)
        assert result['state']=='protected'
        assert result['payload']['remaining_qty']=='0.144'
        assert next(p for p in exchange.plans if p.get('stopLoss'))['qty']=='0.144'
        engine=UtaExecutor(engine.gateway,engine.store,lambda:None)
        trigger(exchange,'sl')
        result=await engine.resume(sample().id)
        assert result['state']=='closed'
        assert not exchange.plans
        assert D(result['payload']['realized_after_fees'])==D('1.97')
    asyncio.run(run())


def test_management_tp1_and_runner_are_idempotent(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        await engine.start(sample(),limits=UtaRiskLimits(),requested_leverage=10)
        await engine.manage(message('tp1'),sample().id)
        assert D(exchange.positions[0]['total'])==D('.144')
        count=len(exchange.calls)
        await engine.manage(message('tp1'),sample().id)
        assert len(exchange.calls)==count
        await engine.resume(sample().id)
        await engine.manage(message('runner',89),sample().id)
        assert D(exchange.positions[0]['total'])==D('.0144') # 10% of remaining
        assert D(next(p for p in exchange.plans if p.get('takeProfit'))['takeProfit'])==150
        assert (await engine.resume(sample().id))['state']=='protected'
    asyncio.run(run())


def test_breakeven_includes_fees(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        await engine.start(sample(),limits=UtaRiskLimits(),requested_leverage=10)
        await engine.manage(message('breakeven'),sample().id)
        stop=D(next(p for p in exchange.plans if p.get('stopLoss'))['stopLoss'])
        assert stop>D('100.10')
        assert (await engine.resume(sample().id))['state']=='protected'
    asyncio.run(run())


def test_reduce_response_lost_recovery_queries_without_resend(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        await engine.start(sample(),limits=UtaRiskLimits(),requested_leverage=10)
        original=exchange._request; failed=False
        async def losing(method,path,**kwargs):
            nonlocal failed
            result=await original(method,path,**kwargs)
            if path.endswith('/place-order') and kwargs.get('payload',{}).get('reduceOnly')=='yes' and not failed:
                failed=True; raise BitgetOrderUncertain('response lost after filled')
            return result
        exchange._request=losing
        with pytest.raises(BitgetOrderUncertain): await engine.manage(message('runner'),sample().id)
        assert D(exchange.positions[0]['total'])==D('.024')
        count=sum(path.endswith('/place-order') for _,path,_ in exchange.calls)
        restored=UtaExecutor(engine.gateway,engine.store,lambda:None)
        assert (await restored.resume(sample().id))['state']=='protected'
        assert count==sum(path.endswith('/place-order') for _,path,_ in exchange.calls)
        assert (await restored.resume(sample().id))['state']=='protected'
    asyncio.run(run())


def test_external_position_change_halts_without_touching_it(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        await engine.start(sample(),limits=UtaRiskLimits(),requested_leverage=10)
        exchange.positions[0]['total']='.1'
        count=sum(m=='POST' for m,_,_ in exchange.calls)
        with pytest.raises(BitgetOrderUncertain): await engine.resume(sample().id)
        assert sum(m=='POST' for m,_,_ in exchange.calls)==count
        assert engine.all()[0]['state']=='needs_reconciliation'
    asyncio.run(run())


def test_runtime_activation_restart_pause_and_routing(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        async def check(self): return {'read_access':True,'checks':[{'ok':True}]}
        monkeypatch.setattr(UtaReadiness,'check',check)
        runtime=UtaRuntime(exchange,engine.store)
        with pytest.raises(BitgetError): await runtime.activate('我确认启用实盘自动跟单')
        runtime.host_authorized=True
        await runtime.activate('我确认启用实盘自动跟单')
        engine.store.upsert(sample())
        result=await runtime.ingest(sample(),10)
        assert result['state']=='protected'
        runtime.pause()
        with pytest.raises(BitgetError): await runtime.ingest(sample(2),10)
        restored=UtaRuntime(exchange,engine.store); restored.host_authorized=True
        assert not restored.enabled and restored.management_authorized
        await restored.reconcile()
        assert restored.ready
        assert restored.performance()['points']
    asyncio.run(run())


def test_telegram_split_entry_routes_real_uta_once_and_management(tmp_path,monkeypatch):
    from dataclasses import replace
    from app.service import CopierService
    from test_desktop_workflow import settings,event,FakeTelegram
    from test_uta_executor import WorkflowExchange,fake_preview
    async def run():
        monkeypatch.setattr(UtaReadiness,'preview',fake_preview)
        service=CopierService(replace(settings(tmp_path),bitget_api_environment='live'))
        original=service.bitget
        exchange=WorkflowExchange()
        runtime=UtaRuntime(exchange,service.store)
        runtime.host_authorized=True; runtime.management_authorized=True; runtime.enabled=True; runtime.ready=True
        service.uta_runtime=runtime
        service.telegram.client=FakeTelegram()
        async def quote(symbol): return D('100')
        service.public_price=quote
        try:
            await service.telegram._handle_message(event('#BTC 市价多 100',message_id=7606))
            assert len(exchange.orders)==1
            assert runtime.engine.all()[0]['payload']['signal']['awaiting_protection']
            protection=event('止盈：120-130-140\n止损：90',message_id=7608)
            protection.message.reply_to_msg_id=7606
            await service.telegram._handle_message(protection)
            assert runtime.engine.all()[0]['state']=='protected'
            assert len(exchange.orders)==1
            repeated=event('止盈：120-130-140\n止损：90',message_id=7610)
            repeated.message.reply_to_msg_id=7606
            await service.telegram._handle_message(repeated)
            assert len(exchange.orders)==1
            await service.telegram._handle_message(event('#BTC 直接手動TP1',message_id=7611))
            assert D(exchange.positions[0]['total'])==D('.72') # default 50x: initial 1.2, TP1 40%
            assert service.store.get_message(-100123,7611)['status']=='managed'
        finally: await original.close()
    asyncio.run(run())


def test_cancelled_stop_failsafe_closes_only_owned_quantity(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        await engine.start(sample(),limits=UtaRiskLimits(),requested_leverage=10)
        sl=next(p for p in exchange.plans if p.get('stopLoss'))
        exchange.plans.remove(sl); exchange.history.append({**sl,'status':'cancelled'})
        with pytest.raises(BitgetOrderUncertain): await engine.resume(sample().id)
        assert D(exchange.positions[0]['total'])==0
        assert (await engine.resume(sample().id))['state']=='closed'
        assert not exchange.plans
    asyncio.run(run())
