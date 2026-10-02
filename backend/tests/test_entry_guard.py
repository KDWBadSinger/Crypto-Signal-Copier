import asyncio
import copy
import time
from datetime import UTC, datetime
from decimal import Decimal as D
from types import SimpleNamespace

import httpx
import pytest

from app.bitget import BitgetError
from app.entry_guard import EntryGuard, EntryGuardSettings, estimate
from app.models import PaperSizingRequest
from app.parser import parse_signal
from app.service import CopierService
from app.source_review import SourceReview
from app.storage import SignalStore
from app.uta_risk import UtaRiskLimits
from test_desktop_workflow import settings as service_settings
from test_uta_executor import setup as executor_setup, sample as live_signal


def sample(side='long', message_id=1):
    text='#BTC 市价多 100\n止损：90\n止盈：120-130-140' if side=='long' else '#BTC 市价空 100\n止损：110\n止盈：80-70-60'
    s=parse_signal(text,source_name='测试频道',chat_id=-100123,message_id=message_id)
    s.source_messages=[{'chat_id':s.source_chat_id,'message_id':message_id,'sent_at':datetime.now(UTC).isoformat(),'text':text}]
    return s


def market_data(*, depth=100000, price='100', turnover=10000000, now=None):
    now=now or int(time.time()*1000); p=D(price)
    book={'a':[[str(p+D('.01')),str(depth)],[str(p+D('.1')),str(depth)]],
          'b':[[str(p-D('.01')),str(depth)],[str(p-D('.1')),str(depth)]],'ts':str(now)}
    minute=now//60000*60000
    candles=[[str(minute-i*60000),str(p),str(p+1),str(p-1),str(p),'10000',str(turnover)] for i in range(12)]
    return book,candles


def decision(s=None,cfg=None,data=None,**kwargs):
    params=dict(notional=D(1000),equity=D(100000),leverage=25,step=D('.001'),tick=D('.01'),
                min_qty=D('.001'),min_notional=D(1),fee=D('.0006'),now_ms=int(time.time()*1000))
    params.update(kwargs)
    return estimate(s or sample(),cfg or EntryGuardSettings(),*(data or market_data()),**params)


def test_liquid_market_preserves_leverage_and_bounds_notional():
    d=decision()
    assert d['action']=='allow' and d['effective_leverage']==25
    assert D(d['approved_notional'])<=1000
    assert D(d['quantity'])*D(d['limit_price'])<=1000
    assert D(d['limit_price'])<=D('100.01')*D('1.008')
    assert d['pre_move_percent']=='0'


@pytest.mark.parametrize('side',['long','short'])
def test_thin_book_resizes_both_sides_and_never_uses_margin_as_notional(side):
    d=decision(sample(side),data=market_data(depth=10),notional=D(5000))
    assert d['action']=='resize' and d['effective_leverage']==10
    assert D(d['approved_notional'])<=D(d['caps']['exit_depth'])<101
    assert D(d['estimated_margin'])==D(d['approved_notional'])/10
    assert D(d['stress_exit_slippage_percent'])>=0


def test_existing_symbol_exposure_consumes_capacity():
    d=decision(data=market_data(depth=10),existing_notional=D(200))
    assert d['action']=='skip' and d['approved_notional']=='0'


@pytest.mark.parametrize('side,price,expected',[('long','103','resize'),('short','97','resize'),('long','106','skip'),('short','94','skip'),('long','97','allow'),('short','103','allow')])
def test_chase_checks_are_directional(side,price,expected):
    d=decision(sample(side),data=market_data(price=price))
    assert d['action']==expected


def test_presignal_uses_only_closed_bars_before_actual_send_time():
    s=sample(); book,candles=market_data()
    # Huge current candle is not earlier evidence and cannot inflate capacity.
    candles[0][4]='900'; candles[0][6]='999999999'
    d=decision(s,data=(book,candles))
    assert D(d['pre_move_percent'])==0
    assert d['minute_turnover']=='10000000'
    s.source_messages=[]
    assert decision(s,data=(book,candles))['pre_move_percent'] is None
    s=sample(); candles[5][1]='80'
    assert decision(s,data=(book,candles))['action']=='skip'


@pytest.mark.parametrize('failure',['stale','future','crossed','missing','duplicate','nan','negative_volume'])
def test_invalid_market_never_produces_capacity(failure):
    book,candles=market_data()
    if failure=='stale': book['ts']=str(int(time.time()*1000)-4000)
    if failure=='future': book['ts']=str(int(time.time()*1000)+3000)
    if failure=='crossed': book['b'][0][0]='200'
    if failure=='missing': candles=candles[:3]
    if failure=='duplicate': book['a'].append(book['a'][0])
    if failure=='nan': book['a'][0][1]='NaN'
    if failure=='negative_volume': candles[1][6]='-1'
    with pytest.raises((BitgetError,ValueError)): decision(data=(book,candles))


