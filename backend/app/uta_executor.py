"""Recoverable UTA entry -> fill -> exchange protection workflow.

The application must explicitly authorize this workflow before calling start.
No order is sent from construction, importing, or status inspection.
"""
import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_DOWN

from .bitget import BitgetError, BitgetOrderUncertain
from .models import ParsedSignal
from .uta import UtaReadiness, number
from .uta_risk import UtaRiskLimits
from .uta_lifecycle import UtaLifecycle
from .follow_policy import target_sizes, temporary_stop, PROTECTION_WAIT_SECONDS
from .order_history import event, signal_evidence, message_evidence


class UtaExecutor(UtaLifecycle):
    def __init__(self, gateway, store, authorize_new, entry_guard=None):
        self.gateway, self.store, self.authorize_new = gateway, store, authorize_new
        self.entry_guard = entry_guard
        self.lock=asyncio.Lock()
        with store._connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS uta_workflows (
                scope TEXT NOT NULL, signal_id TEXT NOT NULL, symbol TEXT NOT NULL,
                state TEXT NOT NULL, payload TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(scope,signal_id))''')

    def all(self):
        with self.store._connect() as conn:
            rows=conn.execute('SELECT * FROM uta_workflows WHERE scope=? ORDER BY updated_at DESC',(self.gateway.scope,)).fetchall()
        return [{**dict(r),'payload':json.loads(r['payload'])} for r in rows]

    def save(self, signal_id, symbol, state, payload):
        with self.store._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            prior = conn.execute('SELECT state,payload FROM uta_workflows WHERE scope=? AND signal_id=?', (self.gateway.scope, signal_id)).fetchone()
            old = json.loads(prior['payload']) if prior else {}
            events = old.get('order_events', [])
            known_fills = {f['id'] for f in old.get('fills', [])}
            for fill in payload.get('fills', []):
                if fill['id'] not in known_fills:
                    evidence = fill.get('evidence', {})
                    events.append(event('fill', f'{evidence.get("label", "交易所成交已核对")}：数量 {fill.get("quantity", "未记录")}，成交价 {fill.get("price") or "未记录"}；已实现盈亏 {fill["pnl"]}，手续费 {fill["fee"]} USDT',
                                        sources=evidence.get('sources', []), actor=evidence.get('actor', 'system'),
                                        at=datetime.fromtimestamp(int(fill['at'])/1000, UTC).isoformat(), values=fill))
            keys = ('current_stop', 'remaining_qty', 'target_quantities', 'manual_close_requested', 'filled_qty')
            changed = not prior or prior['state'] != state or any(old.get(k) != payload.get(k) for k in keys)
            if changed:
                context = payload.get('audit_context', {})
                labels = {'prepared': '准备开仓', 'submitting': '提交开仓', 'awaiting_fill': '等待成交核对',
                          'protecting': '核对止盈止损', 'protected': '持仓与保护已核对', 'managing': '执行持仓管理',
                          'needs_reconciliation': '等待交易所核对', 'closed': '订单已结束', 'rejected': '开仓已拒绝'}
                changes = []
                if old.get('current_stop') != payload.get('current_stop') and payload.get('current_stop'):
                    changes.append('止损线更新至 '+payload['current_stop'])
                if old.get('remaining_qty') != payload.get('remaining_qty') and payload.get('remaining_qty') is not None:
                    changes.append('已核对剩余数量 '+payload['remaining_qty'])
                targets = payload.get('preview', {}).get('take_profits', [])
                if old.get('target_quantities') != payload.get('target_quantities') and targets:
                    changes.append('止盈 '+ ' / '.join(targets))
                detail = '；'.join([labels.get(state, state), *changes, payload.get('detail', '')])
                actor = context.get('actor', 'system' if prior else 'signal')
                sources = context.get('sources', payload.get('protection_sources', signal_evidence(payload.get('signal', {}))))
                events.append(event(state, detail, sources=sources, actor=actor,
                                    values={k: payload.get(k) for k in keys}))
            payload['order_events'] = events
            if not prior:
                payload['entry_signal'] = json.loads(json.dumps(payload.get('signal', {})))
            elif 'entry_signal' in old:
                payload['entry_signal'] = old['entry_signal']
            if state in {'protected', 'closed', 'rejected'} and not payload.get('pending_management') and not payload.get('protection_update'):
                payload.pop('audit_context', None)
            conn.execute('INSERT INTO uta_workflows VALUES (?,?,?,?,?,?) ON CONFLICT(scope,signal_id) DO UPDATE SET state=excluded.state,payload=excluded.payload,updated_at=excluded.updated_at',
                         (self.gateway.scope,signal_id,symbol,state,json.dumps(payload),datetime.now(UTC).isoformat()))

    async def _require_empty_symbol(self,symbol,limits=None,reserved=0):
        client=self.gateway.client
        data=(await client._request('GET','/api/v3/position/current-position',params={'category':'USDT-FUTURES'})).get('data') or {}
        if not isinstance(data,dict) or not isinstance(data.get('list'),list):
            raise BitgetError('无法完整读取 UTA 持仓，拒绝开单')
        if limits:
            limits.check_count(data['list'],reserved)
        for row in data['list']:
            if row.get('symbol') == symbol and number(row.get('total'),'持仓数量',positive=False) != 0:
                raise BitgetError('该币种已有持仓，不接管或混合其他交易')
        cursor=None; seen=set()
        for _ in range(20):
            params={'category':'USDT-FUTURES','symbol':symbol,'limit':'100'}
            if cursor: params['cursor']=cursor
            result=(await client._request('GET','/api/v3/trade/unfilled-orders',params=params)).get('data') or {}
            if not isinstance(result.get('list'),list):
                raise BitgetError('无法完整读取未成交订单')
            if any(r.get('symbol') == symbol for r in result['list']):
                raise BitgetError('该币种已有挂单，拒绝混合下单')
            cursor=result.get('cursor')
            if not result['list'] or not cursor: break
            if cursor in seen: raise BitgetError('未成交订单分页异常')
            seen.add(cursor)
        else: raise BitgetError('未完成订单分页核对，拒绝开单')
        plans=(await client._request('GET','/api/v3/trade/unfilled-strategy-orders',params={'category':'USDT-FUTURES'})).get('data')
        if not isinstance(plans,list) or any(r.get('symbol') == symbol for r in plans):
            raise BitgetError('该币种存在策略单或无法读取策略单，不接管未知订单')

    async def start(self,signal,*,limits: UtaRiskLimits,requested_leverage,default_max_percent=50,tp_percentages=None):
        from .follow_policy import validate_tp_percentages
        allocation = validate_tp_percentages(tp_percentages if tp_percentages is not None else [40,40,20])
        async with self.lock:
            self.authorize_new()
            now=datetime.now(UTC)
            if not 0 <= (now-signal.received_at).total_seconds() <= 120:
                raise BitgetError('信号已过期，不追补实盘开单')
            existing=self.all()
            old=next((r for r in existing if r['signal_id']==signal.id),None)
            if old:
                return old
            active=[r for r in existing if r['state'] not in {'rejected','closed'}]
            if any(r['state']=='needs_reconciliation' for r in active):
                raise BitgetError('存在结果未确认的交易任务，暂停新开仓')
            if len(active) >= limits.max_positions or any(r['symbol']==signal.symbol for r in active):
                raise BitgetError('达到最大跟单数量或同币种已有执行任务')
            pending=sum(r['state'] in {'prepared','submitting','awaiting_fill'} for r in active)
            await self._require_empty_symbol(signal.symbol,limits,pending)
            equity=await self._equity()
            margin=limits.margin(equity)
            # None means a fresh instrument maximum / 2, not the obsolete global cap.
            leverage=requested_leverage
            guard_settings=self.entry_guard.settings() if self.entry_guard else None
            bounded=bool(guard_settings and guard_settings.mode=='enforce')
            preview=await UtaReadiness(self.gateway.client,bounded_entry=bounded).preview(signal,margin=margin,leverage=leverage,default_max_percent=default_max_percent)
            if self.entry_guard:
                preview=await self.entry_guard.apply_preview(signal,preview,equity,settings=guard_settings)
                margin=Decimal(preview.get('estimated_margin',margin))
            first_decision=preview.get('entry_guard')
            # Validate each target's minimum lot before opening, not after it fills.
            step=Decimal(preview['quantity_step']); total=Decimal(preview['payload']['qty'])
            try:
                sizes=target_sizes(total,len(signal.take_profits),step,allocation) if signal.take_profits else []
                if any(q < Decimal(preview['min_quantity']) or q*t < Decimal(preview['min_notional']) for q,t in zip(sizes,signal.take_profits) if q>0):
                    raise BitgetError('投入金额不足以按全部止盈档位分仓，拒绝开单而不是省略档位')
            except (BitgetError, ValueError) as exc:
                if self.entry_guard:
                    self.entry_guard.reject(signal, first_decision, str(exc))
                raise
            # No preset TP: all targets will have independently owned plan IDs.
            for field in ('takeProfit','tpTriggerBy','tpOrderType'):
                preview['payload'].pop(field,None)
            payload={'signal':signal.model_dump(mode='json'),'preview':preview,'margin':str(margin),
                     'limits':limits.model_dump(mode='json'),'equity_at_sizing':str(equity),
                     'hold_mode':'hedge_mode' if preview['payload'].get('posSide') else 'one_way_mode',
                     'detail':'已持久化执行计划，尚未下单'}
            payload['tp_percentages']=allocation
            self.save(signal.id,signal.symbol,'prepared',payload)
            try:
                self.authorize_new()
                await self.gateway.set_leverage(signal.id,signal.symbol,preview['effective_leverage'])
                self.authorize_new()
                # Equity is re-read after leverage configuration, immediately before
                # building the final order. Loss never increases the margin budget.
                equity=await self._equity()
                margin=min(margin,limits.margin(equity))
                configured_leverage=preview['effective_leverage']
                ceiling=Decimal(preview['payload']['qty'])*Decimal(preview['reference_price'])
                preview=await UtaReadiness(self.gateway.client,bounded_entry=bounded).preview(signal,margin=margin,leverage=configured_leverage,default_max_percent=default_max_percent)
                if self.entry_guard:
                    preview=await self.entry_guard.apply_preview(signal,preview,equity,notional_ceiling=ceiling,settings=guard_settings)
                    margin=Decimal(preview.get('estimated_margin',margin))
                    final_decision=preview.get('entry_guard')
                    if first_decision and final_decision:
                        final_decision['requested_notional']=first_decision['requested_notional']
                        final_decision['requested_leverage']=first_decision['requested_leverage']
                        final_decision['reasons']=list(dict.fromkeys(first_decision['reasons']+final_decision['reasons']))
                        if final_decision['action']=='allow' and (Decimal(final_decision['approved_notional'])<Decimal(first_decision['requested_notional'])*Decimal('.99') or final_decision['effective_leverage']!=first_decision['requested_leverage']):
                            final_decision['action']='resize'
                        self.entry_guard.record(signal,final_decision)
                if preview['effective_leverage'] != configured_leverage:
                    raise BitgetError('交易所杠杆限制在设置后发生变化，停止本次开单，不使用不一致杠杆')
                step=Decimal(preview['quantity_step']); total=Decimal(preview['payload']['qty'])
                sizes=target_sizes(total,len(signal.take_profits),step,allocation) if signal.take_profits else []
                if any(q < Decimal(preview['min_quantity']) or q*t < Decimal(preview['min_notional']) for q,t in zip(sizes,signal.take_profits) if q>0):
                    raise BitgetError('实时净值变化后不足以分档保护，拒绝扩大订单')
                for field in ('takeProfit','tpTriggerBy','tpOrderType'):
                    preview['payload'].pop(field,None)
                payload.update(preview=preview,margin=str(margin),equity_at_sizing=str(equity))
                await self._require_empty_symbol(signal.symbol,limits,pending)
                if (datetime.now(UTC)-signal.received_at).total_seconds()>120:
                    raise BitgetError('调整杠杆期间信号已过期，不提交开单')
                self.save(signal.id,signal.symbol,'submitting',payload)
                if signal.awaiting_protection:
                    payload['protection_deadline']=(datetime.now(UTC)+timedelta(seconds=PROTECTION_WAIT_SECONDS)).isoformat()
                    self.save(signal.id,signal.symbol,'submitting',payload)
                if self.entry_guard:
                    self.entry_guard.require_fresh(preview.get('entry_guard'))
                await self.gateway.enter(signal.id,preview['payload'],entry_decision=preview.get('entry_guard'))
                self.save(signal.id,signal.symbol,'awaiting_fill',payload)
            except BaseException as exc:
                # An exception does not prove the account has no exposure.
                op=self.gateway.journal.get(self.gateway.scope,signal.id+':entry')
                state='rejected' if op is None or op['state']=='rejected' else 'needs_reconciliation'
                payload['detail']=str(exc) if isinstance(exc,BitgetError) else '请求中断，等待核对'
                if state=='rejected' and self.entry_guard:
                    self.entry_guard.reject(signal, preview.get('entry_guard'), payload['detail'])
                    payload['preview']=preview
                self.save(signal.id,signal.symbol,state,payload)
                raise
            return await self._advance(signal.id)

    async def _equity(self):
        result=await self.gateway.client._request('GET','/api/v3/account/assets')
        data=result.get('data')
        if not isinstance(data,dict):
            raise BitgetError('无法读取 UTA 实时净值')
        return number(data.get('usdtEquity'),'实时 USDT 净值')

    async def current_quantity(self,row):
        result=await self.gateway.client._request('GET','/api/v3/position/current-position',params={'category':'USDT-FUTURES'})
        data=result.get('data') or {}
        if not isinstance(data.get('list'),list): raise BitgetError('无法核对实时持仓')
        signal=row['payload']['signal']
        rows=[r for r in data['list'] if r.get('symbol')==row['symbol'] and number(r.get('total'),'持仓数量',positive=False)!=0]
        if not rows: return Decimal(0)
        if len(rows)!=1 or rows[0].get('posSide') != signal['side']:
            raise BitgetOrderUncertain('持仓方向或数量记录不唯一，暂停管理')
        qty=number(rows[0]['total'],'实时持仓数量')
        if qty > Decimal(row['payload'].get('filled_qty','0')):
            raise BitgetOrderUncertain('检测到超出本程序开仓数量的仓位，不操作混合持仓')
        return qty

    async def manage(self,message,signal_id):
        async with self.lock:
            rows=[r for r in self.all() if r['signal_id']==signal_id and r['state']=='protected']
            if len(rows)!=1: raise BitgetError('不存在唯一的已确认受保护跟单持仓')
            row=rows[0]; payload=row['payload']; signal=payload['signal']
            if signal.get('awaiting_protection'):
                raise BitgetError('正在等待完整保护回复，不猜测 TP1 或尾仓档位；临时止损及超时退出继续运行')
            if signal['source_chat_id']!=message['chat_id']:
                raise BitgetError('频道与持仓归属不一致')
            command=message['management']; actions=command['actions']
            if command.get('ambiguous') or (command.get('symbol') and command['symbol']!=row['symbol']):
                raise BitgetError('管理指令币种或语义不唯一')
            identity=f"{message['chat_id']}:{message['message_id']}"
            completed=payload.setdefault('management_messages',[])
            if identity in completed: return '已处理过该管理消息，不重复执行'
            await self.reconcile_protected(row)
            flags=set(payload.get('management_flags',[]))
            qty=await self.current_quantity(row)
            if qty<=0: raise BitgetError('交易所已无此持仓，不发送管理订单')
            price=await self.gateway.client.market_price(row['symbol'])
            entry=Decimal(payload['entry_price']); long=signal['side']=='long'
            step=Decimal(payload['preview']['quantity_step'])
            tick=Decimal(payload['preview'].get('price_step','0.00000001'))
            stop=Decimal(payload.get('current_stop',payload['preview']['payload']['stopLoss']))
            if (long and price<=stop) or (not long and price>=stop):
                raise BitgetError('当前价格已越过止损，先核对交易所止损执行结果')
            new_stop=None; runner_target=None; keep=None
            if 'breakeven' in actions:
                # Confirm actual opening fees from the exchange, do not use paper P&L.
                fees=await self._opening_fees(payload['entry_order_id'],Decimal(payload['filled_qty']))
                opening_per_unit=fees/Decimal(payload['filled_qty'])
                rate=Decimal(payload['preview']['taker_fee_rate'])
                cost=(entry+opening_per_unit)/(1-rate) if long else (entry-opening_per_unit)/(1+rate)
                from decimal import ROUND_UP
                cost=(cost/tick).to_integral_value(rounding=ROUND_UP if long else ROUND_DOWN)*tick
                new_stop=max(stop,cost) if long else min(stop,cost)
                if (long and price<=new_stop) or (not long and price>=new_stop):
                    raise BitgetError('当前价格不满足含手续费成本损条件')
            if 'runner' in actions and 'runner' not in flags:
                keep=(qty*Decimal('.1')/step).to_integral_value(rounding=ROUND_DOWN)*step
                lev=Decimal(payload['preview']['effective_leverage'])
                runner_target=entry*(1+5/lev) if long else entry*(1-5/lev)
                runner_target=(runner_target/tick).to_integral_value(rounding=ROUND_DOWN)*tick
                if keep < Decimal(payload['preview']['min_quantity']) or runner_target<=0:
                    raise BitgetError('尾仓数量或 500% 保证金收益目标不可执行，不先减仓')
                if (long and price>=runner_target) or (not long and price<=runner_target):
                    raise BitgetError('当前价已经越过尾仓收益目标，不执行此格局指令')
            original=Decimal(payload['filled_qty'])
            first=Decimal(payload['target_quantities'][0])
            if qty<=original-first:
                flags.add('tp1')
            # Persist the phase before any management write. Recovery must never
            # mistake an interrupted reduction for an unfinished initial entry.
            payload['pending_management']=identity
            payload['audit_context'] = {'actor': 'signal', 'sources': [message_evidence(message)]}
            payload.setdefault('reduction_evidence', {})[identity] = {'actor': 'signal', 'sources': [message_evidence(message)], 'label': '博主管理指令减仓成交'}
            close_qty=None; targets=[]
            if runner_target is not None:
                targets=[f'tp{i+1}' for i,q in enumerate(payload['target_quantities']) if Decimal(q)>0]
                close_qty=qty-keep
            elif 'tp1' in actions and 'tp1' not in flags:
                targets=['tp1']; close_qty=min(first,qty)
            payload['management_plan']={'id':identity,'qty':str(qty),
                'close_qty':str(close_qty) if close_qty is not None else None,
                'new_stop':str(new_stop) if new_stop is not None else None,
                'runner_target':str(runner_target) if runner_target is not None else None,
                'targets':targets,'flags':sorted(flags)}
            self.save(signal_id,row['symbol'],'managing',payload)
            return await self._resume_management(row)

    async def _resume_management(self,row):
        payload=row['payload']; signal=payload['signal']; signal_id=row['signal_id']
        plan=payload['management_plan']; identity=plan['id']; qty=Decimal(plan['qty'])
        flags=set(plan['flags']); stop=payload.get('current_stop',payload['preview']['payload']['stopLoss'])
        try:
            if plan['new_stop'] is not None and not plan.get('stop_done'):
                await self.gateway.modify_protection(signal_id,'sl',identity,qty=qty,stop=plan['new_stop'])
                await self.gateway.verify_protection(signal_id,'sl',identity)
                payload['current_stop']=plan['new_stop']; stop=plan['new_stop']; plan['stop_done']=True
                payload.setdefault('protection_evidence', {})['sl'] = payload.get('audit_context', {}).get('sources', [])
                self.save(signal_id,row['symbol'],'managing',payload)
            for leg in plan['targets']:
                await self._cancel_if_pending(signal_id,leg,identity)
            if plan['close_qty'] is not None:
                close_qty=Decimal(plan['close_qty']); key=f'{signal_id}:reduce:{identity}'
                op=self.gateway.journal.get(self.gateway.scope,key)
                if not op or op['state']=='prepared':
                    if await self.current_quantity(row)!=qty:
                        raise BitgetOrderUncertain('撤销止盈期间持仓变化，不重复减仓')
                    await self.gateway.reduce(signal_id,identity,symbol=row['symbol'],side=signal['side'],hold_mode=payload['hold_mode'],qty=close_qty)
                result=await self.gateway.reconcile_order(key)
                if result['state']!='verified' or Decimal(result['result']['data']['cumExecQty'])!=close_qty:
                    raise BitgetOrderUncertain('减仓尚未完整成交，不重复提交')
                remaining=qty-close_qty
                if await self.current_quantity(row)!=remaining:
                    raise BitgetOrderUncertain('减仓后持仓尚未同步或出现额外成交，等待核对')
                if remaining>0:
                    revision=identity+':resize'
                    await self.gateway.modify_protection(signal_id,'sl',revision,qty=remaining,stop=stop)
                    await self.gateway.verify_protection(signal_id,'sl',revision)
                if plan['runner_target'] is not None:
                    await self.gateway.protect(signal_id,'runner',symbol=row['symbol'],side=signal['side'],hold_mode=payload['hold_mode'],qty=remaining,target=plan['runner_target'])
                    await self.gateway.verify_protection(signal_id,'runner')
                    payload.setdefault('protection_evidence', {})['runner'] = payload.get('audit_context', {}).get('sources', [])
                    flags.update(['runner','tp1'])
                else: flags.add('tp1')
            payload.setdefault('management_messages',[]).append(identity)
            payload.pop('pending_management',None); payload.pop('management_plan',None)
            payload['management_flags']=sorted(flags)
            payload['detail']='持仓管理回执已核对；成本损包括实际开仓费与预估平仓费，不含资金费'
            self.save(signal_id,row['symbol'],'protected',payload)
            return payload['detail']
        except BaseException:
            payload['detail']='持仓管理等待恢复核对；不重复开仓或盲目重发减仓'
            self.save(signal_id,row['symbol'],'needs_reconciliation',payload)
            raise

    async def _cancel_if_pending(self,signal_id,leg,command_id):
        op=self.gateway.owned_protection(signal_id,leg)
        order_id=str(op['result']['data']['orderId'])
        async def pending():
            rows=(await self.gateway.client._request('GET','/api/v3/trade/unfilled-strategy-orders',params={'category':'USDT-FUTURES','type':'tpsl'})).get('data')
            if not isinstance(rows,list): raise BitgetError('无法核对保护单列表')
            return any(str(r.get('orderId'))==order_id for r in rows)
        if await pending():
            await self.gateway.cancel_protection(signal_id,leg,command_id)
            if await pending(): raise BitgetOrderUncertain('保护单取消尚未确认，不继续减仓')

    async def _opening_fees(self,order_id,expected_qty):
        cursor=None; seen=set(); ids=set(); fee=Decimal(0); quantity=Decimal(0)
        for _ in range(20):
            params={'category':'USDT-FUTURES','orderId':str(order_id),'limit':'100'}
            if cursor: params['cursor']=cursor
            data=(await self.gateway.client._request('GET','/api/v3/trade/fills',params=params)).get('data') or {}
            rows=data.get('list')
            if not isinstance(rows,list): raise BitgetError('无法核对开仓手续费')
            for row in rows:
                if str(row.get('orderId'))!=str(order_id) or not row.get('execId'):
                    raise BitgetOrderUncertain('成交明细身份不匹配')
                if row['execId'] in ids: continue
                ids.add(row['execId']); quantity+=number(row.get('execQty'),'成交数量')
                if not isinstance(row.get('feeDetail'),list): raise BitgetError('缺少手续费明细')
                for item in row['feeDetail']:
                    if item.get('feeCoin')!='USDT': raise BitgetError('存在非 USDT 手续费，不能精确计算成本损')
                    fee+=number(item.get('fee'),'手续费',positive=False)
            cursor=data.get('cursor')
            if not rows or not cursor: break
            if cursor in seen: raise BitgetError('成交分页异常')
            seen.add(cursor)
        else: raise BitgetError('成交明细未完整读取')
        if quantity != expected_qty: raise BitgetOrderUncertain('成交明细数量未对齐，稍后核对成本损')
        return fee

    async def request_manual_close(self, signal_id=None):
        async with self.lock:
            rows=[r for r in self.all() if signal_id is None or r['signal_id']==signal_id]
            if signal_id and not rows: raise BitgetError('找不到本程序跟单仓位')
            ids=[]
            # Persist every intent before the first exchange request; recovery resumes the batch.
            for row in rows:
                if row['state'] in {'closed','rejected'}: continue
                p=row['payload']; p['manual_close_requested']=True
                p['audit_context'] = {'actor': 'user', 'sources': []}
                p['detail']='用户请求市价平仓，等待持仓归属及成交核对'
                self.save(row['signal_id'],row['symbol'],'needs_reconciliation',p)
                ids.append(row['signal_id'])
            for sid in ids:
                try: await self._advance(sid)
                except Exception as exc:
                    row=next(r for r in self.all() if r['signal_id']==sid)
                    row['payload']['detail']=f'手动平仓等待核对：{exc}'
                    self.save(sid,row['symbol'],'needs_reconciliation',row['payload'])
            return ids

    async def _resume_manual_close(self,row):
        sid=row['signal_id']; p=row['payload']
        attempt=p.get('manual_close_attempt',0)
        identity=f'manual-close-{attempt}'
        key=f'{sid}:reduce:{identity}'
        op=self.gateway.journal.get(self.gateway.scope,key)
        if op is None:
            # Reconcile all owned fills first. Never flatten another user's mixed position.
            await self.reconcile_protected(row)
            current=next(r for r in self.all() if r['signal_id']==sid)
            if current['state']=='closed': return current
            if attempt >= 3: raise BitgetOrderUncertain('多次部分成交仍有剩余仓位，需要人工核对，不无限重试')
            qty=await self.current_quantity(row)
            if qty<=0: raise BitgetOrderUncertain('持仓正在同步，等待核对')
            await self.gateway.reduce(sid,identity,symbol=row['symbol'],side=p['signal']['side'],hold_mode=p['hold_mode'],qty=qty)
        result=await self.gateway.reconcile_order(key)
        if result['state']!='verified':
            raise BitgetOrderUncertain('平仓成交尚未确认，保留保护，不重复提交')
        await self.reconcile_protected(row)
        current=next(r for r in self.all() if r['signal_id']==sid)
        if current['state']!='closed':
            if Decimal(result['result']['data']['cumExecQty'])<=0:
                raise BitgetOrderUncertain('平仓订单未成交，请核对交易所状态')
            p['manual_close_attempt']=attempt+1
            p['detail']='已确认部分平仓，下一次核对处理剩余数量'
            self.save(sid,row['symbol'],'needs_reconciliation',p)
        return next(r for r in self.all() if r['signal_id']==sid)

    async def resume(self,signal_id):
        async with self.lock:
            return await self._advance(signal_id)

    async def _advance(self,signal_id):
        row=next((r for r in self.all() if r['signal_id']==signal_id),None)
        if not row: raise BitgetError('找不到本程序执行任务')
        if row['state'] in {'closed','rejected'}: return row
        payload=row['payload']; symbol=row['symbol']; preview=payload['preview']
        if payload.get('protection_update'):
            return await self._apply_protection_update(row)
        if payload.get('pending_management') or row['state']=='managing':
            if payload.get('management_plan'):
                await self._resume_management(row)
                return next(r for r in self.all() if r['signal_id']==signal_id)
            payload['detail']='持仓管理操作中断，保留现有保护并等待专项核对；不重新执行开仓保护流程'
            self.save(signal_id,symbol,'needs_reconciliation',payload)
            return next(r for r in self.all() if r['signal_id']==signal_id)
        if payload.get('protection_initialized'):
            try:
                if payload.get('manual_close_requested'):
                    return await self._resume_manual_close(row)
                await self.reconcile_protected(row)
            except Exception as exc:
                payload['detail']=str(exc)
                self.save(signal_id,symbol,'needs_reconciliation',payload)
                raise
            return next(r for r in self.all() if r['signal_id']==signal_id)
        entry=self.gateway.journal.get(self.gateway.scope,signal_id+':entry')
        if entry is None or entry['state'] == 'prepared':
            # The application may have crashed before sending. Never chase it on restart.
            await self._require_empty_symbol(symbol)
            payload['detail']='开单请求未提交，已核对币种无持仓和挂单；本次信号终止，不追补'
            self.save(signal_id,symbol,'rejected',payload)
            return next(r for r in self.all() if r['signal_id']==signal_id)
        if entry['state']=='rejected':
            self.save(signal_id,symbol,'rejected',payload)
            return next(r for r in self.all() if r['signal_id']==signal_id)
        try:
            entry=await self.gateway.reconcile_order(signal_id+':entry')
            order=entry['result']['data']
            if entry['state'] != 'verified':
                payload['detail']='交易所尚未确认最终成交数量；保留预设止损，不重复开单'
                self.save(signal_id,symbol,'awaiting_fill',payload)
                return next(r for r in self.all() if r['signal_id']==signal_id)
            qty=number(order['cumExecQty'],'成交数量',positive=False)
            if qty==0:
                payload['detail']='交易所已确认订单终结且没有成交'
                self.save(signal_id,symbol,'closed',payload)
                return next(r for r in self.all() if r['signal_id']==signal_id)
            payload['filled_qty']=str(qty); payload['entry_order_id']=order['orderId']
            payload['entry_price']=str(number(order.get('avgPrice'),'实际开仓均价'))
            payload['margin']=str(Decimal(payload['entry_price'])*qty/Decimal(preview['effective_leverage']))
            if payload['signal'].get('awaiting_protection'):
                await self.gateway.bind_preset_stop(signal_id,preview,qty)
                await self.gateway.verify_protection(signal_id,'sl')
                entry_price=Decimal(payload['entry_price'])
                initial_margin=entry_price*qty/Decimal(preview['effective_leverage'])
                stop=temporary_stop(entry_price,qty,initial_margin,payload['signal']['side'],preview['taker_fee_rate'],preview['price_step'])
                payload['initial_margin']=str(initial_margin)
                # Keep the exchange preset SL while adjusting for actual fill slippage.
                await self.gateway.modify_protection(signal_id,'sl','actual-fill',qty=qty,stop=stop)
                await self.gateway.verify_protection(signal_id,'sl','actual-fill')
                payload.update(current_stop=str(stop),protection_initialized=True,remaining_qty=str(qty),target_quantities=[],detail='已市价开仓并核对临时止损；最多等待保护回复 5 分钟')
                self.save(signal_id,symbol,'protected',payload)
                return next(r for r in self.all() if r['signal_id']==signal_id)
            step=Decimal(preview['quantity_step']); targets=preview['take_profits']
            try:
                sizes=target_sizes(qty,len(targets),step,payload.get('tp_percentages'))
                invalid_sizes=any(q < Decimal(preview['min_quantity']) or q*Decimal(t)<Decimal(preview['min_notional']) for q,t in zip(sizes,targets) if q>0)
            except ValueError:
                if preview.get('entry_guard',{}).get('mode')!='enforce': raise
                invalid_sizes=True
            if invalid_sizes:
                if preview.get('entry_guard',{}).get('mode')=='enforce':
                    # A final partial IOC fill is owned exposure. Verify its SL,
                    # persist exit intent, then use the existing recoverable close.
                    await self.gateway.bind_preset_stop(signal_id,preview,qty)
                    await self.gateway.verify_protection(signal_id,'sl')
                    payload.update(protection_initialized=True,remaining_qty=str(qty),target_quantities=[],
                                   manual_close_requested=True,guard_small_fill_exit=True,
                                   detail='IOC 部分成交不足以分档保护，已记录只减仓退出意图')
                    payload['audit_context']={'actor':'system','sources':[]}
                    payload['reduction_evidence']={'manual-close-0':{'label':'IOC 小额部分成交退出','actor':'system','sources':[]}}
                    self.save(signal_id,symbol,'needs_reconciliation',payload)
                    return await self._resume_manual_close(row)
                raise BitgetOrderUncertain('部分成交数量不足以分档保护；已有预设止损，需人工核对')
            self.save(signal_id,symbol,'protecting',payload)
            signal=ParsedSignal.model_validate(payload['signal'])
            common={'symbol':symbol,'side':signal.side.value,'hold_mode':payload['hold_mode']}
            await self.gateway.bind_preset_stop(signal_id,preview,qty)
            await self.gateway.verify_protection(signal_id,'sl')
            payload['target_quantities']=[str(q) for q in sizes]
            for index,(target,size) in enumerate(zip(targets,sizes)):
                if size == 0: continue
                leg=f'tp{index+1}'
                await self.gateway.protect(signal_id,leg,**common,qty=size,target=target)
                await self.gateway.verify_protection(signal_id,leg)
            payload['detail']='开仓成交已核对；止损及全部分档止盈已在交易所待触发列表确认'
            payload['protection_initialized']=True
            payload['remaining_qty']=str(qty)
            self.save(signal_id,symbol,'protected',payload)
        except BaseException:
            payload['detail']='成交或保护状态未完全确认；保留原预设止损，停止后续开仓并等待核对'
            self.save(signal_id,symbol,'needs_reconciliation',payload)
            raise
        return next(r for r in self.all() if r['signal_id']==signal_id)

    async def receive_protection(self,signal):
        async with self.lock:
            row=next((r for r in self.all() if r['signal_id']==signal.id),None)
            if not row or row['state'] in {'closed','rejected'}:
                return False
            p=row['payload']
            if not p['signal'].get('awaiting_protection'): return False
            if signal.source_chat_id!=p['signal']['source_chat_id'] or signal.symbol!=row['symbol'] or signal.side!=p['signal']['side']:
                raise BitgetError('保护回复与原仓位身份不符')
            if datetime.now(UTC)>=datetime.fromisoformat(p['protection_deadline']):
                await self._advance(signal.id)
                raise BitgetError('保护回复超过 5 分钟，不恢复或重新开仓')
            if not p.get('protection_initialized'):
                raise BitgetOrderUncertain('原开仓尚未核对完成，不能修改未知仓位')
            if p.get('protection_update'):
                return await self._apply_protection_update(row)
            price=await self.gateway.client.market_price(signal.symbol)
            # Protective levels must still be executable now, not merely valid at entry.
            ParsedSignal.model_validate({**signal.model_dump(),'entry_low':price,'entry_high':price})
            qty=await self.current_quantity(row)
            if qty!=Decimal(p['filled_qty']): raise BitgetOrderUncertain('等待回复期间仓位变化，不猜测分档数量')
            tick=Decimal(p['preview']['price_step'])
            from decimal import ROUND_HALF_UP
            targets=[(t/tick).to_integral_value(rounding=ROUND_HALF_UP)*tick for t in signal.take_profits]
            stop=(signal.stop_loss/tick).to_integral_value(rounding=ROUND_HALF_UP)*tick
            ParsedSignal.model_validate({**signal.model_dump(),'entry_low':price,'entry_high':price,'stop_loss':stop,'take_profits':targets})
            sizes=target_sizes(qty,len(targets),Decimal(p['preview']['quantity_step']),p.get('tp_percentages'))
            if any(q<Decimal(p['preview']['min_quantity']) or q*t<Decimal(p['preview']['min_notional']) for q,t in zip(sizes,targets) if q>0):
                raise BitgetError('回复分档低于交易所最小订单，保留临时止损及原超时期限')
            p['protection_update']={'signal':signal.model_dump(mode='json'),'targets':[str(t) for t in targets],'sizes':[str(q) for q in sizes],'stop':str(stop)}
            p['audit_context'] = {'actor': 'signal', 'sources': signal_evidence(signal)}
            self.save(signal.id,row['symbol'],'protecting',p)
            return await self._apply_protection_update(row)

    async def _apply_protection_update(self,row):
        p=row['payload']; update=p['protection_update']; sid=row['signal_id']
        try:
            qty=Decimal(p['filled_qty'])
            await self.gateway.modify_protection(sid,'sl','blogger-protection',qty=qty,stop=update['stop'])
            await self.gateway.verify_protection(sid,'sl','blogger-protection')
            p['current_stop']=update['stop']
            p['protection_sources'] = signal_evidence(update['signal'])
            p['protection_evidence'] = {name: signal_evidence(update['signal']) for name in ['sl', *[f'tp{i+1}' for i in range(len(update['targets']))]]}
            for i,(target,size) in enumerate(zip(update['targets'],update['sizes'])):
                if Decimal(size) == 0: continue
                await self.gateway.protect(sid,f'tp{i+1}',symbol=row['symbol'],side=p['signal']['side'],hold_mode=p['hold_mode'],qty=size,target=target)
                await self.gateway.verify_protection(sid,f'tp{i+1}')
            p.update(signal=update['signal'],target_quantities=update['sizes'],detail='原仓位已更新博主止盈止损；三档按开仓时保存的比例分配初始成交数量')
            p['preview']['take_profits']=update['targets']
            p.pop('protection_update')
            self.save(sid,row['symbol'],'protected',p)
            return next(r for r in self.all() if r['signal_id']==sid)
        except BaseException:
            self.save(sid,row['symbol'],'needs_reconciliation',p)
            raise
