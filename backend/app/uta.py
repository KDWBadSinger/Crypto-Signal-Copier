"""UTA V3 validation/preview. No live write path is exposed by this module.

Do not bypass this boundary until exchange protection and recovery are accepted.
"""
import hashlib
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP

from .bitget import BitgetError
from .follow_policy import temporary_stop, exchange_leverage


def number(value, name, *, positive=True):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise BitgetError(f'UTA {name} 缺失或格式无效') from None
    if not result.is_finite() or (positive and result <= 0):
        raise BitgetError(f'UTA {name} 无效')
    return result


def build_order_preview(signal, instrument, *, margin, leverage, market_price, hold_mode, account_scope, default_max_percent=50, bounded_entry=False):
    """Build, but never submit, a V3 market order with exchange-side protection."""
    if instrument.get('symbol') != signal.symbol or instrument.get('category') != 'USDT-FUTURES' or instrument.get('status') != 'online':
        raise BitgetError('UTA 合约不匹配、不是 USDT 永续或不可交易')
    if instrument.get('type') != 'perpetual':
        raise BitgetError('当前 UTA 跟单仅设计用于 USDT 永续合约')
    if hold_mode not in {'one_way_mode', 'hedge_mode'}:
        raise BitgetError('无法识别 UTA 持仓模式')
    price, margin = number(market_price,'行情价格'), number(margin,'保证金')
    effective = Decimal(exchange_leverage(instrument.get('maxLeverage'),instrument.get('minLeverage'),leverage,default_max_percent))
    if effective != effective.to_integral_value() or effective < number(instrument.get('minLeverage'),'最小杠杆'):
        raise BitgetError('UTA 杠杆超出支持范围')
    deviation=Decimal('.10') if signal.entry_correction else Decimal('.02')
    if not bounded_entry and abs(price/signal.reference_entry-1) > deviation:
        raise BitgetError(f'当前价格偏离校验参考价超过 {deviation*100}%，不追单')
    step = number(instrument.get('quantityMultiplier'),'数量步长')
    tick = number(instrument.get('priceMultiplier'),'价格步长')
    qty = (margin*effective/price/step).to_integral_value(rounding=ROUND_DOWN)*step
    if bounded_entry:
        # Respect the smaller market-size cap even though the final order is IOC.
        maximum=number(instrument.get('maxMarketOrderQty'),'最大市价数量')
        qty=min(qty,(maximum/step).to_integral_value(rounding=ROUND_DOWN)*step)
    if qty < number(instrument.get('minOrderQty'),'最小数量') or qty*price < number(instrument.get('minOrderAmount'),'最小名义金额'):
        raise BitgetError('保证金低于 UTA 合约最小下单要求')
    if qty > number(instrument.get('maxMarketOrderQty'),'最大市价数量'):
        raise BitgetError('数量超过 UTA 市价单限制，不自动拆单')
    fee = number(instrument.get('takerFeeRate'),'taker 手续费',positive=False)
    if fee < 0: raise BitgetError('手续费无效')
    stop = (temporary_stop(price,qty,qty*price/effective,signal.side.value,fee,tick) if signal.awaiting_protection
            else (signal.stop_loss/tick).to_integral_value(rounding=ROUND_HALF_UP)*tick)
    targets = [(p/tick).to_integral_value(rounding=ROUND_HALF_UP)*tick for p in signal.take_profits]
    if not signal.awaiting_protection:
        type(signal).model_validate({**signal.model_dump(), 'entry_low':price,'entry_high':price,'stop_loss':stop,'take_profits':targets})
    identity = hashlib.sha256((account_scope+'|'+signal.id+'|entry').encode()).hexdigest()[:27]
    payload = {'category':'USDT-FUTURES','symbol':signal.symbol,'qty':format(qty,'f'),
               'side':'buy' if signal.side == 'long' else 'sell','orderType':'market',
               'clientOid':'uta_'+identity,'marginMode':'crossed','reduceOnly':'no',
               'stopLoss':format(stop,'f'),'slTriggerBy':'mark','slOrderType':'market'}
    if hold_mode == 'hedge_mode':
        payload['posSide'] = signal.side.value
    # A single preset TP closes the whole protected quantity. Never pretend this
    # is a multi-target bracket. Multiple targets need separate verified plans.
    if len(targets) == 1:
        payload.update(takeProfit=format(targets[0],'f'),tpTriggerBy='mark',tpOrderType='market')
    fee = number(instrument.get('takerFeeRate'),'taker 手续费',positive=False)
    if fee < 0:
        raise BitgetError('无法估计 UTA 开仓手续费')
    return {'payload':payload, 'effective_leverage':int(effective), 'estimated_margin':str(qty*price/effective),
            'quantity_step':str(step), 'min_quantity':str(number(instrument.get('minOrderQty'),'最小数量')),
            'price_step':str(tick),'taker_fee_rate':str(fee),
            'min_notional':str(number(instrument.get('minOrderAmount'),'最小名义金额')), 'reference_price':str(price),
            'estimated_opening_fee':str(qty*price*fee),'take_profits':[str(t) for t in targets],
            'leverage_source':f'exchange_max_{default_max_percent}_percent' if leverage is None else 'symbol_override',
            'exchange_max_leverage':str(instrument['maxLeverage']),
            'multi_target_protection_required':len(targets)>1,
            'execution_enabled':False, 'notice':'仅生成参数预览，不修改杠杆、不下单；多档保护及成交归因尚待验收'}


