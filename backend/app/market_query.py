"""Public, read-only symbol lookup and timestamped 15-minute price history."""
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx

from .bitget import BitgetError


def normalize_symbol(value: str) -> str:
    symbol = value.strip().upper().replace('/', '').replace('-', '').replace('_', '')
    if not re.fullmatch(r'[A-Z0-9]{2,24}', symbol):
        raise ValueError('请输入币种代码，例如 BTC、UAI 或 BTC/USDT')
    symbol = symbol if symbol.endswith('USDT') else symbol + 'USDT'
    if len(symbol) <= 4:
        raise ValueError('请输入完整币种代码')
    return symbol


def price_value(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result <= 0:
            raise ValueError()
        return str(result)
    except (InvalidOperation, ValueError, TypeError):
        raise BitgetError('交易所返回无效行情价格，请稍后重试') from None


async def query_market(client, raw_symbol):
    symbol = normalize_symbol(raw_symbol)
    tickers = await client.market_tickers()
    ticker = tickers.get(symbol)
    if not ticker:
        raise LookupError(f'未找到 {symbol} 的 USDT 永续行情，请确认币种已在 Bitget 上线对应合约')
    try:
        response = await client._http.get('/api/v2/mix/market/candles', params={
            'symbol': symbol, 'productType': 'USDT-FUTURES', 'granularity': '15m', 'limit': '96',
        })
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or result.get('code') != '00000' or not isinstance(result.get('data'), list):
            raise BitgetError('暂时无法获取该币种价格曲线，请稍后重试')
        points = {}
        for row in result['data']:
            if not isinstance(row, list) or len(row) < 5:
                raise ValueError()
            timestamp = int(row[0])
            if timestamp <= 0:
                raise ValueError()
            points[timestamp] = {'timestamp': timestamp, 'close': price_value(row[4])}
        change = Decimal(str(ticker.get('change24h') or '0'))
        if not change.is_finite():
            raise ValueError()
    except (httpx.HTTPError, ValueError, TypeError, InvalidOperation):
        raise BitgetError('行情服务返回异常或连接失败，请稍后重试') from None
    return {
        'symbol': symbol, 'last_price': price_value(ticker.get('lastPr') or ticker.get('markPrice')),
        'change_24h': str(change), 'high_24h': price_value(ticker.get('high24h')),
        'low_24h': price_value(ticker.get('low24h')),
        'points': [points[t] for t in sorted(points)],
        'interval': '15m', 'updated_at': datetime.now(UTC).isoformat(),
    }