def test_spike_does_not_inflate_volume_and_limits_are_validated():
    book,candles=market_data(turnover=1000)
    candles[1][6]='10000000'
    d=decision(data=(book,candles))
    assert D(d['minute_turnover'])<D(candles[1][6])
    for values in ({'risk_percent':'NaN'},{'thin_leverage_cap':2.5},{'chase_soft_percent':6,'chase_hard_percent':5},{'mode':'unsafe'}):
        with pytest.raises(ValueError): EntryGuardSettings(**values)


def test_public_requests_are_unsigned_and_failures_persist(tmp_path):
    async def run():
        seen=[]
        def transport(request):
            seen.append(request)
            book,candles=market_data()
            data=book if request.url.path.endswith('orderbook') else candles
            return httpx.Response(200,json={'code':'00000','data':data})
        async with httpx.AsyncClient(base_url='https://api.bitget.com',transport=httpx.MockTransport(transport)) as http:
            store=SignalStore(tmp_path/'guard.db'); guard=EntryGuard(SimpleNamespace(_http=http),store,'paper')
            kwargs=dict(notional=1000,equity=100000,leverage=25,step='.001',tick='.01',min_qty='.001',min_notional=1,fee='.0006')
            first=await guard.assess(sample(),**kwargs)
            assert first['action']=='allow'
            assert len(seen)==2 and all('ACCESS-KEY' not in r.headers for r in seen)
            async def unavailable(symbol): raise BitgetError('盘口已过期')
            guard.market=unavailable
            d=await guard.assess(sample(),**kwargs)
            assert d['action']=='skip' and d['data_unavailable']
            assert len(EntryGuard(None,store,'paper').recent())==1
            guard.configure(EntryGuardSettings(mode='observe'))
            assert EntryGuard(None,store,'paper').settings().mode=='observe'
            assert EntryGuard(None,store,'uta:another').settings().mode=='enforce'
    asyncio.run(run())


def paper_service(tmp_path, mode='enforce'):
    service=CopierService(service_settings(tmp_path))
    service.paper_guard.configure(EntryGuardSettings(mode=mode))
    service.paper.reset(D(100000),25,D('.0006'),['测试频道'],'entry-quality')
    service.paper.set_sizing(PaperSizingRequest(sizing_mode='fixed_usdt',fixed_usdt=200))
    async def limits(symbol): return 1,50
    async def price(symbol): return D(100)
    async def instrument(symbol): return {'sizeMultiplier':'.001','priceEndStep':'1','pricePlace':2,'minTradeNum':'.001','minTradeUSDT':'1'}
    async def market(symbol): return market_data(depth=100)
    service.bitget.symbol_leverage_limits=limits; service.bitget.contract_config=instrument
    service.public_price=price; service.paper_guard.market=market
    return service


def test_paper_end_to_end_enforces_snapshot_and_exits_without_market_data(tmp_path):
    async def run():
        svc=paper_service(tmp_path)
        try:
            s=sample(); svc.store.upsert(s)
            await svc.paper_execute(s)
            trade=svc.paper.snapshot().trades[0]
            assert trade.status=='open' and trade.leverage==10 and trade.margin<D(200)
            history=svc.paper.order_detail(trade.id)
            assert history['entry_guard']['action']=='resize'
            assert any(e['action']=='entry_quality' for e in history['events'])
            async def unavailable(symbol): raise BitgetError('不可用')
            svc.paper_guard.market=unavailable
            await svc._mark_paper(s.symbol,D(85))
            assert svc.paper.snapshot().trades[0].status=='closed'
            assert svc.entry_quality('paper')['sources'][0]['closed']==1
            svc.paper.settle({})
            assert svc.paper.report('entry-quality')['order_history'][trade.id]['entry_guard']
            svc.paper.reset(D(1000),25,D('.0006'),[],'next')
            assert svc.entry_quality('paper')['sources'][0]['closed']==1
        finally: await svc.bitget.close()
    asyncio.run(run())


def test_observe_and_off_do_not_change_orders(tmp_path):
    async def run():
        for mode in ['observe','off']:
            svc=paper_service(tmp_path/mode,mode)
            try:
                async def unavailable(symbol): raise BitgetError('不可用')
                svc.paper_guard.market=unavailable
                await svc.paper_execute(sample())
                trade=svc.paper.snapshot().trades[0]
                assert trade.status=='open' and trade.leverage==25 and trade.margin==200
                assert bool(svc.paper_guard.recent())==(mode=='observe')
            finally: await svc.bitget.close()
    asyncio.run(run())


