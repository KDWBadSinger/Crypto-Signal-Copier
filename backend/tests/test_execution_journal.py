import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.bitget import BitgetError, BitgetOrderUncertain
from app.execution_journal import ExecutionJournal
from app.storage import SignalStore
from app.uta_gateway import UtaGateway


def journal(tmp_path):
    return ExecutionJournal(SignalStore(tmp_path/'journal.db'))


def test_atomic_claim_cross_connection_and_parameter_conflict(tmp_path):
    first=journal(tmp_path); second=journal(tmp_path)
    first.prepare('account','signal','entry',{'qty':'1'})
    assert first.claim('account','signal')
    assert not second.claim('account','signal')
    with pytest.raises(BitgetError): second.prepare('account','signal','entry',{'qty':'2'})
    assert second.unresolved('account')[0]['state'] == 'inflight'


@pytest.mark.parametrize('failure',[BitgetOrderUncertain('timeout'),asyncio.CancelledError(),RuntimeError('unexpected')])
def test_interrupted_mutations_never_resend_after_restart(tmp_path,failure):
    async def run():
        calls=[]
        async def send(payload):
            calls.append(payload)
            raise failure
        with pytest.raises(type(failure)):
            await journal(tmp_path).dispatch('account','once','entry',{},send,authorize=lambda:None)
        fresh=journal(tmp_path)
        assert fresh.get('account','once')['state'] == 'unknown'
        with pytest.raises(BitgetOrderUncertain):
            await fresh.dispatch('account','once','entry',{},send,authorize=lambda:None)
        assert len(calls) == 1
    asyncio.run(run())


def test_authorization_checked_before_claim_or_network(tmp_path):
    async def run():
        called=[]
        async def send(p): called.append(p)
        def deny(): raise BitgetError('disabled')
        j=journal(tmp_path)
        with pytest.raises(BitgetError): await j.dispatch('a','k','entry',{},send,authorize=deny)
        assert not called and j.get('a','k')['state'] == 'prepared'
    asyncio.run(run())


def test_acknowledgement_is_not_fill_and_duplicates_return_original(tmp_path):
    async def run():
        calls=[]
        async def send(payload):
            calls.append(payload)
            return {'code':'00000','data':{'orderId':'123'}}
        j=journal(tmp_path)
        await j.dispatch('a','k','entry',{},send,authorize=lambda:None)
        await journal(tmp_path).dispatch('a','k','entry',{},send,authorize=lambda:None)
        assert len(calls)==1
        assert j.get('a','k')['state']=='acknowledged'
    asyncio.run(run())


class Exchange:
    def __init__(self):
        self.settings=SimpleNamespace(bitget_api_environment='demo',bitget_api_key='test',bitget_configured=True,bitget_product_type='USDT-FUTURES')
        self.calls=[]; self.order={}; self.plans=[]

    async def _request(self,method,path,**kwargs):
        self.calls.append((method,path,kwargs))
        if method == 'GET':
            return {'code':'00000','data':self.plans if path.endswith('unfilled-strategy-orders') else self.order}
        return {'code':'00000','data':{'orderId':'123'}}


def test_gateway_reconciles_exact_identity_partial_and_final(tmp_path):
    async def run():
        exchange=Exchange(); j=journal(tmp_path); gateway=UtaGateway(exchange,j,lambda:None)
        payload={'category':'USDT-FUTURES','symbol':'BTCUSDT','qty':'1','side':'buy','marginMode':'crossed','stopLoss':'90','slOrderType':'market','slTriggerBy':'mark'}
        await gateway.enter('signal',payload)
        op=j.get(gateway.scope,'signal:entry')
        exchange.order={**op['payload'],'orderId':'123','orderStatus':'partially_filled','cumExecQty':'.3'}
        assert (await gateway.reconcile_order('signal:entry'))['state']=='acknowledged'
        exchange.order.update(orderStatus='filled',cumExecQty='1')
        assert (await gateway.reconcile_order('signal:entry'))['state']=='verified'
        await gateway.enter('signal',payload)
        assert sum(method=='POST' for method,_,_ in exchange.calls)==1
    asyncio.run(run())


def test_gateway_unknown_lookup_does_not_create_retry_permission(tmp_path):
    async def run():
        exchange=Exchange(); j=journal(tmp_path); gateway=UtaGateway(exchange,j,lambda:None)
        await gateway.reduce('s','tp1',symbol='BTCUSDT',side='long',hold_mode='one_way_mode',qty=1)
        payload=exchange.calls[0][2]['payload']
        assert payload['side']=='sell' and payload['reduceOnly']=='yes' and 'posSide' not in payload
        with pytest.raises(BitgetOrderUncertain): await gateway.reconcile_order('s:reduce:tp1')
        assert j.get(gateway.scope,'s:reduce:tp1')['state']=='acknowledged'
    asyncio.run(run())


def test_protection_ownership_and_exchange_verification(tmp_path):
    async def run():
        exchange=Exchange(); j=journal(tmp_path); gateway=UtaGateway(exchange,j,lambda:None)
        with pytest.raises(BitgetError): await gateway.cancel_protection('foreign','sl','command')
        await gateway.protect('s','sl',symbol='BTCUSDT',side='short',hold_mode='hedge_mode',qty=2,stop=110)
        payload=exchange.calls[0][2]['payload']
        assert payload['side']=='buy' and payload['posSide']=='short'
        with pytest.raises(BitgetOrderUncertain): await gateway.verify_protection('s','sl')
        exchange.plans=[{**payload,'orderId':'123','status':'pending'}]
        await gateway.verify_protection('s','sl')
        assert j.get(gateway.scope,'s:protection:sl')['state']=='verified'
        await gateway.modify_protection('s','sl','breakeven',qty=2,stop=100)
        assert exchange.calls[-1][2]['payload']['stopLoss']=='100'
    asyncio.run(run())
