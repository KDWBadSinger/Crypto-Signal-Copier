"""Reconcile exchange-owned protection, remaining quantity and attributable fills."""
from datetime import UTC, datetime
from decimal import Decimal

from .bitget import BitgetError, BitgetOrderUncertain
from .uta import number
from .order_history import signal_evidence


class UtaLifecycle:
    async def reconcile_protected(self,row):
        p=row['payload']; sid=row['signal_id']; symbol=row['symbol']
        qty=await self.current_quantity(row)
        plans={str(r['orderId']):r for r in await self.gateway.pending_plans()}
        legs=self.gateway.journal.operations(self.gateway.scope,f'{sid}:protection:')
        closed=Decimal(0); orders={str(p['entry_order_id']):Decimal(p['filled_qty'])}; states={}
        evidence = {str(p['entry_order_id']): {'label': '开仓成交', 'actor': 'signal', 'sources': signal_evidence(p.get('entry_signal', p['signal']))}}
        for leg in legs:
            if leg['state'] not in {'verified','acknowledged'}:
                raise BitgetOrderUncertain('存在尚未核对的保护操作')
            oid=str(leg['result']['data']['orderId']); name=leg['operation_key'].split(':protection:',1)[1]
            if oid in plans:
                states[name]='pending'
                # Includes the latest verified amendments, not obsolete SL values.
                await self.gateway.verify_protection(sid,name)
                continue
            history=await self.gateway.strategy_history(oid)
            if history.get('symbol')!=symbol:
                raise BitgetOrderUncertain('策略历史币种不符')
            state=history.get('status'); states[name]=state
            if state not in {'success','cancelled','failed'}:
                raise BitgetOrderUncertain('策略触发结果仍待同步')
            children=await self.gateway.pages('/api/v3/trade/strategy-sub-orders',{'orderId':oid,'limit':'100'})
            for child in children:
                if child.get('symbol')!=symbol or child.get('side')!=('sell' if p['signal']['side']=='long' else 'buy') or not child.get('subOrderId'):
                    raise BitgetOrderUncertain('策略成交子单归属不符')
                if child.get('status') not in {'filled','cancelled'}:
                    raise BitgetOrderUncertain('策略子单仍在成交中')
                cid=str(child['subOrderId'])
                if cid not in orders:
                    orders[cid]=number(child.get('cumExecQty'),'策略成交数量',positive=False); closed+=orders[cid]
                    evidence[cid] = {'label': f'行情触发 {"止损" if name == "sl" else name.upper()} 成交', 'actor': 'system',
                                     'sources': p.get('protection_evidence', {}).get(name, signal_evidence(p['signal']))}
            if state=='success' and not children:
                raise BitgetOrderUncertain('已触发策略尚无成交子单，等待交易所同步')
        for op in self.gateway.journal.operations(self.gateway.scope,f'{sid}:reduce:'):
            if op['state']=='rejected': continue
            op=await self.gateway.reconcile_order(op['operation_key'])
            if op['state']!='verified': raise BitgetOrderUncertain('主动减仓结果待确认')
            order=op['result']['data']; oid=str(order['orderId'])
            if oid not in orders:
                orders[oid]=number(order['cumExecQty'],'减仓成交数量',positive=False); closed+=orders[oid]
                command = op['operation_key'].split(':reduce:', 1)[1]
                evidence[oid] = p.get('reduction_evidence', {}).get(command, {
                    'label': '用户手动平仓成交' if command.startswith('manual-close-') else {'protection-timeout': '等待保护回复超时退出成交', 'protection-failsafe': '保护失效退出成交'}.get(command, '减仓成交'),
                    'actor': 'user' if command.startswith('manual-close-') else 'system', 'sources': []})
        if closed+qty!=Decimal(p['filled_qty']):
            raise BitgetOrderUncertain('实时持仓与本程序成交明细不一致；可能有手动交易，暂停自动操作')
        p['remaining_qty']=str(qty); p['protection_states']=states
        await self.collect_performance(row,orders,evidence)
        if qty==0:
            for leg in legs:
                oid=str(leg['result']['data']['orderId'])
                if oid in plans:
                    await self._cancel_if_pending(sid,leg['operation_key'].split(':protection:',1)[1],'settlement')
            # A second position read prevents marking a newly appeared mixed
            # position as a successfully settled copier trade.
            if await self.current_quantity(row)!=0:
                raise BitgetOrderUncertain('清理保护单期间仓位变化')
            p['closed_at']=datetime.now(UTC).isoformat(); p['detail']='交易所平仓成交及残留保护单清理已核对'
            self.save(sid,symbol,'closed',p)
            return
        if states.get('sl') in {'cancelled','failed'}:
            # Definitively missing protection: close only the reconciled owned
            # quantity, not an arbitrary account position. Durable identity.
            await self.gateway.reduce(sid,'protection-failsafe',symbol=symbol,side=p['signal']['side'],hold_mode=p['hold_mode'],qty=qty)
            raise BitgetOrderUncertain('交易所止损已取消或失败，已请求只减仓市价退出，等待成交核对')
        if states.get('sl')!='pending':
            raise BitgetOrderUncertain('止损已触发但尚有仓位，等待成交同步，不重复平仓')
        sl=self.gateway.owned_protection(sid,'sl')
        actual=plans[str(sl['result']['data']['orderId'])]
        if number(actual['qty'],'止损数量')!=qty:
            revision='remaining-'+str(qty)
            await self.gateway.modify_protection(sid,'sl',revision,qty=qty,stop=p.get('current_stop',p['preview']['payload']['stopLoss']))
            await self.gateway.verify_protection(sid,'sl',revision)
        pending_tp=[n for n,s in states.items() if n!='sl' and s=='pending']
        if p['signal'].get('awaiting_protection'):
            if not p.get('manual_close_requested') and datetime.now(UTC)>=datetime.fromisoformat(p['protection_deadline']):
                await self.gateway.reduce(sid,'protection-timeout',symbol=symbol,side=p['signal']['side'],hold_mode=p['hold_mode'],qty=qty)
                raise BitgetOrderUncertain('等待保护回复已超过 5 分钟，已请求市价平仓，等待成交核对')
            p['detail']='临时止损已核对，等待博主回复；5 分钟期限跨重启保留'
            self.save(sid,symbol,'protected',p)
            return
        if not pending_tp:
            raise BitgetOrderUncertain('剩余仓位没有待触发止盈，暂停新单并提示核对')
        p['detail']='剩余仓位、交易所保护及成交明细已核对'
        self.save(sid,symbol,'protected',p)

    async def collect_performance(self,row,orders,evidence=None):
        fills={}
        for oid in sorted(orders):
            rows=await self.gateway.pages('/api/v3/trade/fills',{'category':'USDT-FUTURES','orderId':oid,'limit':'100'})
            quantity=Decimal(0); local_ids=set()
            for fill in rows:
                if str(fill.get('orderId'))!=oid or fill.get('symbol')!=row['symbol'] or not fill.get('execId'):
                    raise BitgetOrderUncertain('收益成交明细归属不符')
                if not isinstance(fill.get('feeDetail'),list): raise BitgetError('缺少成交手续费')
                if str(fill['execId']) in local_ids: continue
                local_ids.add(str(fill['execId']))
                quantity+=number(fill.get('execQty'),'成交数量')
                fee=Decimal(0)
                for item in fill['feeDetail']:
                    if item.get('feeCoin')!='USDT': raise BitgetError('非 USDT 手续费暂无法归因')
                    fee+=number(item.get('fee'),'手续费',positive=False)
                fills[str(fill['execId'])]={'id':str(fill['execId']),'at':str(fill['createdTime']),
                    'quantity': str(fill['execQty']), 'price': fill.get('execPrice'),
                    'evidence': (evidence or {}).get(oid, {}),
                    'pnl':str(number(fill.get('execPnl'),'已实现收益',positive=False)), 'fee':str(fee)}
            if quantity!=orders[oid]: raise BitgetOrderUncertain('收益成交数量未完整同步，不结算或推算收益')
        p=row['payload']; p['fills']=list(fills.values())
        p['realized_after_fees']=str(sum((Decimal(f['pnl'])-Decimal(f['fee']) for f in fills.values()),Decimal(0)))
        p['performance_note']='仅本程序已核对成交的已实现盈亏减交易手续费；不含未实现盈亏及资金费，不含充值提现。'
