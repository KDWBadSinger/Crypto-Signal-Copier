from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, SecretStr
from telethon.errors import RPCError, FloodWaitError

from .bitget import BitgetError
from .config import load_settings
from .models import (
    ApproveSignalRequest,
    AutoExecutionSettings,
    AutoExecutionSettingsRequest,
    BitgetConnectionRequest,
    BitgetAccountSnapshot,
    ConnectionOverview,
    LeverageOverride,
    LeverageOverrides,
    MarketOverview,
    PaperAccount,
    PaperAccountResetRequest,
    PaperAutoExecuteRequest,
    PaperStrategyRequest,
    ParsedSignal,
    ReviewModeRequest,
    SystemStatus,
    SymbolLeverageLimit,
    TelegramConnectionRequest,
    TelegramLoginRequest,
    TelegramChannelsRequest,
)
from .paper import PaperTradingError
from .models import PaperSizingRequest
from .service import CopierService
from .release import VERSION, database_export
from .uta import UtaReadiness

settings = load_settings()
service = CopierService(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await service.start()
    try:
        yield
    finally:
        await service.stop()


app = FastAPI(
    title="Mia Crypto Copier API",
    version=VERSION,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4173", "http://127.0.0.1:4173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get('/api/uta/readiness')
async def uta_readiness():
    try:
        return await UtaReadiness(service.bitget).check()
    except BitgetError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.exception_handler(RequestValidationError)
async def invalid_request(_, exc):
    # Pydantic's default errors can include plaintext credential input.
    errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.get('/api/system/diagnostics')
async def diagnostics():
    import sqlite3
    from .database import ManagedConnection
    with sqlite3.connect(service.settings.database_path, factory=ManagedConnection) as connection:
        integrity = connection.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    telegram, _ = service.telegram.state()
    paper = service.paper.snapshot()
    def task_state(task):
        return 'not_started' if task is None else 'stopped' if task.done() else 'running'
    return {'version': VERSION, 'database_ok': integrity, 'telegram_connected': telegram,
            'selected_channel_count': len(service.settings.telegram_allowed_chat_ids),
            'market': {'connected': service.market_feed.connected, 'detail': service.market_feed.detail,
                       'reconnects': service.market_feed.reconnects, 'rejected_ticks': service.market_feed.rejected_ticks},
            'tasks': {'market': task_state(service.market_feed.task), 'paper': task_state(service._paper_monitor_task)},
            'paper_lifecycle': paper.lifecycle, 'real_money_execution': False,
            'commercial_status': 'release_candidate_requires_live_acceptance'}


class AccountLogin(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: SecretStr = Field(min_length=12, max_length=128)
    register_account: bool = Field(default=False, alias='register')


@app.get('/api/account/license')
async def account_license():
    return await service.license.status()


@app.post('/api/account/login')
async def account_login(body: AccountLogin):
    try:
        return await service.license.login(body.username, body.password.get_secret_value(), body.register_account)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post('/api/account/logout')
async def account_logout():
    try:
        await service.license.logout()
    except ValueError:
        pass
    return await service.license.status()


@app.get('/api/system/backup')
async def export_backup():
    return Response(database_export(service.settings.database_path), media_type='application/octet-stream',
                    headers={'Content-Disposition': 'attachment; filename="copier-ledger-backup.sqlite3"',
                             'Cache-Control': 'no-store'})


async def telegram_call(awaitable):
    try:
        return await asyncio.wait_for(awaitable, timeout=45)
    except FloodWaitError as exc:
        raise HTTPException(429, f"Telegram 请求频繁，请等待 {exc.seconds} 秒后重试") from exc
    except RPCError as exc:
        raise HTTPException(409, f"Telegram 请求失败：{type(exc).__name__}，请检查验证码、密码或 API 配置") from exc
    except (ValueError, OSError, TimeoutError) as exc:
        detail = str(exc) if isinstance(exc, ValueError) else "Telegram 连接超时或网络不可用，请重试"
        raise HTTPException(409, detail) from exc


@app.post("/api/telegram/send-code")
async def telegram_send_code():
    return await telegram_call(service.telegram.send_code())


@app.post("/api/telegram/sign-in")
async def telegram_sign_in(request: TelegramLoginRequest):
    return await telegram_call(service.telegram.sign_in(
        request.code.get_secret_value() if request.code else None,
        request.password.get_secret_value() if request.password else None,
    ))


@app.get("/api/telegram/channels")
async def telegram_channels():
    return await telegram_call(service.telegram_channels())


@app.post("/api/telegram/channels")
async def telegram_select_channels(request: TelegramChannelsRequest):
    return await telegram_call(service.select_telegram_channels(request.chat_ids))


@app.get("/api/telegram/messages")
async def telegram_messages(limit: int = Query(default=50, ge=1, le=100)):
    return service.store.messages(limit)


@app.get("/api/telegram/inbox")
async def telegram_inbox(chat_id: int | None = None, limit: int = Query(default=100, ge=1, le=200)):
    return service.telegram_inbox(chat_id, limit)


@app.post("/api/telegram/channels/{chat_id}/history")
async def telegram_history(chat_id: int, limit: int = Query(default=30, ge=1, le=100)):
    count = await telegram_call(service.telegram.sync_history(chat_id, limit))
    return {"synced": count, "inbox": service.telegram_inbox(chat_id)}


@app.get("/api/telegram/media/{chat_id}/{message_id}")
async def telegram_photo(chat_id: int, message_id: int):
    data = await telegram_call(service.telegram.photo(chat_id, message_id))
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@app.websocket("/api/telegram/stream")
async def telegram_stream(websocket: WebSocket, chat_id: int | None = None) -> None:
    await websocket.accept()
    queue = asyncio.Queue(maxsize=1)
    service._message_subscribers.add(queue)
    try:
        while True:
            await websocket.send_json(service.telegram_inbox(chat_id))
            try:
                await asyncio.wait_for(queue.get(), timeout=10)
            except TimeoutError:
                pass
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass
    finally:
        service._message_subscribers.discard(queue)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status", response_model=SystemStatus)
async def get_status() -> SystemStatus:
    return await service.status()


@app.get("/api/market/overview", response_model=MarketOverview)
async def get_market_overview() -> MarketOverview:
    try:
        return await service.market_overview()
    except BitgetError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/connections", response_model=ConnectionOverview)
async def get_connections() -> ConnectionOverview:
    return await service.connection_overview()


@app.post("/api/connections/bitget", response_model=ConnectionOverview)
async def configure_bitget(request: BitgetConnectionRequest) -> ConnectionOverview:
    try:
        return await service.configure_bitget(request)
    except (ValueError, BitgetError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/connections/telegram", response_model=ConnectionOverview)
async def configure_telegram(request: TelegramConnectionRequest) -> ConnectionOverview:
    try:
        return await service.configure_telegram(request)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/bitget/account", response_model=BitgetAccountSnapshot)
async def get_bitget_account() -> BitgetAccountSnapshot:
    try:
        return await service.bitget_account_snapshot()
    except BitgetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.websocket("/api/bitget/account/stream")
async def stream_bitget_account(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = service.subscribe_bitget_account()
    try:
        snapshot = await service.bitget_account_snapshot()
        await websocket.send_text(snapshot.model_dump_json())
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=20)
                await websocket.send_text(payload)
            except TimeoutError:
                # A lightweight application heartbeat also discovers closed browser
                # sockets when the Bitget account itself has no events to publish.
                await websocket.send_json({"type": "heartbeat"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    except BitgetError as exc:
        await websocket.send_json({"error": str(exc)})
    finally:
        service.unsubscribe_bitget_account(queue)


@app.post("/api/settings/manual-review", response_model=SystemStatus)
async def set_manual_review(request: ReviewModeRequest) -> SystemStatus:
    service.set_manual_review(request.enabled)
    return await service.status()


@app.get("/api/settings/auto-execution", response_model=AutoExecutionSettings)
async def get_auto_execution_settings() -> AutoExecutionSettings:
    return service.auto_execution_settings()


@app.post("/api/settings/auto-execution", response_model=AutoExecutionSettings)
async def set_auto_execution_settings(
    request: AutoExecutionSettingsRequest,
) -> AutoExecutionSettings:
    return service.set_auto_execution_settings(request)


@app.get("/api/settings/leverage-overrides", response_model=LeverageOverrides)
async def get_leverage_overrides() -> LeverageOverrides:
    return service.leverage_overrides()


@app.post("/api/settings/leverage-overrides", response_model=LeverageOverrides)
async def set_leverage_overrides(request: LeverageOverrides) -> LeverageOverrides:
    return service.set_leverage_overrides(request)


@app.get("/api/bitget/contracts/{symbol}/leverage", response_model=SymbolLeverageLimit)
async def get_symbol_leverage_limit(symbol: str) -> SymbolLeverageLimit:
    try:
        normalized = LeverageOverride(symbol=symbol, leverage=1).symbol
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="请输入正确的 USDT 合约交易对") from exc
    try:
        minimum, maximum = await service.bitget.symbol_leverage_limits(normalized)
    except BitgetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SymbolLeverageLimit(
        symbol=normalized,
        min_leverage=minimum,
        max_leverage=maximum,
    )


@app.get("/api/signals", response_model=list[ParsedSignal])
async def list_signals(limit: int = Query(default=20, ge=1, le=100)):
    return service.store.list(limit)


@app.get("/api/signals/latest", response_model=ParsedSignal)
async def latest_signal():
    signal = service.store.latest()
    if signal is None:
        raise HTTPException(status_code=404, detail="No signals available")
    return signal


@app.get("/api/signals/{signal_id}/audit")
async def signal_audit(signal_id: str):
    if service.store.get(signal_id) is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    return service.store.audit_for(signal_id)


@app.get("/api/signals/{signal_id}", response_model=ParsedSignal)
async def get_signal(signal_id: str):
    signal = service.store.get(signal_id)
    if signal is None:
        raise HTTPException(404, "Signal not found")
    return signal


@app.post("/api/signals/{signal_id}/approve", response_model=ParsedSignal)
async def approve_signal(signal_id: str, request: ApproveSignalRequest):
    signal = service.store.get(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    try:
        return await service.approve(signal, request)
    except (ValueError, BitgetError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/signals/{signal_id}/ignore", response_model=ParsedSignal)
async def ignore_signal(signal_id: str):
    signal = service.store.get(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    return service.ignore(signal)


@app.get('/api/account/performance')
async def account_performance():
    return service.account_curve.report(service.settings)


@app.get("/api/paper/account", response_model=PaperAccount)
async def paper_account(refresh: bool = Query(default=True)) -> PaperAccount:
    if refresh and service.paper.is_initialized():
        try:
            await service.refresh_paper_market()
        except (BitgetError, OSError, PaperTradingError):
            # Return the last good marks so a temporary market-data error does not hide the ledger.
            pass
    return service.paper.snapshot()


@app.post("/api/paper/account/reset", response_model=PaperAccount)
async def reset_paper_account(request: PaperAccountResetRequest) -> PaperAccount:
    try:
        async with service._paper_lock:
            service.paper.heartbeat()
            service.paper.reset(request.initial_balance, request.leverage, request.fee_rate,
                                request.selected_sources, request.simulation_id)
            service.paper.set_sizing(request)
    except PaperTradingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return service.paper.snapshot()


@app.get('/api/market/feed')
async def public_market_feed():
    return service.market_feed.snapshot()


@app.websocket('/api/market/stream')
async def public_market_stream(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(service.market_feed.snapshot())
            await asyncio.sleep(1)
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass


@app.post('/api/paper/account/sizing', response_model=PaperAccount)
async def set_paper_sizing(request: PaperSizingRequest):
    try:
        async with service._paper_lock:
            service.paper.set_sizing(request)
        return service.paper.snapshot()
    except PaperTradingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/paper/account/stop", response_model=PaperAccount)
async def stop_paper_account() -> PaperAccount:
    try:
        return await service.stop_paper()
    except (PaperTradingError, BitgetError, OSError) as exc:
        raise HTTPException(status_code=409, detail="结算未完成，请检查公开行情连接后重试：" + str(exc)) from exc


@app.get("/api/paper/report")
async def paper_report(simulation_id: str | None = None) -> dict:
    try:
        return service.paper.report(simulation_id)
    except PaperTradingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/paper/reports")
async def paper_reports() -> list[dict]:
    return service.paper.reports()


@app.post("/api/paper/account/auto-execute", response_model=PaperAccount)
async def set_paper_auto_execute(request: PaperAutoExecuteRequest) -> PaperAccount:
    if not request.enabled:
        return await stop_paper_account()
    try:
        service.paper.set_auto_execute(request.enabled)
    except PaperTradingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return service.paper.snapshot()


@app.get("/api/paper/sources", response_model=list[str])
async def paper_sources() -> list[str]:
    return sorted(set(service.store.list_sources()) | {channel['title'] for channel in service.selected_telegram_channels()})


@app.post("/api/paper/account/strategy", response_model=PaperAccount)
async def set_paper_strategy(request: PaperStrategyRequest) -> PaperAccount:
    try:
        service.paper.set_strategy(request.selected_sources)
    except PaperTradingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return service.paper.snapshot()


@app.post("/api/paper/signals/{signal_id}/execute", response_model=PaperAccount)
async def execute_signal_in_paper_account(signal_id: str) -> PaperAccount:
    signal = service.store.get(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    try:
        await service.paper_execute(signal)
    except (PaperTradingError, BitgetError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return service.paper.snapshot()
