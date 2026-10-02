"""Screenshot regression preview. Isolated sample messages; no credentials/trades."""
import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import UTC,datetime,timedelta

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
os.environ.update(APP_DATABASE_PATH=str(ROOT/'desktop-test-data'/'parser-preview.sqlite3'),
    APP_SEED_DEMO_DATA='false',BITGET_API_KEY='',BITGET_API_SECRET='',BITGET_API_PASSPHRASE='',
    BITGET_API_ENVIRONMENT='live',TELEGRAM_API_ID='',TELEGRAM_API_HASH='',TELEGRAM_ALLOWED_CHAT_IDS='-100123')
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.main import app,service


@asynccontextmanager
async def lifespan(_):
    service.telegram.channel_names[-100123]='截图回归样例（隔离测试，非实时消息）'
    base=datetime.now(UTC)-timedelta(days=1)
    entries=[(7671,'#ONE 市價空 0.0031410',None),(7672,'#ONE 市價空 0.0031410',None),
      (7673,'止盈：0.0029687-0.0027859-0.0025235\n止損：0.0033155',7671),
      (7674,'止盈：0.0029687-0.0027859-0.0025235\n止損：0.0033155',7672),
      (7677,'#NIL 市價多 進場0.9085',None),(7679,'止盈：0.09474-0.10388\n止损：0.08687',7677),
      (7681,'#FORM 市價多 0.3161',None),(7682,'#FORM 市價多 0.3161',None),
      (7683,'止盈：0.332-0.355-0.382\n止損：0.305',7681),(7684,'止盈：0.332-0.355-0.382\n止損：0.305',7682)]
    for i,(identifier,text,parent) in enumerate(entries):
        service.store.record_message({'chat_id':-100123,'message_id':identifier,'source_name':'截图回归样例（非真实消息）',
            'text':text,'origin':'history','sent_at':(base+timedelta(seconds=i)).isoformat(),
            'reply_to_message_id':parent,'status':'unparsed' if parent else 'waiting'})
    await service.reparse_cached_messages()
    yield
    await service.bitget.close()

app.router.lifespan_context=lifespan
assets=ROOT/'prototype'/'dist'/'client'
app.mount('/assets',StaticFiles(directory=assets/'assets'))
@app.get('/')
async def index(): return FileResponse(assets/'index.html')
if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=18766,access_log=False)
