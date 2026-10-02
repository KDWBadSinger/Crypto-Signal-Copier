"""Isolated visual acceptance server. No personal keys, no exchange orders."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from datetime import timedelta
from decimal import Decimal
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.update(APP_DATABASE_PATH=str(ROOT / "desktop-test-data" / "inbox-preview.sqlite3"),
                  APP_SEED_DEMO_DATA="false", TELEGRAM_API_ID="", TELEGRAM_API_HASH="",
                  TELEGRAM_ALLOWED_CHAT_IDS="", BITGET_API_KEY="", BITGET_API_SECRET="",
                  BITGET_API_PASSPHRASE="", BITGET_ENABLE_DEMO_ORDERS="false", BITGET_API_ENVIRONMENT="live")

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.main import app, service

class PreviewTelegram:
    def is_connected(self): return True
    async def send_read_acknowledge(self, *args): pass
    async def iter_dialogs(self):
        yield SimpleNamespace(id=-100123, name="验收样例频道（非真实消息）", is_channel=True, is_group=False)
    async def iter_messages(self, chat_id, limit):
        yield SimpleNamespace(id=7608, message="止盈：0.479-0.511-0.551\n止损：0.425", reply_to_msg_id=7606, date=datetime.now(UTC))
        yield SimpleNamespace(id=7606, message="#UAI 市價多 0.45091", date=datetime.now(UTC))
        for identifier, text in enumerate(["BTCUSDT LONG\nEntry: 80000 - 80100\nSL: 79000\nTP1: 82000\nTP2: 84000", "今日市场波动较大，注意交易风险。", "ETHUSDT SHORT\nEntry: 2600\nTP1: 2500"], start=1):
            yield SimpleNamespace(id=identifier, message=text, date=datetime.now(UTC))

@asynccontextmanager
async def preview_lifespan(_):
    service.set_manual_review(True)
    service.settings = replace(service.settings, telegram_allowed_chat_ids=frozenset({-100123}))
    service.telegram.settings = service.settings
    service.telegram.client = PreviewTelegram()
    service.telegram.connected = True
    service.telegram.detail = "隔离验收环境，仅显示样例消息"
    await service.telegram.sync_history(-100123)
    # Clearly labelled chart fixtures, isolated from the user's desktop data.
    if not service.paper.is_initialized():
        import app.paper as ledger
        from app.parser import parse_signal
        real_datetime = ledger.datetime
        class PreviewClock(datetime):
            value = datetime.now(UTC) - timedelta(days=7)
            @classmethod
            def now(cls, tz=None): return cls.value
        ledger.datetime = PreviewClock
        try:
            service.paper.reset(Decimal('10000'), 10, Decimal('0.0006'), [], '图表验收样例-非真实收益')
            service.paper.enqueue(parse_signal('BTCUSDT LONG\nEntry: 100\nSL: 70\nTP1: 150', source_name='验收样例'))
            for index, price in enumerate(['100','105','103','108','102','112','109','115']):
                PreviewClock.value = datetime.now(UTC) - timedelta(days=7-index)
                service.paper.mark('BTCUSDT', Decimal(price))
                service.paper.heartbeat(market_ok=True)
        finally:
            ledger.datetime = real_datetime
    async def preview_price(symbol): return Decimal('115')
    service.bitget.market_price = preview_price
    service.paper.runtime_start()
    await service.market_feed.start()
    yield
    await service.market_feed.stop()
    await service.bitget.close()

app.router.lifespan_context = preview_lifespan
assets = ROOT / "prototype" / "dist" / "client"
app.mount("/assets", StaticFiles(directory=assets / "assets"))

@app.get("/")
async def index(): return FileResponse(assets / "index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=18765, access_log=False)