def test_uta_ioc_is_protected_and_recovery_does_not_reassess(tmp_path,monkeypatch):
    async def run():
        engine,exchange=executor_setup(tmp_path,monkeypatch)
        guard=EntryGuard(exchange,engine.store,'uta:'+engine.gateway.scope); engine.entry_guard=guard
        calls=[]
        async def market(symbol): calls.append(symbol); return market_data()
        guard.market=market
        row=await engine.start(live_signal(),limits=UtaRiskLimits(),requested_leverage=25)
        assert row['state']=='protected'
        p=row['payload']['preview']['payload']
        assert p['orderType']=='limit' and p['timeInForce']=='ioc' and p['stopLoss']=='90'
        assert len(exchange.plans)==4 and len(calls)==2
        assert row['payload']['preview']['entry_guard']['mode']=='enforce'
        await engine.start(live_signal(),limits=UtaRiskLimits(),requested_leverage=25)
        assert len(calls)==2
    asyncio.run(run())


def test_uta_stale_or_thin_rejects_before_any_write(tmp_path,monkeypatch):
    async def run():
        engine,exchange=executor_setup(tmp_path,monkeypatch)
        guard=EntryGuard(exchange,engine.store,'uta:'+engine.gateway.scope);engine.entry_guard=guard
        async def market(symbol):
            b,c=market_data();b['ts']=str(int(time.time()*1000)-10000);return b,c
        guard.market=market
        with pytest.raises(BitgetError,match='入场质量'): await engine.start(live_signal(),limits=UtaRiskLimits(),requested_leverage=25)
        assert not any(method=='POST' for method,_,_ in exchange.calls)
    asyncio.run(run())


def test_ioc_small_partial_fill_exits_owned_quantity_once(tmp_path,monkeypatch):
    async def run():
        engine,exchange=executor_setup(tmp_path,monkeypatch)
        guard=EntryGuard(exchange,engine.store,'uta:'+engine.gateway.scope);engine.entry_guard=guard
        async def market(symbol): return market_data()
        guard.market=market
        original=exchange._request
        async def partial(method,path,**kwargs):
            payload=kwargs.get('payload',{})
            opening=path.endswith('/place-order') and payload.get('reduceOnly')!='yes'
            exchange.phase='cancelled' if opening else 'filled'
            result=await original(method,path,**kwargs)
            if opening:
                oid=result['data']['orderId']
                exchange.fill_rows[oid][0]['execQty']='.01'
            return result
        exchange._request=partial
        row=await engine.start(live_signal(),limits=UtaRiskLimits(),requested_leverage=25)
        assert row['state']=='closed' and row['payload']['guard_small_fill_exit']
        assert D(row['payload']['filled_qty'])==D('.01')
        entries=[k['payload'] for m,p,k in exchange.calls if p.endswith('/place-order') and k['payload'].get('reduceOnly')!='yes']
        closes=[k['payload'] for m,p,k in exchange.calls if p.endswith('/place-order') and k['payload'].get('reduceOnly')=='yes']
        assert len(entries)==len(closes)==1 and D(closes[0]['qty'])==D('.01')
        assert not exchange.plans
        before=len(exchange.calls)
        await engine.resume(live_signal().id)
        assert len(exchange.calls)==before
    asyncio.run(run())


def test_ioc_final_freshness_gate_prevents_network_send(tmp_path,monkeypatch):
    async def run():
        engine,exchange=executor_setup(tmp_path,monkeypatch)
        cfg={'mode':'enforce','book_ts':int(time.time()*1000)-4000}
        payload={'category':'USDT-FUTURES','symbol':'BTCUSDT','qty':'1','marginMode':'crossed',
                 'orderType':'limit','timeInForce':'ioc','price':'100.5','stopLoss':'90','slOrderType':'market','slTriggerBy':'mark'}
        with pytest.raises(BitgetError,match='过期'):
            await engine.gateway.enter('stale',payload,entry_decision=cfg)
        assert not exchange.calls
        assert engine.gateway.journal.get(engine.gateway.scope,'stale:entry')['state']=='rejected'
    asyncio.run(run())


def test_target_minimum_rejection_updates_capacity_explanation(tmp_path,monkeypatch):
    async def run():
        engine,exchange=executor_setup(tmp_path,monkeypatch)
        guard=EntryGuard(exchange,engine.store,'uta:'+engine.gateway.scope);engine.entry_guard=guard
        guard.configure(EntryGuardSettings(max_notional=2))
        async def market(symbol): return market_data()
        guard.market=market
        with pytest.raises(BitgetError,match='止盈档位'):
            await engine.start(live_signal(),limits=UtaRiskLimits(),requested_leverage=25)
        assert not any(m=='POST' for m,_,_ in exchange.calls)
        saved=guard.recent()[0]
        assert saved['action']=='skip' and saved['approved_notional']=='0'
        assert any('止盈档位' in reason for reason in saved['reasons'])
    asyncio.run(run())


