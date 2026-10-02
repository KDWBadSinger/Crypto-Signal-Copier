"""Isolated visual fixture for price precision and all TP legs."""
import os
import sys
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
os.environ.update(APP_DATABASE_PATH=str(ROOT/'desktop-test-data'/'paper-precision-preview.sqlite3'),
    APP_SEED_DEMO_DATA='false',BITGET_API_KEY='',BITGET_API_SECRET='',BITGET_API_PASSPHRASE='',
    TELEGRAM_API_ID='',TELEGRAM_API_HASH='',TELEGRAM_ALLOWED_CHAT_IDS='')

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.main import app,service
from app.parser import parse_signal


@asynccontextmanager
async def lifespan(_):
    if service.paper.is_initialized() and service.paper.snapshot().lifecycle!='stopped':
        service.paper.stop({'UAIUSDT':Decimal('.40142')})
    service.paper.reset(Decimal('1000'),20,Decimal('.0006'),['Mia市价单-山寨币'],'精度展示验收')
    signal=parse_signal('#UAI 市價多 0.3942\n止損：0.37747\n止盈：0.411-0.434-0.475',
                        source_name='Mia市价单-山寨币',chat_id=-1,message_id=1)
    service.store.upsert(signal)
    service.paper.enqueue(signal,leverage=20)
    service.paper.mark('UAIUSDT',Decimal('.39748'))
    service.paper.mark('UAIUSDT',Decimal('.40142'))
    yield
    await service.bitget.close()


app.router.lifespan_context=lifespan
assets=ROOT/'prototype'/'dist'/'client'
app.mount('/assets',StaticFiles(directory=assets/'assets'))
@app.get('/')
async def index(): return FileResponse(assets/'index.html')

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=18767,access_log=False)
