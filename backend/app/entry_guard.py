"""Timestamped, direction-aware entry capacity estimates. Never an exit gate.

Amounts are position notional in USDT, not margin. Displayed liquidity and
reported turnover are observations, not promises of future executable depth.
"""
import asyncio
import json
import time
from datetime import UTC, datetime
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .bitget import BitgetError
from .uta import number
from .follow_policy import temporary_stop

D = Decimal


class EntryGuardSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    mode: Literal['enforce', 'observe', 'off'] = 'enforce'
    max_notional: Decimal = Field(default=D('20000'), gt=0, le=10000000)
    depth_percent: Decimal = Field(default=D('10'), gt=0, le=30)
    volume_percent: Decimal = Field(default=D('5'), gt=0, le=20)
    entry_slippage_percent: Decimal = Field(default=D('0.8'), gt=0, le=3)
    exit_band_percent: Decimal = Field(default=D('2'), ge=D('.2'), le=5)
    remaining_depth_percent: Decimal = Field(default=D('50'), ge=10, le=100)
    max_spread_percent: Decimal = Field(default=D('0.8'), gt=0, le=3)
    chase_soft_percent: Decimal = Field(default=D('2'), ge=D('.1'), le=10)
    chase_hard_percent: Decimal = Field(default=D('5'), ge=D('.2'), le=15)
    pre_move_soft_percent: Decimal = Field(default=D('6'), ge=1, le=20)
    pre_move_hard_percent: Decimal = Field(default=D('15'), ge=2, le=40)
    thin_leverage_cap: int = Field(default=10, ge=1, le=100, strict=True)
    risk_percent: Decimal = Field(default=D('3'), gt=0, le=7)

    @model_validator(mode='after')
    def ordered_thresholds(self):
        if self.chase_soft_percent >= self.chase_hard_percent or self.pre_move_soft_percent >= self.pre_move_hard_percent:
            raise ValueError('降仓阈值必须低于跳过阈值')
        return self


def signal_time(signal):
    """Use only the root message's send time; missing is explicitly unknown."""
    for item in signal.source_messages:
        if item.get('message_id') == signal.source_message_id and item.get('chat_id') == signal.source_chat_id:
            try:
                value = datetime.fromisoformat(item['sent_at'].replace('Z', '+00:00'))
                if value.tzinfo:
                    return value.timestamp()
            except (KeyError, TypeError, ValueError):
                pass
    return None


def parse_book(data, now_ms):
    if not isinstance(data, dict):
        raise BitgetError('盘口结构无效')
    ts = int(number(data.get('ts'), '盘口时间'))
    if not -1000 <= now_ms-ts <= 3000:
        raise BitgetError('盘口超过 3 秒或时间异常，本次不使用旧数据')
    sides = []
    for key, reverse in [('a', False), ('b', True)]:
        values = data.get(key)
        if not isinstance(values, list) or not values:
            raise BitgetError('缺少双边盘口')
        rows = [(number(r[0], '盘口价'), number(r[1], '盘口量')) for r in values if isinstance(r, list) and len(r) == 2]
        if len(rows) != len(values) or len({p for p, _ in rows}) != len(rows):
            raise BitgetError('盘口档位无效或重复')
        if rows != sorted(rows, reverse=reverse):
            raise BitgetError('盘口排序异常')
        sides.append(rows)
    asks, bids = sides
    if bids[0][0] >= asks[0][0]:
        raise BitgetError('盘口交叉，等待更新')
    return asks, bids, ts


def closed_candles(data, now_ms):
    rows = {}
    if not isinstance(data, list):
        raise BitgetError('分钟成交数据无效')
    for r in data:
        if not isinstance(r, list) or len(r) < 7:
            raise BitgetError('分钟成交数据缺失')
        ts = int(number(r[0], '分钟时间'))
        if ts % 60000:
            raise BitgetError('分钟数据时间未对齐')
        if ts+60000 <= now_ms:
            turnover = number(r[6], '成交额', positive=False)
            if turnover < 0 or ts in rows:
                raise BitgetError('分钟成交额无效或数据重复')
            rows[ts] = (number(r[1], '开盘价'), number(r[4], '收盘价'), turnover)
    expected = now_ms//60000*60000-60000
    recent = [expected-i*60000 for i in range(5)]
    if any(ts not in rows for ts in recent):
        raise BitgetError('最近 5 根完整分钟线缺失，不外推成交容量')
    # A sudden single-minute spike must not inflate the capacity estimate.
    volume = min(rows[expected][2], sum((rows[t][2] for t in recent), D(0))/5)
    return rows, volume


