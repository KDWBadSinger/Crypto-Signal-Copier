"""Explicit desktop activation, persisted intent and recovery-before-entry."""
import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from .bitget import BitgetError
from .execution_journal import ExecutionJournal
from .uta import UtaReadiness
from .uta_executor import UtaExecutor
from .uta_gateway import UtaGateway
from .uta_risk import UtaRiskLimits


class UtaRuntime:
    def __init__(self,client,store):
        self.client,self.store=client,store
        self.journal=ExecutionJournal(store)
        self.gateway=UtaGateway(client,self.journal,self._authorize_write)
        self.engine=UtaExecutor(self.gateway,store,self._authorize_new)
        self.host_authorized=False
        self.enabled=store.get_setting(self._key('enabled'))=='true'
        self.management_authorized=store.get_setting(self._key('authorized'))=='true'
        self.ready=False; self.task=None; self.last_check=None; self.error=''

    def _key(self,name): return 'uta_'+self.gateway.scope+'_'+name

    def limits(self):
        value=self.store.get_setting('uta_risk_limits')
        return UtaRiskLimits.model_validate_json(value) if value else UtaRiskLimits()

    def configure(self,limits):
        validated=UtaRiskLimits.model_validate(limits.model_dump())
        self.store.set_setting('uta_risk_limits',validated.model_dump_json())
        return self.status()

    def _authorize_write(self):
        if not self.host_authorized or not self.management_authorized:
            raise BitgetError('请在受保护桌面窗口明确授权 UTA 交易；保存 API 或参数不会开启交易')

    def _authorize_new(self):
        self._authorize_write(); self.limits()
        if not self.enabled or not self.ready: raise BitgetError('新开仓已暂停或正在恢复核对')
        if any(r['state'] in {'needs_reconciliation','managing','awaiting_fill','protecting'} for r in self.engine.all()):
            raise BitgetError('有交易正在执行或等待核对，暂停新开仓')

    async def activate(self,confirmation):
        if not self.host_authorized: raise BitgetError('实盘只能从受保护桌面应用启用')
        if confirmation!='我确认启用实盘自动跟单': raise BitgetError('请输入完整实盘风险确认文字')
        self.limits()
        check=await UtaReadiness(self.client).check()
        if not check.get('read_access') or not all(c['ok'] for c in check['checks']):
            raise BitgetError('账户类型、权限或环境未通过检查，请查看连接管理')
        self.management_authorized=True
        self.store.set_setting(self._key('authorized'),'true')
        await self.reconcile()
        if any(r['state'] not in {'protected','closed','rejected'} for r in self.engine.all()):
            raise BitgetError('已有交易尚未完成恢复；继续管理，但暂不启用新开仓')
        self.enabled=True; self.ready=True
        self.store.set_setting(self._key('enabled'),'true')
        return self.status()

    def pause(self):
        self.enabled=False
        self.store.set_setting(self._key('enabled'),'false')
        return self.status()

    def selected_sources(self):
        import json
        value = self.store.get_setting(self._key('sources'))
        return json.loads(value) if value is not None else None

    def set_sources(self, chat_ids):
        import json
        self.store.set_setting(self._key('sources'), json.dumps(sorted(set(chat_ids))))
        return self.status()

    async def start(self):
        if not self.task or self.task.done(): self.task=asyncio.create_task(self._loop())

    async def stop(self):
        if self.task:
            self.task.cancel()
            try: await self.task
            except asyncio.CancelledError: pass
        self.ready=False

    async def _loop(self):
        while True:
            if self.host_authorized and self.management_authorized:
                try: await self.reconcile()
                except Exception as exc:
                    self.ready=False; self.error=str(exc)
            await asyncio.sleep(5)

    async def reconcile(self):
        failures=[]
        for row in self.engine.all():
            if row['state'] in {'closed','rejected'}: continue
            try:
                result=await self.engine.resume(row['signal_id'])
                self._sync_signal(result)
            except Exception as exc: failures.append(str(exc))
        self.last_check=datetime.now(UTC).isoformat()
        self.error='；'.join(dict.fromkeys(failures))
        self.ready=not failures and all(r['state'] in {'protected','closed','rejected'} for r in self.engine.all())
        return self.status()

    async def ingest(self,signal,leverage,default_max_percent=50,tp_percentages=None):
        sources = self.selected_sources()
        if sources is not None and signal.source_chat_id not in sources:
            raise BitgetError('该频道未选为实盘跟单来源')
        self._authorize_new()
        try:
            row=await self.engine.start(signal,limits=self.limits(),requested_leverage=leverage,default_max_percent=default_max_percent,tp_percentages=tp_percentages)
            self._sync_signal(row)
            return row
        except Exception:
            row=next((r for r in self.engine.all() if r['signal_id']==signal.id),None)
            if row: self._sync_signal(row)
            raise

    def _sync_signal(self,row):
        from .models import SignalStatus
        try: signal=self.store.get(row['signal_id'])
        except (KeyError,ValueError): return
        if signal is None: return
        p=row['payload']; state=row['state']
        signal.bitget_order_id=p.get('entry_order_id')
        signal.execution_size=Decimal(p['filled_qty']) if p.get('filled_qty') else None
        if p.get('filled_qty') and not signal.execution_at: signal.execution_at=datetime.now(UTC)
        status=SignalStatus.SUBMITTED if state in {'protected','closed'} else SignalStatus.REJECTED if state=='rejected' else SignalStatus.UNKNOWN
        detail=p.get('detail','等待交易所核对')
        if signal.status==status and signal.execution_detail==detail: return
        self.store.update_status(signal,status,order_id=p.get('entry_order_id'),detail=detail)

    async def manage(self,message,signal_id=None):
        self._authorize_write(); command=message['management']
        matches=[r for r in self.engine.all() if r['state']=='protected' and
                 r['payload']['signal']['source_chat_id']==message['chat_id'] and
                 (not command.get('symbol') or r['symbol']==command['symbol']) and
                 (not signal_id or r['signal_id']==signal_id) and
                 (not message.get('reply_to_message_id') or signal_id or
                  r['payload']['signal']['source_message_id']==message['reply_to_message_id'])]
        if len(matches)!=1: raise BitgetError('管理指令未定位到唯一同频道实盘持仓')
        row=matches[0]; detail=await self.engine.manage(message,row['signal_id'])
        self.store.add_audit(row['signal_id'],'uta_management',detail)
        return row['signal_id'],detail

    async def close_positions(self, signal_id=None):
        self._authorize_write()
        if signal_id is None: self.pause()
        await self.engine.request_manual_close(signal_id)
        self.ready=False
        self.store.add_audit(signal_id,'uta_manual_close','用户请求平仓；全部平仓同时暂停新开仓' if signal_id is None else '用户请求单笔平仓，等待成交核对')
        return self.status()

    def status(self):
        return {'enabled':self.enabled,'ready':self.ready,'management_authorized':self.management_authorized,
                'selected_sources': self.selected_sources(),
                'desktop_only':not self.host_authorized,'limits':self.limits().model_dump(mode='json'),
                'hard_limits':{'position_percent':'7','max_leverage':None,'max_positions':6},
                'leverage_policy':'未配置币种采用币种杠杆页保存的最大杠杆比例向下取整；0% 暂停未配置币种新开仓；单币种配置优先',
                'margin_mode':'crossed','last_check':self.last_check,'error':self.error,
                'workflows':[{'signal_id':r['signal_id'],'symbol':r['symbol'],'state':r['state'],
                    'side':r['payload']['signal']['side'],
                    'created_at':r['payload'].get('entry_signal',r['payload']['signal']).get('received_at'),
                    'entry_price':r['payload'].get('entry_price'),
                    'entry_low':r['payload']['signal'].get('entry_low'),
                    'take_profits':r['payload'].get('preview',{}).get('take_profits',[]),
                    'tp_percentages':r['payload'].get('tp_percentages',[40,40,20]),
                    'protection_states':r['payload'].get('protection_states',{}),
                    'manual_close_requested':r['payload'].get('manual_close_requested',False),
                    'detail':r['payload'].get('detail'),'remaining_qty':r['payload'].get('remaining_qty'),
                    'margin':r['payload'].get('margin'),'leverage':r['payload'].get('preview',{}).get('effective_leverage'),
                    'awaiting_protection':r['payload']['signal'].get('awaiting_protection',False),
                    'protection_deadline':r['payload'].get('protection_deadline'),
                    'current_stop':r['payload'].get('current_stop',r['payload'].get('preview',{}).get('payload',{}).get('stopLoss')),
                    'realized_after_fees':r['payload'].get('realized_after_fees'),'updated_at':r['updated_at']} for r in self.engine.all()],
                'detail':'停止新开仓后仍管理已有持仓；关闭应用后交易所保护单保留，重新打开先核对再跟单。'}

    def performance(self):
        daily={}; seen=set()
        for row in self.engine.all():
            for f in row['payload'].get('fills',[]):
                if f['id'] in seen: continue
                seen.add(f['id'])
                day=datetime.fromtimestamp(int(f['at'])/1000,UTC).date().isoformat()
                daily[day]=daily.get(day,Decimal(0))+Decimal(f['pnl'])-Decimal(f['fee'])
        total=Decimal(0); points=[]
        for day,value in sorted(daily.items()):
            total+=value; points.append({'day':day,'daily':str(value),'cumulative':str(total)})
        return {'points':points,'realized_after_fees':str(total),
                'detail':'交易所已核对成交的已实现盈亏减交易手续费；不含资金费、浮盈、充值提现。异常待核对期间可能延迟。'}
