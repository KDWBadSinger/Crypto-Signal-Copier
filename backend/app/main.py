from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

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
    PaperAccount,
    PaperAccountResetRequest,
    PaperAutoExecuteRequest,
    PaperStrategyRequest,
    ParsedSignal,
    ReviewModeRequest,
    SystemStatus,
    SymbolLeverageLimit,
    TelegramConnectionRequest,
)
from .paper import PaperTradingError
from .service import CopierService

settings = load_settings()
service = CopierService(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await service.start()
    yield
    await service.stop()


app = FastAPI(
    title="Mia Crypto Copier API",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4173", "http://127.0.0.1:4173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status", response_model=SystemStatus)
async def get_status() -> SystemStatus:
    return await service.status()


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


@app.get("/api/paper/account", response_model=PaperAccount)
async def paper_account(refresh: bool = Query(default=True)) -> PaperAccount:
    if refresh and service.paper.is_initialized():
        try:
            await service.refresh_paper_market()
        except BitgetError:
            # Return the last good marks so a temporary market-data error does not hide the ledger.
            pass
    return service.paper.snapshot()


@app.post("/api/paper/account/reset", response_model=PaperAccount)
async def reset_paper_account(request: PaperAccountResetRequest) -> PaperAccount:
    service.paper.reset(
        request.initial_balance,
        request.leverage,
        request.fee_rate,
        request.selected_sources,
    )
    return service.paper.snapshot()


@app.post("/api/paper/account/auto-execute", response_model=PaperAccount)
async def set_paper_auto_execute(request: PaperAutoExecuteRequest) -> PaperAccount:
    try:
        service.paper.set_auto_execute(request.enabled)
    except PaperTradingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return service.paper.snapshot()


@app.get("/api/paper/sources", response_model=list[str])
async def paper_sources() -> list[str]:
    return service.store.list_sources()


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
    except (PaperTradingError, BitgetError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return service.paper.snapshot()