def sweep(rows, quantity):
    remaining, cost = quantity, D(0)
    for price, available in rows:
        taken = min(remaining, available)
        cost += taken*price
        remaining -= taken
        if remaining <= 0:
            return cost/quantity
    raise BitgetError('可见盘口不足以估算完整成交')


def estimate(signal, settings, book, candles, *, notional, equity, leverage, step, tick, min_qty, min_notional, fee, now_ms, existing_notional=D(0)):
    if fee < 0 or leverage < 1:
        raise BitgetError('费率或杠杆无效')
    asks, bids, ts = parse_book(book, now_ms)
    rows, minute_volume = closed_candles(candles, now_ms)
    mid = (asks[0][0]+bids[0][0])/2
    direction = D(1) if signal.side == 'long' else D(-1)
    buying = direction == 1
    entry_rows, exit_rows = (asks, bids) if buying else (bids, asks)
    best = entry_rows[0][0]
    spread = (asks[0][0]-bids[0][0])/mid*100
    boundary = best*(1+direction*settings.entry_slippage_percent/100)
    chase_boundary=signal.reference_entry*(1+direction*settings.chase_hard_percent/100)
    boundary=min(boundary,chase_boundary) if buying else max(boundary,chase_boundary)
    # Buy limit rounds DOWN, sell limit UP: never widen the user's price bound.
    limit_price = (boundary/tick).to_integral_value(rounding=ROUND_DOWN if buying else ROUND_UP)*tick
    if not signal.market_entry:
        signal_limit=signal.entry_high if buying else signal.entry_low
        signal_limit=(signal_limit/tick).to_integral_value(rounding=ROUND_DOWN if buying else ROUND_UP)*tick
        limit_price=min(limit_price,signal_limit) if buying else max(limit_price,signal_limit)
    eligible = [(p,q) for p,q in entry_rows if direction*(p-limit_price) <= 0]
    exit_eligible = [(p,q) for p,q in exit_rows if abs(p/mid-1)*100 <= settings.exit_band_percent]
    entry_depth = sum((p*q for p,q in eligible), D(0))
    exit_depth = sum((p*q for p,q in exit_eligible), D(0))
    sent = signal_time(signal)
    premove = None
    if sent is not None and 0 <= now_ms-int(sent*1000) <= 120000:
        # Five fully closed bars before the send minute; never include post-signal data.
        end = int(sent*1000)//60000*60000
        before = [end-i*60000 for i in range(1,6)]
        if all(t in rows for t in before):
            premove = direction*(rows[before[0]][1]/rows[before[-1]][0]-1)*100
    chase = max(D(0), direction*(best/signal.reference_entry-1)*100)
    caps = {
        'absolute': settings.max_notional,
        'entry_depth': entry_depth*settings.depth_percent/100,
        'exit_depth': exit_depth*settings.depth_percent/100*settings.remaining_depth_percent/100,
        'volume': minute_volume*settings.volume_percent/100,
    }
    caps={key:max(D(0),value-existing_notional) for key,value in caps.items()}
    reasons, blocked = [], False
    if spread > settings.max_spread_percent:
        reasons.append('买卖价差超过上限'); blocked = True
    if chase > settings.chase_hard_percent:
        reasons.append('相对博主参考价追价过远'); blocked = True
    if premove is not None and premove > settings.pre_move_hard_percent:
        reasons.append('喊单前 5 分钟同向涨跌幅过大'); blocked = True
    hot = chase > settings.chase_soft_percent or (premove is not None and premove > settings.pre_move_soft_percent)
    thin = min(caps[k] for k in ('entry_depth','exit_depth','volume')) < notional or spread > settings.max_spread_percent/2
    effective = min(leverage, settings.thin_leverage_cap) if thin or hot else leverage
    # Reducing leverage never reallocates the margin into a larger position.
    caps['leverage'] = notional*D(effective)/D(leverage)
    stop_fraction = D(1)/effective if signal.awaiting_protection else abs(limit_price-signal.stop_loss)/limit_price
    caps['risk'] = equity*settings.risk_percent/100/(stop_fraction+settings.exit_band_percent/100+2*fee)
    if hot:
        caps['heat'] = notional*D('.5')
        reasons.append('信号拥挤，目标仓位减半')
    if effective < leverage:
        reasons.append(f'偏薄或拥挤盘口，杠杆由 {leverage}x 降至 {effective}x')
    if premove is None:
        reasons.append('缺少可验证的原消息时间或前序分钟线，喊单前异动未知')
    selected = min(notional, *caps.values())
    # Use the larger reference for notional/margin accounting on either side.
    sizing_price = max(best, limit_price)
    qty = (selected/sizing_price/step).to_integral_value(rounding=ROUND_DOWN)*step
    avg, exit_avg = None, None
    if qty <= 0 or qty < min_qty or qty*min(best, limit_price) < min_notional:
        reasons.append('缩单后低于合约最小数量或金额'); blocked = True
    if not blocked:
        avg = sweep(eligible, qty)
        stress_rows = [(p,q*settings.remaining_depth_percent/100) for p,q in exit_eligible]
        exit_avg = sweep(stress_rows, qty)
        if not signal.awaiting_protection:
            try:
                for price in (best, limit_price, avg):
                    type(signal).model_validate({**signal.model_dump(), 'entry_low':price, 'entry_high':price})
            except ValueError:
                reasons.append('限价成交范围越过信号止盈或止损'); blocked = True
    approved = D(0) if blocked else qty*sizing_price
    limiting = [k for k,v in caps.items() if v < notional]
    if limiting:
        names = {'absolute':'单币金额上限','entry_depth':'进场深度','exit_depth':'压力退出深度','volume':'近期成交额','risk':'单笔亏损预算','leverage':'杠杆调整','heat':'追价/提前异动'}
        reasons.append('限制因素：'+'、'.join(names[k] for k in limiting))
    return {
        'action':'skip' if blocked else 'resize' if approved < notional*D('.99') or effective != leverage else 'allow',
        'requested_notional':str(notional), 'approved_notional':str(approved), 'quantity':str(qty if not blocked else 0),
        'requested_leverage':leverage, 'effective_leverage':effective,
        'estimated_margin':str(approved/effective), 'limit_price':str(limit_price),
        'estimated_fill':str(avg) if avg is not None else None,
        'entry_slippage_percent':str(max(D(0), direction*(avg/best-1)*100)) if avg is not None else None,
        'stress_exit_slippage_percent':str(max(D(0), direction*(mid-exit_avg)/mid*100)) if exit_avg is not None else None,
        'spread_percent':str(spread), 'chase_percent':str(chase), 'pre_move_percent':str(premove) if premove is not None else None,
        'entry_depth':str(entry_depth), 'exit_depth':str(exit_depth), 'minute_turnover':str(minute_volume),
        'existing_notional':str(existing_notional),
        'caps':{k:str(v) for k,v in caps.items()}, 'reasons':reasons or ['盘口与价格条件通过'],
        'book_ts':ts, 'evaluated_at':datetime.fromtimestamp(now_ms/1000, UTC).isoformat(),
        'signal_sent_at':datetime.fromtimestamp(sent, UTC).isoformat() if sent else None,
        'mode':settings.mode, 'settings':settings.model_dump(mode='json'),
    }


