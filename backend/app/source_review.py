"""Observed source evidence and attributable outcomes, never a fraud verdict."""
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal as D


class SourceReview:
    def __init__(self, store):
        self.store=store
        with store._connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS source_message_events (
                identity TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
                kind TEXT NOT NULL, observed_at TEXT NOT NULL)''')
            conn.execute('CREATE INDEX IF NOT EXISTS source_event_message ON source_message_events(chat_id,message_id)')

    def observe_message(self, message):
        kind=message.get('origin')
        if kind not in {'edit','delete'} or message.get('chat_id') is None:
            return
        # Multiple receive/parse notifications of one edit count only once.
        raw=f"{message['chat_id']}:{message['message_id']}:{kind}:{message.get('edited_at')}:{message.get('text','')}"
        identity=hashlib.sha256(raw.encode()).hexdigest()
        with self.store._connect() as conn:
            conn.execute('INSERT OR IGNORE INTO source_message_events VALUES (?,?,?,?,?)',
                         (identity,message['chat_id'],message['message_id'],kind,datetime.now(UTC).isoformat()))

    def summarize(self, guard, outcomes):
        decisions=guard.recent(1000)
        groups={}
        for d in decisions:
            key=d['source_key']
            group=groups.setdefault(key,{'source_name':d['source_name'],'source_key':key,'signals':0,
                'skipped':0,'resized':0,'premove':[],'closed':0,'wins':0,'net':D(0),'returns':[],
                'slippage':[],'edited_messages':0,'deleted_messages':0})
            group['signals']+=1
            group['skipped']+=int(d['action']=='skip')
            group['resized']+=int(d['action']=='resize')
            if d.get('pre_move_percent') is not None:
                group['premove'].append(D(d['pre_move_percent']))
            for outcome in outcomes.get(d['signal_id'],[]):
                if outcome.get('entry_price') and outcome.get('reference'):
                    slip=(D(outcome['entry_price'])/D(outcome['reference'])-1)*100*(1 if outcome['side']=='long' else -1)
                    group['slippage'].append(slip)
                if outcome.get('net') is not None and D(outcome.get('notional') or 0)>0:
                    pnl=D(outcome['net'])
                    group['closed']+=1; group['wins']+=int(pnl>0); group['net']+=pnl
                    group['returns'].append(pnl/D(outcome['notional'])*100)
        with self.store._connect() as conn:
            # Count only messages that are roots of evaluated signals, not all chatter.
            for d in decisions:
                saved=conn.execute('SELECT payload FROM signals WHERE id=?',(d['signal_id'],)).fetchone()
                signal=json.loads(saved['payload']) if saved else {}
                if signal.get('source_chat_id') is None:
                    continue
                kinds={r['kind'] for r in conn.execute('SELECT DISTINCT kind FROM source_message_events WHERE chat_id=? AND message_id=?',
                    (signal['source_chat_id'],signal.get('source_message_id')))}
                group=groups[d['source_key']]
                group['edited_messages']+=int('edit' in kinds)
                group['deleted_messages']+=int('delete' in kinds)
        result=[]
        for g in groups.values():
            pre=g.pop('premove'); returns=g.pop('returns'); slip=g.pop('slippage')
            average=sum(returns,D(0))/len(returns) if returns else None
            # A transparent empirical indicator, not a prediction of future profits.
            score=max(0,min(100,round(D(50)+(average or 0)*10))) if g['closed']>=20 else None
            g.update(net=str(g['net']),score=score,average_net_return_percent=str(average) if average is not None else None,
                     pre_move_samples=len(pre),average_pre_move_percent=str(sum(pre,D(0))/len(pre)) if pre else None,
                     average_entry_deviation_percent=str(sum(slip,D(0))/len(slip)) if slip else None,
                     verdict='样本不足（至少 20 笔已平仓）' if score is None else '近期净收益偏弱' if score<50 else '近期净收益为正' if score>50 else '近期持平')
            result.append(g)
        return result
