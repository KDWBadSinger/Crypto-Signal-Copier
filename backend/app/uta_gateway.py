"""Journaled UTA exchange operations. Not an automatic-execution enable switch."""
import hashlib
import asyncio
import time
from decimal import Decimal

from .bitget import BitgetError, BitgetOrderUncertain
from .uta import number


class UtaGateway:
    def __init__(self, client, journal, authorize):
        self.client, self.journal, self.authorize = client, journal, authorize
        cfg=client.settings
        self.scope=hashlib.sha256((cfg.bitget_api_environment+'|'+(cfg.bitget_api_key or '')).encode()).hexdigest()
        self._write_lock=asyncio.Lock()
        self._last_write=0

    def guard(self):
        cfg=self.client.settings
        scope=hashlib.sha256((cfg.bitget_api_environment+'|'+(cfg.bitget_api_key or '')).encode()).hexdigest()
        if not cfg.bitget_configured or scope != self.scope or cfg.bitget_product_type != 'USDT-FUTURES':
            raise BitgetError('账户身份或交易产品发生变化，停止执行')
        self.authorize()

    async def mutate(self, key, kind, path, payload):
        async def send(value):
            async with self._write_lock:
                await asyncio.sleep(max(0,.15-(time.monotonic()-self._last_write)))
                self.guard()
                self._last_write=time.monotonic()
                return await self.client._request('POST',path,payload=value)
        return await self.journal.dispatch(self.scope,key,kind,payload,send,authorize=self.guard)

    async def enter(self, signal_id, payload):
        if payload.get('category') != 'USDT-FUTURES' or payload.get('marginMode') != 'crossed':
            raise BitgetError('UTA 开单仅允许 USDT 全仓合约')
        number(payload.get('qty'),'数量')
        number(payload.get('stopLoss'),'交易所预设止损')
        if payload.get('slOrderType') != 'market' or payload.get('slTriggerBy') != 'mark':
            raise BitgetError('必须预设标记价触发的市价止损')
        key=f'{signal_id}:entry'
        payload={**payload,'clientOid':self.journal.client_id(self.scope,key)}
        return await self.mutate(key,'entry','/api/v3/trade/place-order',payload)

    async def set_leverage(self, signal_id, symbol, leverage):
        value=number(leverage,'杠杆')
        if value != value.to_integral_value():
            raise BitgetError('杠杆必须是正整数')
        return await self.mutate(f'{signal_id}:leverage','leverage','/api/v3/account/set-leverage',
                                 {'category':'USDT-FUTURES','symbol':symbol,'leverage':str(value),'marginMode':'crossed'})

    @staticmethod
    def closing_fields(symbol, side, hold_mode):
        if side not in {'long','short'} or hold_mode not in {'one_way_mode','hedge_mode'}:
            raise BitgetError('减仓方向或持仓模式无效')
        payload={'category':'USDT-FUTURES','symbol':symbol,'side':'sell' if side == 'long' else 'buy','reduceOnly':'yes'}
        if hold_mode == 'hedge_mode':
            payload['posSide']=side
        return payload

    async def protect(self, signal_id, leg, *, symbol, side, hold_mode, qty, stop=None, target=None):
        payload=self.closing_fields(symbol,side,hold_mode)
        payload.update(type='tpsl',tpslMode='partial',qty=str(number(qty,'保护数量')))
        if stop is not None:
            payload.update(stopLoss=str(number(stop,'止损')),slTriggerBy='mark',slOrderType='market')
        if target is not None:
            payload.update(takeProfit=str(number(target,'止盈')),tpTriggerBy='mark',tpOrderType='market')
        if stop is None and target is None:
            raise BitgetError('保护单必须包含止盈或止损')
        key=f'{signal_id}:protection:{leg}'
        payload['clientOid']=self.journal.client_id(self.scope,key)
        try:
            return await self.mutate(key,'protect','/api/v3/trade/place-strategy-order',payload)
        except BitgetOrderUncertain:
            op=self.journal.get(self.scope,key)
            if not op or op['state'] not in {'unknown','inflight'}:
                raise
            result=await self.client._request('GET','/api/v3/trade/unfilled-strategy-orders',params={'category':'USDT-FUTURES','type':'tpsl'})
            rows=result.get('data')
            if not isinstance(rows,list): raise BitgetOrderUncertain('无法核对不确定的保护单')
            matches=[r for r in rows if r.get('clientOid')==payload['clientOid']]
            if len(matches)!=1 or not matches[0].get('orderId'):
                raise BitgetOrderUncertain('尚未查到唯一保护单结果，不重新提交')
            row=matches[0]
            for field in ('symbol','category'):
                if row.get(field)!=payload[field]: raise BitgetOrderUncertain('保护单查询结果身份不符')
            for field in ('qty','stopLoss','takeProfit'):
                if field in payload and number(row.get(field),field)!=number(payload[field],field):
                    raise BitgetOrderUncertain('保护单查询参数不符')
            ack={'code':'00000','data':{'orderId':str(row['orderId']),'clientOid':payload['clientOid']}}
            self.journal.update(self.scope,key,'acknowledged',result=ack,detail='超时后通过 clientOid 查询找到保护单，未重新发送')
            return ack

    def owned_protection(self, signal_id, leg):
        op=self.journal.get(self.scope,f'{signal_id}:protection:{leg}')
        if not op or op['state'] not in {'acknowledged','verified'}:
            raise BitgetError('保护单没有本程序已确认的归属记录，拒绝修改')
        return op

    async def modify_protection(self, signal_id, leg, command_id, *, qty, stop=None, target=None):
        op=self.owned_protection(signal_id,leg)
        payload={'orderId':op['result']['data']['orderId'],'qty':str(number(qty,'保护数量'))}
        if stop is not None:
            payload.update(stopLoss=str(number(stop,'止损')),slTriggerBy='mark',slOrderType='market')
        if target is not None:
            payload.update(takeProfit=str(number(target,'止盈')),tpTriggerBy='mark',tpOrderType='market')
        if stop is None and target is None:
            raise BitgetError('缺少保护单修改参数')
        try:
            return await self.mutate(f'{signal_id}:modify:{leg}:{command_id}','modify','/api/v3/trade/modify-strategy-order',payload)
        except BitgetOrderUncertain:
            await self.verify_protection(signal_id,leg,command_id)
            return self.journal.get(self.scope,f'{signal_id}:modify:{leg}:{command_id}')['result']

    async def cancel_protection(self, signal_id, leg, command_id):
        op=self.owned_protection(signal_id,leg)
        return await self.mutate(f'{signal_id}:cancel:{leg}:{command_id}','cancel','/api/v3/trade/cancel-strategy-order',
                                 {'orderId':op['result']['data']['orderId']})

    async def reduce(self, signal_id, command_id, *, symbol, side, hold_mode, qty):
        payload=self.closing_fields(symbol,side,hold_mode)
        key=f'{signal_id}:reduce:{command_id}'
        payload.update(qty=str(number(qty,'减仓数量')),orderType='market',marginMode='crossed',clientOid=self.journal.client_id(self.scope,key))
        return await self.mutate(key,'reduce','/api/v3/trade/place-order',payload)

    async def reconcile_order(self, key):
        op=self.journal.get(self.scope,key)
        if not op or op['kind'] not in {'entry','reduce'}:
            raise BitgetError('不是可核对的本程序订单')
        if op['state'] in {'prepared','rejected','verified'}:
            return op
        # A missing lookup never grants permission to submit again.
        result=await self.client._request('GET','/api/v3/trade/order-info',params={'clientOid':op['payload']['clientOid']})
        order=result.get('data') or {}
        expected=op['payload']
        if any(order.get(k) != expected.get(k) for k in ('clientOid','symbol','category','side')):
            raise BitgetOrderUncertain('交易所回执身份不匹配，不采用此结果')
        qty=number(order.get('cumExecQty'),'累计成交数量',positive=False)
        if qty < 0 or qty > number(expected['qty'],'提交数量') or not order.get('orderId'):
            raise BitgetOrderUncertain('交易所成交数量或订单 ID 无效')
        state=order.get('orderStatus')
        if state not in {'live','new','partially_filled','filled','cancelled'}:
            raise BitgetOrderUncertain('未知交易所订单状态')
        if state == 'filled' and qty != number(expected['qty'],'提交数量'):
            raise BitgetOrderUncertain('全部成交状态与数量不符')
        self.journal.update(self.scope,key,'verified' if state in {'filled','cancelled'} else 'acknowledged',result=result,
                            detail=f'交易所状态 {state}；累计成交数量 {qty}')
        return self.journal.get(self.scope,key)

    async def verify_protection(self, signal_id, leg, modification_id=None):
        op=self.owned_protection(signal_id,leg)
        result=await self.client._request('GET','/api/v3/trade/unfilled-strategy-orders',params={'category':'USDT-FUTURES','type':'tpsl'})
        rows=result.get('data')
        if not isinstance(rows,list):
            raise BitgetOrderUncertain('保护单列表结构无效')
        matches=[r for r in rows if str(r.get('orderId')) == str(op['result']['data']['orderId'])]
        if len(matches)!=1:
            raise BitgetOrderUncertain('保护单未在交易所待执行列表中确认，禁止宣称受保护')
        row=matches[0]; expected=op['payload']
        for revision in self.journal.operations(self.scope,f'{signal_id}:modify:{leg}:'):
            if revision['state']=='verified':
                expected={**expected,**revision['payload']}
        modification=None
        if modification_id is not None:
            modification=self.journal.get(self.scope,f'{signal_id}:modify:{leg}:{modification_id}')
            if not modification or modification['state'] not in {'inflight','unknown','acknowledged','verified'}:
                raise BitgetOrderUncertain('保护单修改尚未确认')
            expected={**expected,**modification['payload']}
        if row.get('symbol') != expected['symbol'] or row.get('status') != 'pending':
            raise BitgetOrderUncertain('保护单币种或状态不匹配')
        for field in ('qty','stopLoss','takeProfit'):
            if field in expected and number(row.get(field),field) != number(expected[field],field):
                raise BitgetOrderUncertain('保护单参数与预期不一致')
        if expected.get('posSide') and row.get('posSide') != expected['posSide']:
            raise BitgetOrderUncertain('保护单持仓方向不一致')
        for field in ('slTriggerBy','tpTriggerBy','slOrderType','tpOrderType'):
            if field in expected and row.get(field) != expected[field]:
                raise BitgetOrderUncertain('保护单触发类型或执行方式不一致')
        if op['state'] != 'verified':
            self.journal.update(self.scope,op['operation_key'],'verified',result=op['result'],detail='已在交易所确认待触发保护单参数')
        if modification and modification['state'] != 'verified':
            self.journal.update(self.scope,modification['operation_key'],'verified',result={'code':'00000','data':{'orderId':row['orderId']}},detail='修改后的保护参数已查询确认')
        return row

    async def pending_plans(self):
        rows=(await self.client._request('GET','/api/v3/trade/unfilled-strategy-orders',params={'category':'USDT-FUTURES','type':'tpsl'})).get('data')
        if not isinstance(rows,list): raise BitgetOrderUncertain('无法读取策略单列表')
        return rows

    async def pages(self,path,params):
        result=[]; seen=set()
        for _ in range(100):
            data=(await self.client._request('GET',path,params=params)).get('data')
            if not isinstance(data,dict) or not isinstance(data.get('list'),list):
                raise BitgetOrderUncertain('交易所分页响应无效')
            result.extend(data['list']); cursor=data.get('cursor')
            if not cursor or not data['list']: return result
            if str(cursor) in seen: raise BitgetOrderUncertain('交易所分页游标重复')
            seen.add(str(cursor)); params={**params,'cursor':str(cursor)}
            await asyncio.sleep(.12)
        raise BitgetOrderUncertain('交易所分页未完整读取')

    async def strategy_history(self,order_id):
        rows=await self.pages('/api/v3/trade/history-strategy-orders',{'category':'USDT-FUTURES','type':'tpsl','limit':'100'})
        matches=[r for r in rows if str(r.get('orderId'))==str(order_id)]
        if len(matches)!=1: raise BitgetOrderUncertain('策略单暂未进入唯一历史记录，等待同步')
        return matches[0]

    async def bind_preset_stop(self,signal_id,preview,filled_qty):
        """Bind the entry's preset SL, never create an untracked duplicate SL.

        Entry is allowed only on an empty symbol. Require a unique, exact stop
        generated in the entry's submission window. Mixed/ambiguous plans halt.
        """
        key=f'{signal_id}:protection:sl'
        old=self.journal.get(self.scope,key)
        if old: return self.owned_protection(signal_id,'sl')
        from datetime import datetime
        entry=self.journal.get(self.scope,f'{signal_id}:entry')
        start=int(datetime.fromisoformat(entry['created_at']).timestamp()*1000)-2000
        end=int(datetime.fromisoformat(entry['updated_at']).timestamp()*1000)+10000
        p=preview['payload']
        rows=[r for r in await self.pending_plans() if r.get('symbol')==p['symbol']]
        matches=[]
        for r in rows:
            if r.get('status')!='pending' or not r.get('stopLoss'): continue
            if number(r['stopLoss'],'止损')!=number(p['stopLoss'],'止损'): continue
            if number(r.get('qty'),'保护数量')!=number(filled_qty,'成交数量'): continue
            if r.get('posSide') and r['posSide']!=('long' if p['side']=='buy' else 'short'): continue
            if r.get('slTriggerBy')!='mark' or r.get('slOrderType')!='market': continue
            if not start<=int(r.get('createdTime',0))<=end: continue
            matches.append(r)
        if len(rows)!=1 or len(matches)!=1 or not matches[0].get('orderId'):
            raise BitgetOrderUncertain('无法唯一核对本次开仓生成的预设止损，不创建重复止损或接管未知策略单')
        r=matches[0]
        expected=self.closing_fields(p['symbol'],'long' if p['side']=='buy' else 'short','hedge_mode' if p.get('posSide') else 'one_way_mode')
        expected.update(qty=str(filled_qty),stopLoss=p['stopLoss'],slTriggerBy='mark',slOrderType='market')
        self.journal.prepare(self.scope,key,'protect',expected)
        self.journal.claim(self.scope,key)
        self.journal.update(self.scope,key,'verified',result={'code':'00000','data':{'orderId':str(r['orderId'])}},detail='空币种开仓窗口内唯一预设止损已核对并绑定')
        return self.owned_protection(signal_id,'sl')
