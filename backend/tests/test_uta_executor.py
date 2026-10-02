import asyncio
import time
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from app.bitget import BitgetError, BitgetOrderUncertain
from app.execution_journal import ExecutionJournal
from app.parser import parse_signal
from app.storage import SignalStore
from app.uta_executor import UtaExecutor
from app.uta_gateway import UtaGateway
from app.uta_risk import UtaRiskLimits
from app.uta import UtaReadiness
from test_execution_journal import Exchange


def test_confirmed_hard_limits():
    limits=UtaRiskLimits()
    assert limits.margin(50)==D(3)
    assert limits.margin(40)==D('2.4')
    assert limits.max_positions==5
    assert UtaRiskLimits(position_percent=7,max_leverage=30,max_positions=6).margin(50)==D('3.5')
    for params in ({'position_percent':7.01},{'max_leverage':0},{'max_positions':7}):
        with pytest.raises(ValidationError): UtaRiskLimits(**params)
    assert limits.leverage(100,150)==100
    assert limits.leverage(None,100)==50
    assert UtaRiskLimits(max_leverage=30).leverage(30,20)==20
    with pytest.raises(BitgetError): limits.check_count([{'total':'1'}]*5)


class WorkflowExchange(Exchange):
    def __init__(self):
        super().__init__()
        self.equities=['50','40']; self.orders={}; self.plans=[]; self.positions=[]
        self.counter=0; self.fail_plan=False; self.phase='filled'
        self.history=[]; self.children={}; self.fill_rows={}

    async def market_price(self,symbol): return D('110')

    async def _request(self,method,path,**kwargs):
        self.calls.append((method,path,kwargs)); payload=kwargs.get('payload',{})
        if path.endswith('/assets'):
            return {'code':'00000','data':{'usdtEquity':self.equities.pop(0) if len(self.equities)>1 else self.equities[0]}}
        if path.endswith('/current-position'): return {'code':'00000','data':{'list':self.positions}}
        if path.endswith('/unfilled-orders'): return {'code':'00000','data':{'list':[],'cursor':''}}
        if path.endswith('/unfilled-strategy-orders'): return {'code':'00000','data':self.plans}
        if path.endswith('/history-strategy-orders'): return {'code':'00000','data':{'list':self.history}}
        if path.endswith('/strategy-sub-orders'): return {'code':'00000','data':{'list':self.children.get(kwargs['params']['orderId'],[])}}
        if path.endswith('/fills'): return {'code':'00000','data':{'list':self.fill_rows.get(kwargs['params']['orderId'],[])}}
        if path.endswith('/set-leverage'): return {'code':'00000','data':'success'}
        if path.endswith('/order-info'):
            return {'code':'00000','data':self.orders[kwargs['params']['clientOid']]}
        self.counter+=1
        order_id=str(self.counter)
        if path.endswith('/place-order'):
            self.orders[payload['clientOid']]={**payload,'orderId':order_id,'orderStatus':self.phase,
                                               'cumExecQty':payload['qty'] if self.phase=='filled' else '0.01','avgPrice':'100'}
            self.fill_rows[order_id]=[{'execId':'fill-'+order_id,'orderId':order_id,'symbol':'BTCUSDT',
                'execQty':payload['qty'],'execPnl':'0','createdTime':str(int(time.time()*1000)),
                'feeDetail':[{'feeCoin':'USDT','fee':'.01'}]}]
            if payload.get('reduceOnly')=='yes':
                self.positions[0]['total']=str(D(self.positions[0]['total'])-D(payload['qty']))
            else:
                filled=payload['qty'] if self.phase=='filled' else '.01'
                self.positions=[{'symbol':'BTCUSDT','total':filled,'posSide':'long'}]
                self.plans.append({'orderId':'preset-'+order_id,'symbol':payload['symbol'],'qty':filled,
                    'stopLoss':payload['stopLoss'],'slTriggerBy':'mark','slOrderType':'market',
                    'posSide':'long','createdTime':str(int(time.time()*1000)),'status':'pending'})
        if path.endswith('/place-strategy-order'):
            if self.fail_plan: raise BitgetOrderUncertain('timeout')
            self.plans.append({**payload,'orderId':order_id,'status':'pending'})
        if path.endswith('/modify-strategy-order'):
            plan=next(p for p in self.plans if p['orderId']==payload['orderId']); plan.update(payload)
            order_id=payload['orderId']
        if path.endswith('/cancel-strategy-order'):
            plan=next(p for p in self.plans if p['orderId']==payload['orderId'])
            self.plans.remove(plan); self.history.append({**plan,'status':'cancelled'})
        return {'code':'00000','data':{'orderId':order_id}}


