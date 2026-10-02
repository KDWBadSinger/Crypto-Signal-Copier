"""Durable write-ahead journal: never retry an exchange mutation blindly."""
import hashlib
import json
from datetime import UTC, datetime

from .bitget import BitgetError, BitgetOrderUncertain


class ExecutionJournal:
    def __init__(self, store):
        self.store = store
        with store._connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS exchange_operations (
                    scope TEXT NOT NULL, operation_key TEXT NOT NULL, kind TEXT NOT NULL,
                    payload TEXT NOT NULL, state TEXT NOT NULL, result TEXT, detail TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(scope, operation_key)
                );
            ''')

    @staticmethod
    def client_id(scope, key):
        return 'csc_'+hashlib.sha256((scope+'|'+key).encode()).hexdigest()[:28]

    def prepare(self, scope, key, kind, payload):
        encoded = json.dumps(payload,sort_keys=True,separators=(',',':'))
        now = datetime.now(UTC).isoformat()
        with self.store._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = conn.execute('SELECT * FROM exchange_operations WHERE scope=? AND operation_key=?',(scope,key)).fetchone()
            if old:
                if old['payload'] != encoded or old['kind'] != kind:
                    raise BitgetError('相同操作 ID 的参数发生变化，拒绝覆盖或重复提交')
            else:
                conn.execute('INSERT INTO exchange_operations VALUES (?,?,?,?,?,?,?,?,?)',
                             (scope,key,kind,encoded,'prepared',None,None,now,now))
        return self.get(scope,key)

    def get(self, scope, key):
        with self.store._connect() as conn:
            row = conn.execute('SELECT * FROM exchange_operations WHERE scope=? AND operation_key=?',(scope,key)).fetchone()
        if not row:
            return None
        value=dict(row)
        value['payload']=json.loads(value['payload'])
        value['result']=json.loads(value['result']) if value['result'] else None
        return value

    def claim(self, scope, key):
        with self.store._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            count=conn.execute("UPDATE exchange_operations SET state='inflight', updated_at=? WHERE scope=? AND operation_key=? AND state='prepared'",
                               (datetime.now(UTC).isoformat(),scope,key)).rowcount
        return count == 1

    def update(self, scope, key, state, result=None, detail=None):
        if state not in {'unknown','acknowledged','verified','rejected'}:
            raise ValueError('Unsupported journal transition')
        with self.store._connect() as conn:
            changed=conn.execute("UPDATE exchange_operations SET state=?,result=?,detail=?,updated_at=? WHERE scope=? AND operation_key=? AND state IN ('inflight','unknown','acknowledged')",
                                (state,json.dumps(result) if result is not None else None,detail,datetime.now(UTC).isoformat(),scope,key)).rowcount
        if not changed:
            raise BitgetError('操作状态已改变或尚未提交，不能更新交易结果')

    def unresolved(self, scope):
        with self.store._connect() as conn:
            keys=conn.execute("SELECT operation_key FROM exchange_operations WHERE scope=? AND state IN ('inflight','unknown','acknowledged') ORDER BY created_at",(scope,)).fetchall()
        return [self.get(scope,r['operation_key']) for r in keys]

    def operations(self, scope, prefix):
        with self.store._connect() as conn:
            keys=conn.execute('SELECT operation_key FROM exchange_operations WHERE scope=? ORDER BY created_at',(scope,)).fetchall()
        return [self.get(scope,r['operation_key']) for r in keys if r['operation_key'].startswith(prefix)]

    async def dispatch(self, scope, key, kind, payload, send, *, authorize):
        """authorize must recheck the current account and explicit execution gate."""
        operation=self.prepare(scope,key,kind,payload)
        if operation['state'] in {'acknowledged','verified'}:
            return operation['result']
        if operation['state'] != 'prepared':
            raise BitgetOrderUncertain('该操作已提交或结果待核对，不允许自动重发')
        authorize()
        if not self.claim(scope,key):
            raise BitgetOrderUncertain('该操作已被其他执行流程接管，不重复提交')
        try:
            result=await send(payload)
            if not isinstance(result,dict) or result.get('code') != '00000':
                raise BitgetOrderUncertain('交易请求响应无法确认')
            if kind in {'entry','reduce','protect'} and not (result.get('data') or {}).get('orderId'):
                raise BitgetOrderUncertain('交易所未返回订单 ID，必须先核对')
        except BitgetOrderUncertain:
            self.update(scope,key,'unknown',detail='交易所结果不确定；仅允许查询核对，不自动重发')
            raise
        except BitgetError:
            self.update(scope,key,'rejected',detail='交易所明确拒绝，请核对审计记录')
            raise
        except BaseException:
            # Cancellation and application shutdown are as ambiguous as a timeout.
            self.update(scope,key,'unknown',detail='请求中断或进程取消；禁止自动重发')
            raise
        self.update(scope,key,'acknowledged',result=result,detail='交易所已接收，不等于成交或保护已生效')
        return result
