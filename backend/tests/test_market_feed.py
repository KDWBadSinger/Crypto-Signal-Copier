import asyncio
import time
from decimal import Decimal

from app.market_feed import PublicMarketFeed


def test_valid_fresh_quotes_and_reject_stale_invalid_out_of_order():
    async def run():
        ticks = []
        async def tick(symbol, price): ticks.append((symbol, price))
        feed = PublicMarketFeed(tick, lambda: ['BTCUSDT'])
        feed.connected = True
        stamp = int(time.time()*1000)
        def payload(ts, price='100'):
            return {'arg': {'channel':'ticker','instType':'USDT-FUTURES','instId':'BTCUSDT'},
                    'data':[{'ts': str(ts),'lastPr':price,'markPrice':price}]}
        await feed.consume(payload(stamp))
        await feed.consume(payload(stamp-1))
        await feed.consume(payload(stamp-30000))
        await feed.consume(payload(stamp+1, 'NaN'))
        assert ticks == [('BTCUSDT', Decimal('100'))]
        assert feed.rejected_ticks == 2
        assert feed.quote('BTCUSDT')['mark'] == Decimal('100')
        feed.cache['BTCUSDT']['received'] -= 20
        assert feed.quote('BTCUSDT') is None
        assert feed.snapshot()['quotes']['BTCUSDT']['stale']
    asyncio.run(run())
