"""Local UI acceptance using real public prices, with isolated data and no account login."""
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'backend'))
os.environ.update(APP_DATABASE_PATH=str(ROOT/'desktop-test-data'/'market-search-preview.sqlite3'),
    APP_SEED_DEMO_DATA='false', BITGET_API_KEY='', BITGET_API_SECRET='', BITGET_API_PASSPHRASE='',
    TELEGRAM_API_ID='', TELEGRAM_API_HASH='', TELEGRAM_ALLOWED_CHAT_IDS='')
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.main import app, service

@asynccontextmanager
async def lifespan(_):
    yield
    await service.bitget.close()

app.router.lifespan_context = lifespan
assets = ROOT/'prototype'/'dist'/'client'
app.mount('/assets', StaticFiles(directory=assets/'assets'))
@app.get('/')
async def index():
    return FileResponse(assets/'index.html')

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=18768, access_log=False)