def test_range_limit_never_crosses_blogger_entry_band():
    s=sample().model_copy(update={'market_entry':False,'entry_low':D('99.5'),'entry_high':D('100.05')})
    d=decision(s)
    assert D(d['limit_price'])<=D('100.05')


def test_entry_guard_api_validates_and_does_not_activate(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    svc=paper_service(tmp_path)
    monkeypatch.setattr(main,'service',svc)
    try:
        api=TestClient(main.app)
        assert api.get('/api/entry-quality/paper').json()['settings']['mode']=='enforce'
        cfg=EntryGuardSettings(mode='observe',max_notional=12345).model_dump(mode='json')
        assert api.post('/api/entry-quality/uta',json=cfg).status_code==200
        assert svc.uta_runtime.entry_guard.settings().max_notional==12345
        assert svc.paper_guard.settings().mode=='enforce'
        assert not svc.uta_runtime.enabled and not svc.uta_runtime.management_authorized
        assert api.post('/api/entry-quality/uta',json={**cfg,'risk_percent':100}).status_code==422
        assert api.get('/api/entry-quality/invalid').status_code==422
    finally: asyncio.run(svc.bitget.close())


def test_pending_paper_freezes_settings_and_survives_restart(tmp_path):
    async def run():
        svc=paper_service(tmp_path)
        s=sample().model_copy(update={'market_entry':False,'entry_low':D(99),'entry_high':D('99.9')})
        try:
            await svc.paper_execute(s)
            assert svc.paper.snapshot().trades[0].status=='pending'
            svc.paper_guard.configure(EntryGuardSettings(mode='off',max_notional=999999))
        finally: await svc.bitget.close()
        restored=CopierService(service_settings(tmp_path))
        try:
            row=restored.paper.pending_guard_entries(s.symbol)[0]
            assert row['entry_guard_mode']=='enforce'
            assert EntryGuardSettings.model_validate_json(row['entry_guard_settings']).max_notional==20000
            restored.paper.mark(s.symbol,D('99.7'))
            assert restored.paper.snapshot().trades[0].status=='pending'  # ticks cannot bypass evaluation
        finally: await restored.bitget.close()
    asyncio.run(run())


def test_concurrent_paper_cannot_delay_live_signal(tmp_path):
    async def run():
        from dataclasses import replace
        svc=paper_service(tmp_path)
        svc.settings=replace(svc.settings,bitget_api_environment='live')
        svc.uta_runtime.enabled=True
        gate=asyncio.Event();started=asyncio.Event()
        async def paper(signal,**kwargs):
            await gate.wait()
        async def live(signal,*args,**kwargs):
            started.set()
        svc.paper_execute=paper;svc.uta_runtime.ingest=live
        work=asyncio.create_task(svc.ingest_signal(sample()))
        try:
            await asyncio.wait_for(started.wait(),.5)
            assert not work.done()
        finally:
            gate.set();await work;await svc.bitget.close()
    asyncio.run(run())


def test_review_counts_edits_once_and_requires_closed_samples(tmp_path):
    store=SignalStore(tmp_path/'review.db');review=SourceReview(store);guard=EntryGuard(None,store,'paper')
    outcomes={}
    for i in range(20):
        s=sample(message_id=i+1);store.upsert(s);guard.record(s,decision(s))
        outcomes[s.id]=[{'entry_price':'100','reference':'100','side':'long','notional':'1000','net':'-10'}]
    s=sample();msg={'chat_id':s.source_chat_id,'message_id':1,'origin':'edit','text':'edited','edited_at':'2026-10-02T00:00:00Z'}
    review.observe_message(msg);review.observe_message(msg)
    report=review.summarize(guard,outcomes)[0]
    assert report['score']==40 and report['closed']==20 and report['edited_messages']==1
    outcomes.pop(s.id)
    assert review.summarize(guard,outcomes)[0]['score'] is None


def test_delete_without_exact_peer_is_ignored_and_never_trades(tmp_path):
    async def run():
        svc=paper_service(tmp_path)
        try:
            msg={'chat_id':-100123,'message_id':1,'source_name':'测试频道','origin':'live','text':'原文','received_at':datetime.now(UTC).isoformat()}
            svc.record_telegram_message(msg)
            await svc.telegram._on_deleted_message(SimpleNamespace(chat_id=None,deleted_ids=[1]))
            assert svc.store.get_message(-100123,1)['origin']=='live'
            await svc.telegram._on_deleted_message(SimpleNamespace(chat_id=-100123,deleted_ids=[1]))
            assert svc.store.get_message(-100123,1)['origin']=='delete'
            assert not svc.paper.snapshot().trades
        finally: await svc.bitget.close()
    asyncio.run(run())
