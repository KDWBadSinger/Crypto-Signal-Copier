"""Isolated, explicitly synthetic UI fixture. No Telegram or exchange orders."""
import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager
from decimal import Decimal as D
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
os.environ.update(APP_DATABASE_PATH=str(ROOT/'desktop-test-data'/'manual-close-preview.sqlite3'),
 APP_SEED_DEMO_DATA='false', BITGET_API_KEY='',BITGET_API_SECRET='',BITGET_API_PASSPHRASE='',
 TELEGRAM_API_ID='',TELEGRAM_API_HASH='',TELEGRAM_ALLOWED_CHAT_IDS='')
from app.main import app,service
from app.parser import parse_signal
from app.models import PaperSizingRequest
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

@asynccontextmanager
async def lifespan(_):
    if not service.paper.is_initialized():
        service.paper.reset(D(1000),10,D('.0006'),simulation_id='隔离平仓界面测试')
        service.paper.set_sizing(PaperSizingRequest(sizing_mode='fixed_usdt',fixed_usdt=10,leverage=10))
        for symbol,mid in [('BTC',1),('ETH',2)]:
            signal=parse_signal(f'#{symbol} 市价多 100\n止损：90\n止盈：120-130-140',source_name='合成测试信号',chat_id=-1,message_id=mid)
            service.paper.enqueue(signal);service.paper.mark(symbol+'USDT',D(100))
    async def price(symbol): return D(110)
    service.public_price=price
    yield
    await service.bitget.close()
app.router.lifespan_context=lifespan
assets=ROOT/'prototype'/'dist'/'client'
app.mount('/assets',StaticFiles(directory=assets/'assets'))
@app.get('/')
async def index(): return FileResponse(assets/'index.html')
if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=18769,access_log=False)