async def fake_preview(self,signal,*,margin,leverage,default_max_percent=50):
    from app.follow_policy import exchange_leverage
    leverage=exchange_leverage(100,override=leverage,percent=default_max_percent)
    qty=margin*leverage/100
    return {'payload':{'category':'USDT-FUTURES','symbol':'BTCUSDT','qty':str(qty),'side':'buy','orderType':'market','marginMode':'crossed',
                       'stopLoss':'90','slOrderType':'market','slTriggerBy':'mark'},
            'quantity_step':'.0001','min_quantity':'.0001','min_notional':'1','reference_price':'100',
            'take_profits':['120','130','140'],'effective_leverage':leverage,'price_step':'.01','taker_fee_rate':'.0006'}


def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(UtaReadiness,'preview',fake_preview)
    exchange=WorkflowExchange(); store=SignalStore(tmp_path/'exec.db')
    gateway=UtaGateway(exchange,ExecutionJournal(store),lambda:None)
    return UtaExecutor(gateway,store,lambda:None),exchange


def sample(identifier=1):
    return parse_signal('#BTC 市价多 100\n止损：90\n止盈：120-130-140',source_name='test',chat_id=-1,message_id=identifier)


def test_equity_resized_after_leverage_and_all_targets_confirmed(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        result=await engine.start(sample(),limits=UtaRiskLimits(),requested_leverage=30)
        assert result['state']=='protected'
        assert result['payload']['margin']=='2.4'
        entry=next(p for _,path,k in exchange.calls if path.endswith('/place-order') for p in [k['payload']])
        assert D(entry['qty'])==D('.72') # fresh equity 40 * 6% * explicit 30x / 100
        assert len(exchange.plans)==4
        assert sum(D(p['qty']) for p in exchange.plans if 'takeProfit' in p)==D('.72')
        calls=len(exchange.calls)
        restored=UtaExecutor(engine.gateway,engine.store,lambda:None)
        assert (await restored.start(sample(),limits=UtaRiskLimits(),requested_leverage=30))['state']=='protected'
        assert len(exchange.calls)==calls
    asyncio.run(run())


def test_protection_timeout_halts_new_trades_and_never_repeats_write(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch); exchange.fail_plan=True
        with pytest.raises(BitgetOrderUncertain): await engine.start(sample(),limits=UtaRiskLimits(),requested_leverage=10)
        assert engine.all()[0]['state']=='needs_reconciliation'
        before=sum(m=='POST' for m,_,_ in exchange.calls)
        with pytest.raises(BitgetOrderUncertain): await engine.resume(sample().id)
        assert before==sum(m=='POST' for m,_,_ in exchange.calls)
        with pytest.raises(BitgetError): await engine.start(sample(2),limits=UtaRiskLimits(),requested_leverage=10)
    asyncio.run(run())


def test_foreign_position_never_touched(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        exchange.positions=[{'symbol':'BTCUSDT','total':'1'}]
        with pytest.raises(BitgetError): await engine.start(sample(),limits=UtaRiskLimits(),requested_leverage=10)
        assert all(m=='GET' for m,_,_ in exchange.calls)
    asyncio.run(run())


def test_partial_fill_is_not_full_fill(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch); exchange.phase='partially_filled'
        result=await engine.start(sample(),limits=UtaRiskLimits(),requested_leverage=10)
        assert result['state']=='awaiting_fill'
        assert len(exchange.plans)==1 # exchange preset stop protects partial fill
        order=next(iter(exchange.orders.values()))
        order.update(orderStatus='cancelled',cumExecQty='.06')
        exchange.plans[0]['qty']='.06'; exchange.positions[0]['total']='.06'
        assert (await engine.resume(sample().id))['state']=='protected'
        assert sum(D(p['qty']) for p in exchange.plans if 'takeProfit' in p)==D('.06')
    asyncio.run(run())


def test_interrupted_management_does_not_recreate_initial_protection(tmp_path,monkeypatch):
    async def run():
        engine,exchange=setup(tmp_path,monkeypatch)
        result=await engine.start(sample(),limits=UtaRiskLimits(),requested_leverage=10)
        payload=result['payload']
        payload['pending_management']='-1:99'
        engine.save(sample().id,'BTCUSDT','needs_reconciliation',payload)
        calls=len(exchange.calls)
        restored=UtaExecutor(engine.gateway,engine.store,lambda:None)
        result=await restored.resume(sample().id)
        assert result['state']=='needs_reconciliation'
        assert result['payload']['pending_management']=='-1:99'
        assert len(exchange.calls)==calls
    asyncio.run(run())
