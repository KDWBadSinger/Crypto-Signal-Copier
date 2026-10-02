"""Read-only real network check; no credentials or orders."""
import asyncio
from app.market_feed import PublicMarketFeed


async def main():
    samples = []
    async def tick(symbol, price):
        samples.append((symbol, str(price)))
    feed = PublicMarketFeed(tick, lambda: ['BTCUSDT', 'ETHUSDT'])
    await feed.start()
    try:
        for _ in range(30):
            if len(samples) >= 4:
                break
            await asyncio.sleep(1)
        print({'samples': len(samples), 'feed': feed.snapshot()})
        if len(samples) < 4:
            raise RuntimeError('No verified public stream; check network')
    finally:
        await feed.stop()


if __name__ == '__main__':
    asyncio.run(main())