class EntryGuard:
    def __init__(self, client, store, scope):
        self.client, self.store, self.scope = client, store, scope
        with store._connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS entry_decisions (
                scope TEXT NOT NULL, signal_id TEXT NOT NULL, source_key TEXT NOT NULL,
                source_name TEXT NOT NULL, symbol TEXT NOT NULL, payload TEXT NOT NULL,
                updated_at TEXT NOT NULL, PRIMARY KEY(scope,signal_id))''')

    def settings(self):
        value = self.store.get_setting('entry_guard_'+self.scope)
        return EntryGuardSettings.model_validate_json(value) if value else EntryGuardSettings()

    def configure(self, settings):
        self.store.set_setting('entry_guard_'+self.scope, settings.model_dump_json())

    async def market(self, symbol):
        async def get(path, params):
            response = await self.client._http.get(path, params=params, timeout=2)
            response.raise_for_status()
            result = response.json()
            if result.get('code') != '00000':
                raise BitgetError('公开盘口或成交数据暂不可用')
            return result.get('data')
        base = {'category':'USDT-FUTURES', 'symbol':symbol}
        return await asyncio.wait_for(asyncio.gather(
            get('/api/v3/market/orderbook', {**base, 'limit':'200'}),
            get('/api/v3/market/candles', {**base, 'interval':'1m', 'limit':'20', 'type':'market'})), timeout=2.5)

    async def assess(self, signal, *, notional, equity, leverage, step, tick, min_qty, min_notional, fee, settings=None, existing_notional=D(0)):
        cfg = settings or self.settings()
        if cfg.mode == 'off':
            return None
        try:
            book, candles = await self.market(signal.symbol)
            result = estimate(signal, cfg, book, candles, notional=number(notional,'计划名义金额'),
                              equity=number(equity,'权益'), leverage=leverage, step=number(step,'数量步长'),
                              tick=number(tick,'价格步长'), min_qty=number(min_qty,'最小数量'),
                              min_notional=number(min_notional,'最小金额'), fee=number(fee,'费率',positive=False),
                              now_ms=int(time.time()*1000),existing_notional=existing_notional)
        except (BitgetError, ValueError, TypeError, KeyError, ArithmeticError, httpx.HTTPError, asyncio.TimeoutError) as exc:
            # Never log URLs, credentials, or arbitrary exchange error bodies.
            reason = str(exc) if isinstance(exc, BitgetError) else '公开数据缺失、无效或超时'
            result = {'action':'skip', 'requested_notional':str(notional), 'approved_notional':'0',
                      'requested_leverage':leverage, 'effective_leverage':leverage, 'mode':cfg.mode,
                      'reasons':[reason], 'data_unavailable':True, 'settings':cfg.model_dump(mode='json'),
                      'evaluated_at':datetime.now(UTC).isoformat()}
        self.record(signal, result)
        return result

    def record(self, signal, result):
        key = str(signal.source_chat_id) if signal.source_chat_id is not None else 'name:'+signal.source_name
        with self.store._connect() as conn:
            conn.execute('''INSERT INTO entry_decisions VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(scope,signal_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at''',
                (self.scope, signal.id, key, signal.source_name, signal.symbol, json.dumps(result), result['evaluated_at']))

    def reject(self, signal, decision, reason):
        """Keep a passed estimate honest when a later pre-entry check rejects it."""
        if decision and decision.get('mode') == 'enforce':
            decision.update(action='skip', approved_notional='0', quantity='0', estimated_margin='0')
            decision['reasons'] = list(dict.fromkeys([*decision.get('reasons', []), reason]))
            self.record(signal, decision)

    def recent(self, limit=100):
        with self.store._connect() as conn:
            rows = conn.execute('SELECT * FROM entry_decisions WHERE scope=? ORDER BY updated_at DESC LIMIT ?', (self.scope, limit)).fetchall()
        return [{**{k:r[k] for k in ('signal_id','source_key','source_name','symbol')}, **json.loads(r['payload'])} for r in rows]

    async def apply_preview(self, signal, preview, equity, *, notional_ceiling=None, settings=None):
        """Return a reduced, price-bounded IOC preview; never submit here."""
        cfg = settings or self.settings()
        if cfg.mode == 'off':
            return preview
        requested = D(preview['payload']['qty'])*D(preview['reference_price'])
        if notional_ceiling is not None:
            requested = min(requested, notional_ceiling)
        result = await self.assess(signal, notional=requested, equity=equity,
            leverage=preview['effective_leverage'], step=preview['quantity_step'],
            tick=preview['price_step'], min_qty=preview['min_quantity'], min_notional=preview['min_notional'],
            fee=preview['taker_fee_rate'], settings=cfg)
        preview['entry_guard'] = result
        if cfg.mode != 'enforce':
            return preview
        if result['action'] == 'skip':
            raise BitgetError('入场质量检查：'+'；'.join(result['reasons']))
        qty, limit_price = D(result['quantity']), D(result['limit_price'])
        preview['effective_leverage'] = result['effective_leverage']
        preview['estimated_margin'] = result['estimated_margin']
        preview['payload'].update(qty=str(qty), orderType='limit', price=str(limit_price), timeInForce='ioc')
        if signal.awaiting_protection:
            preview['payload']['stopLoss'] = str(temporary_stop(limit_price, qty, qty*limit_price/result['effective_leverage'],
                signal.side.value, preview['taker_fee_rate'], preview['price_step']))
        preview['estimated_opening_fee'] = str(D(result['approved_notional'])*D(preview['taker_fee_rate']))
        return preview

    @staticmethod
    def require_fresh(decision):
        if decision and decision['mode'] == 'enforce':
            age = int(time.time()*1000)-decision.get('book_ts', 0)
            if not -1000 <= age <= 3000:
                raise BitgetError('提交前盘口已过期，本次信号跳过，不改用无价格限制的市价单')
