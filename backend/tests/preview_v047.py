"""Explicit synthetic UI acceptance fixture. Isolated DB; exchange I/O blocked."""
import os
import sys
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal as D

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
DATA = Path(tempfile.mkdtemp(prefix='copier-v047-preview-'))
os.environ.update(COPIER_DATA_DIR=str(DATA), APP_DATABASE_PATH=str(DATA / 'preview.sqlite3'),
                  APP_SEED_DEMO_DATA='false', BITGET_API_ENVIRONMENT='live',
                  BITGET_API_KEY='', BITGET_API_SECRET='', BITGET_API_PASSPHRASE='',
                  TELEGRAM_API_ID='', TELEGRAM_API_HASH='', TELEGRAM_ALLOWED_CHAT_IDS='-100123')
from app.main import app, service
from app.bitget import BitgetError
from app.parser import parse_signal
from app.models import PaperSizingRequest, BitgetAccountSnapshot
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse


@asynccontextmanager
async def lifespan(_):
    async def blocked(*args, **kwargs):
        raise BitgetError('隔离合成预览：禁止连接交易所')
    service.bitget._request = blocked
    service.bitget.market_price = blocked
    service.telegram.channel_names[-100123] = '合成测试频道 · 非真实交易'
    service.paper.reset(D(10000), 25, D('.0006'), ['合成测试频道'], 'v0.4.7 隔离合成验收')
    service.paper.set_sizing(PaperSizingRequest(sizing_mode='fixed_usdt', fixed_usdt=100))
    now = datetime.now(UTC).isoformat()
    for symbol, side, mid in [('BTC', '多', 1), ('ETH', '空', 2)]:
        signal = parse_signal(f'#{symbol} 市价{side} 100\n止损：{90 if side == "多" else 110}\n止盈：{"120-130-140" if side == "多" else "80-70-60"}',
                              source_name='合成测试频道', chat_id=-100123, message_id=mid)
        signal.source_messages = [{'source_name': signal.source_name, 'text': signal.raw_text, 'sent_at': now, 'chat_id': -100123, 'message_id': mid}]
        service.store.upsert(signal)
        service.paper.enqueue(signal, leverage=25)
        service.paper.mark(signal.symbol, D(100))
        payload = {'signal': signal.model_dump(mode='json'), 'preview': {'effective_leverage': 25, 'take_profits': [str(t) for t in signal.take_profits], 'payload': {'stopLoss': str(signal.stop_loss)}},
                   'margin': '100', 'entry_price': '100', 'filled_qty': '25', 'remaining_qty': '25',
                   'tp_percentages': [40, 40, 20], 'realized_after_fees': '0', 'detail': '隔离合成预览 · 未连接交易所'}
        service.uta_runtime.engine.save(signal.id, signal.symbol, 'protected', payload)
        if side == '多':
            msg = {'chat_id': -100123, 'message_id': 3, 'source_name': '合成测试频道', 'text': '#BTC 稳健带成本损', 'sent_at': now}
            service.paper.manage(-100123, 3, f'paper_{signal.id}', ['breakeven'], D(110), message=msg)
            payload.update(current_stop='100.12', audit_context={'actor': 'signal', 'sources': [msg]})
            service.uta_runtime.engine.save(signal.id, signal.symbol, 'protected', payload)
        else:
            service.paper.close_positions({'ETHUSDT': D(95)}, f'paper_{signal.id}')
            payload.update(remaining_qty='0', detail='隔离合成预览：用户平仓已核对', audit_context={'actor': 'user', 'sources': []}, realized_after_fees='121.5')
            service.uta_runtime.engine.save(signal.id, signal.symbol, 'closed', payload)
    async def price(symbol): return D(110) if symbol == 'BTCUSDT' else D(95)
    service.public_price = price
    async def account():
        return BitgetAccountSnapshot(environment='live', account_equity_usdt='12543.21', unrealised_pnl_usd='123.45',
                                     updated_at=datetime.now(UTC), assets=[{'coin': 'USDT', 'available': '9840.12'}])
    service.bitget_account_snapshot = account
    service.uta_runtime.performance = lambda: {'points': [{'day': now[:10], 'daily': '121.5', 'cumulative': '121.5'}], 'realized_after_fees': '121.5', 'detail': '隔离合成预览收益，非实际账户收益。'}
    yield
    await service.bitget.close()


app.router.lifespan_context = lifespan
assets = ROOT / 'prototype' / 'dist' / 'client'
app.mount('/assets', StaticFiles(directory=assets / 'assets'))
@app.get('/')
async def index(): return FileResponse(assets / 'index.html')
if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=18770, access_log=False)
