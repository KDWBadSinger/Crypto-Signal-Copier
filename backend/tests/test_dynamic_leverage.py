import asyncio
from decimal import Decimal as D
import pytest

from app.follow_policy import exchange_leverage
from app.uta import build_order_preview
from app.parser import parse_signal
from app.models import LeverageOverrides, PaperSizingRequest
from app.service import CopierService
from app.entry_guard import EntryGuardSettings
from test_uta import INSTRUMENT
from test_desktop_workflow import settings


@pytest.mark.parametrize('maximum,expected',[(100,50),(125,62),(20,10),(150,75)])
def test_half_maximum_without_old_cap(maximum,expected):
    assert exchange_leverage(maximum)==expected
    s=parse_signal('#BTC 市价多 100',source_name='test',allow_pending=True,market_price=D(100))
    p=build_order_preview(s,{**INSTRUMENT,'maxLeverage':str(maximum)},margin=10,leverage=None,
                          market_price=100,hold_mode='one_way_mode',account_scope='test')
    assert p['effective_leverage']==expected
    assert p['leverage_source']=='exchange_max_50_percent'


def test_override_and_bad_metadata():
    assert exchange_leverage(100,override=7)==7
    assert exchange_leverage(20,override=100)==20
    for maximum in (0,1,-1,'NaN','Infinity'):
        with pytest.raises(ValueError): exchange_leverage(maximum)


@pytest.mark.parametrize('percent,expected', [(1,1),(30,37),(50,62),(100,125)])
def test_configurable_percent(percent,expected):
    assert exchange_leverage(125,percent=percent)==expected
    s=parse_signal('#BTC 市价多 100',source_name='test',allow_pending=True,market_price=D(100))
    p=build_order_preview(s,{**INSTRUMENT,'maxLeverage':'125'},margin=100,leverage=None,
                         market_price=100,hold_mode='one_way_mode',account_scope='test',default_max_percent=percent)
    assert p['effective_leverage']==expected


def test_zero_and_invalid_percent():
    for percent in (0,-1,101,'NaN','Infinity'):
        with pytest.raises(ValueError): exchange_leverage(100,percent=percent)
    assert exchange_leverage(100,override=7,percent=0)==7
    with pytest.raises(ValueError): exchange_leverage(20,percent=1)
    for percent in (-1,101,1.5):
        with pytest.raises(ValueError): LeverageOverrides(default_max_percent=percent)


def test_percent_persisted_and_used_by_paper(tmp_path):
    async def run():
        service=CopierService(settings(tmp_path))
        service.paper_guard.configure(EntryGuardSettings(mode='off'))  # legacy sizing independently of liquidity
        service.set_leverage_overrides(LeverageOverrides(default_max_percent=30))
        await service.bitget.close()
        service=CopierService(settings(tmp_path))
        assert service.leverage_overrides().default_max_percent==30
        service.paper.reset(D(1000),10,D('.0006'))
        service.paper.set_sizing(PaperSizingRequest(sizing_mode='fixed_usdt',fixed_usdt=10,leverage=10))
        async def limits(symbol): return 1,125
        async def price(symbol): return D(100)
        service.bitget.symbol_leverage_limits=limits; service.public_price=price
        try:
            s=parse_signal('#BTC 市价多 100',source_name='test',allow_pending=True,market_price=D(100))
            await service.paper_execute(s)
            assert service.paper.snapshot().trades[0].leverage==37
            service.set_leverage_overrides(LeverageOverrides(default_max_percent=0))
            with pytest.raises(ValueError,match='0%'): await service.paper_execute(s)
            assert service.paper.snapshot().trades[0].leverage==37
        finally: await service.bitget.close()
    asyncio.run(run())


@pytest.mark.parametrize('override,expected',[(None,50),(7,7)])
def test_paper_actual_sizing_uses_dynamic_or_override(tmp_path,override,expected):
    async def run():
        service=CopierService(settings(tmp_path))
        service.paper_guard.configure(EntryGuardSettings(mode='off'))  # guard is covered by test_entry_guard
        service.paper.reset(D(1000),10,D('.0006'))
        service.paper.set_sizing(PaperSizingRequest(sizing_mode='fixed_usdt',fixed_usdt=10,leverage=10))
        async def limits(symbol): return 1,100
        async def price(symbol): return D(100)
        service.bitget.symbol_leverage_limits=limits; service.public_price=price
        if override:
            service.set_leverage_overrides(LeverageOverrides(items=[{'symbol':'BTCUSDT','leverage':override}]))
        try:
            s=parse_signal('#BTC 市价多 100',source_name='test',allow_pending=True,market_price=D(100))
            await service.paper_execute(s)
            trade=service.paper.snapshot().trades[0]
            assert trade.leverage==expected and trade.margin==10
            assert trade.size==D(10)*expected/100
        finally: await service.bitget.close()
    asyncio.run(run())