class UtaReadiness:
    def __init__(self, client, *, bounded_entry=False):
        self.client = client
        self.bounded_entry = bounded_entry

    async def check(self):
        cfg = self.client.settings
        if not cfg.bitget_configured:
            return {'read_access':False,'execution_enabled':False,'blockers':['请在本机连接管理填写 UTA API，勿发送到聊天'], 'checks':[]}
        settings = (await self.client._request('GET','/api/v3/account/settings')).get('data')
        info = (await self.client._request('GET','/api/v3/account/info')).get('data')
        if not isinstance(settings,dict) or not isinstance(info,dict):
            raise BitgetError('UTA 返回无效账户结构')
        permissions = set(info.get('permissions') or [])
        checks = [
            {'name':'真实账户环境','ok':not cfg.bitget_is_demo},
            {'name':'UTA 统一账户状态','ok':settings.get('accountMode') in {'unified','hybrid'}},
            {'name':'单向/双向持仓模式可识别','ok':settings.get('holdMode') in {'one_way_mode','hedge_mode'}},
            {'name':'API 可交易及调整杠杆','ok':info.get('permType') == 'read-and-write' and {'uta_trade','uta_mgt'} <= permissions},
            {'name':'未授予提现权限','ok':'withdraw' not in permissions},
        ]
        return {'read_access':True,'account_mode':settings.get('accountMode'), 'hold_mode':settings.get('holdMode'),
                'environment':cfg.bitget_api_environment,'checks':checks,'execution_enabled':False,
                'blockers':[c['name']+'：未满足' for c in checks if not c['ok']]}

    async def preview(self, signal, *, margin, leverage, default_max_percent=50):
        cfg = self.client.settings
        if not cfg.bitget_configured:
            raise BitgetError('请先连接 UTA API')
        settings = (await self.client._request('GET','/api/v3/account/settings')).get('data') or {}
        if settings.get('accountMode') not in {'unified','hybrid'}:
            raise BitgetError('账户不是可用 UTA 状态')
        # Public metadata has no reason to carry the user's API signature.
        response = await self.client._http.get('/api/v3/market/instruments',params={'category':'USDT-FUTURES','symbol':signal.symbol})
        response.raise_for_status()
        result = response.json()
        if result.get('code') != '00000' or not isinstance(result.get('data'),list):
            raise BitgetError('无法读取 UTA 合约规格')
        instruments = [i for i in result['data'] if i.get('symbol') == signal.symbol]
        if len(instruments) != 1:
            raise BitgetError('无法唯一定位 UTA 合约')
        price = await self.client.market_price(signal.symbol)
        return build_order_preview(signal,instruments[0],margin=margin,leverage=leverage,market_price=price,
                                   hold_mode=settings.get('holdMode'),account_scope=cfg.bitget_api_environment+'|'+cfg.bitget_api_key,default_max_percent=default_max_percent,bounded_entry=self.bounded_entry)
