import asyncio
from decimal import Decimal as D
import pytest
from app.follow_policy import target_sizes, validate_tp_percentages
from app.paper import PaperTradingStore
from app.models import PaperSizingRequest
from app.parser import parse_signal
from app.uta_executor import UtaExecutor
from app.uta_risk import UtaRiskLimits
from test_uta_executor import setup, sample


@pytest.mark.parametrize('allocation,expected', [([60,30,10],[60,30,10]),([100,0,0],[100,0,0]),([0,100,0],[0,100,0]),([0,0,100],[0,0,100])])
def test_allocation_and_disabled_legs(allocation,expected):
    assert target_sizes(D(100),3,D(1),allocation)==expected


def test_rounding_and_validation():
    assert target_sizes(D(7),3,D(1),[60,40,0])==[4,3,0]
    for values in ([60,40,20],[20,20,20],[-1,51,50],[True,49,50],[0,0,0]):
        with pytest.raises(ValueError): validate_tp_percentages(values)
    with pytest.raises(ValueError): target_sizes(D(1),3,D(1),[60,30,10])


def test_settings_api_validation_and_restart(tmp_path,monkeypatch):
    from contextlib import asynccontextmanager
    from fastapi.testclient import TestClient
    from app.service import CopierService
    from test_desktop_workflow import settings
    from app import main
    service=CopierService(settings(tmp_path))
    @asynccontextmanager
    async def lifespan(_):
        yield
        await service.bitget.close()
    monkeypatch.setattr(main,'service',service)
    monkeypatch.setattr(main.app.router,'lifespan_context',lifespan)
    with TestClient(main.app) as client:
        assert client.get('/api/settings/take-profit-allocation').json()['percentages']==[40,40,20]
        assert client.post('/api/settings/take-profit-allocation',json={'percentages':[60,30,10]}).status_code==200
        for values in ([60,30,20],[0,0,0],[-1,51,50],[60,40], [60.5,29.5,10]):
            assert client.post('/api/settings/take-profit-allocation',json={'percentages':values}).status_code==422
        assert client.get('/api/settings/take-profit-allocation').json()['percentages']==[60,30,10]
    restored=CopierService(settings(tmp_path))
    try: assert restored.tp_percentages()==[60,30,10]
    finally: asyncio.run(restored.bitget.close())


@pytest.mark.parametrize('allocation', [[60,30,10],[100,0,0],[0,100,0],[0,0,100]])
def test_paper_fills_snapshot_and_restart(tmp_path,allocation):
    path=tmp_path/'paper.db'
    store=PaperTradingStore(path)
    store.reset(D(1000),10,D(0))
    store.set_sizing(PaperSizingRequest(sizing_mode='fixed_usdt',fixed_usdt=100,leverage=10))
    signal=sample()
    store.enqueue(signal,tp_percentages=allocation)
    store.mark('BTCUSDT',D(100))
    size=store.snapshot().trades[0].size
    store=PaperTradingStore(path)
    assert store.snapshot().trades[0].tp_percentages==allocation
    for index,price in enumerate([120,130,140]):
        store.mark('BTCUSDT',D(price))
        trade=store.snapshot().trades[0]
        assert trade.remaining_size==size*(100-sum(allocation[:index+1]))/100
    assert trade.status=='closed'


@pytest.mark.parametrize('allocation', [[60,30,10],[100,0,0],[0,100,0],[0,0,100]])
def test_uta_plan_quantities_and_restore(tmp_path,monkeypatch,allocation):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        row=await engine.start(sample(),limits=UtaRiskLimits(),requested_leverage=30,tp_percentages=allocation)
        assert row['state']=='protected'
        assert row['payload']['tp_percentages']==allocation
        quantities=[D(q) for q in row['payload']['target_quantities']]
        assert quantities==target_sizes(D(row['payload']['filled_qty']),3,D('.0001'),allocation)
        targets=[p for p in exchange.plans if 'takeProfit' in p]
        assert len(targets)==sum(v>0 for v in allocation)
        assert all(D(p['qty'])>0 for p in targets)
        restored=UtaExecutor(engine.gateway,engine.store,lambda:None)
        again=await restored.start(sample(),limits=UtaRiskLimits(),requested_leverage=30,tp_percentages=[40,40,20])
        assert again['payload']['tp_percentages']==allocation
    asyncio.run(run())


def test_delayed_reply_preserves_allocation(tmp_path,monkeypatch):
    from test_immediate_follow import intent
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        await engine.start(intent(),limits=UtaRiskLimits(),requested_leverage=10,tp_percentages=[0,60,40])
        restored=UtaExecutor(engine.gateway,engine.store,lambda:None)
        row=await restored.receive_protection(sample())
        assert row['payload']['tp_percentages']==[0,60,40]
        assert [D(v) for v in row['payload']['target_quantities']]==[D(0),D('.144'),D('.096')]
        assert len(exchange.plans)==3
        store=PaperTradingStore(tmp_path/'reply.db')
        store.reset(D(1000),10,D(0))
        store.set_sizing(PaperSizingRequest(sizing_mode='fixed_usdt',fixed_usdt=10,leverage=10))
        store.enqueue(intent(),tp_percentages=[0,60,40]); store.mark('BTCUSDT',D(100))
        store=PaperTradingStore(store.path)
        assert store.receive_protection(sample(),D(100))
        store.mark('BTCUSDT',D(120))
        assert store.snapshot().trades[0].remaining_size==1
        store.mark('BTCUSDT',D(130))
        assert store.snapshot().trades[0].remaining_size==D('.4')
    asyncio.run(run())
