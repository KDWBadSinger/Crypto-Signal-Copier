"""Isolated synthetic UI acceptance. No credentials, Telegram, or exchange I/O."""
import asyncio
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal as D

import preview_v047 as base
from app.entry_guard import EntryGuardSettings
from app.models import PaperSizingRequest
from app.parser import parse_signal

app,service=base.app,base.service


@asynccontextmanager
async def lifespan(_):
    async with base.lifespan(_):
        service.telegram.channel_names[-100123]='合成样本 · 非真实博主'
        # Do not let a page refresh or future API call reach exchange transport.
        async def blocked(*args,**kwargs):
            from app.bitget import BitgetError
            raise BitgetError('隔离合成预览：不连接交易所')
        service.bitget._http.get=blocked
        service.paper.set_sizing(PaperSizingRequest(sizing_mode='fixed_usdt',fixed_usdt=200))
        async def limits(symbol): return 1,50
        async def contract(symbol): return {'sizeMultiplier':'.001','priceEndStep':'1','pricePlace':2,'minTradeNum':'.001','minTradeUSDT':'1'}
        async def price(symbol): return D(100)
        async def market(symbol):
            now=int(time.time()*1000); minute=now//60000*60000
            p=D(107) if symbol=='XRPUSDT' else D(100)
            depth='300' if symbol=='SOLUSDT' else '100000'
            return {'a':[[str(p+D('.01')),depth],[str(p+D('.1')),depth]],'b':[[str(p-D('.01')),depth],[str(p-D('.1')),depth]],'ts':str(now)}, [
                [str(minute-i*60000),str(p),str(p+1),str(p-1),str(p),'10000','10000000'] for i in range(12)]
        service.public_price=price;service.bitget.symbol_leverage_limits=limits;service.bitget.contract_config=contract
        service.paper_guard.market=market;service.uta_runtime.entry_guard.market=market
        for guard in [service.paper_guard,service.uta_runtime.entry_guard]:
            guard.configure(EntryGuardSettings())
        for i,symbol in enumerate(['ADA','SOL','XRP'],10):
            signal=parse_signal(f'#{symbol} 市价多 100\n止损：97\n止盈：110-120-130',source_name='合成样本 · 非真实博主',chat_id=-100123,message_id=i)
            signal.source_messages=[{'source_name':signal.source_name,'chat_id':-100123,'message_id':i,'sent_at':datetime.now(UTC).isoformat(),'text':signal.raw_text}]
            service.store.upsert(signal)
            await service.paper_execute(signal)
            await service.uta_runtime.entry_guard.assess(signal,notional=D(5000),equity=D(10000),leverage=25,step=D('.001'),tick=D('.01'),min_qty=D('.001'),min_notional=D(1),fee=D('.0006'))
        yield


app.router.lifespan_context=lifespan
if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=18771,access_log=False)
