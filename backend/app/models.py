from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


class SignalSide(StrEnum):
    LONG = "long"
    SHORT = "short"


class SignalStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    APPROVED_DRY_RUN = "approved_dry_run"
    SUBMITTED = "submitted"
    IGNORED = "ignored"
    REJECTED = "rejected"


class ExecutionMethod(StrEnum):
    LIMIT = "limit"
    MARKET = "market"


class SizingMode(StrEnum):
    FIXED_USDT = "fixed_usdt"
    POSITION_PERCENT = "position_percent"


class ParsedSignal(BaseModel):
    id: str
    source_chat_id: int | None = None
    source_name: str
    source_message_id: int | None = None
    raw_text: str
    symbol: str
    side: SignalSide
    entry_low: Decimal
    entry_high: Decimal
    stop_loss: Decimal
    take_profits: list[Decimal] = Field(min_length=1)
    risk_percent: Decimal | None = None
    confidence: float = Field(ge=0, le=1)
    status: SignalStatus = SignalStatus.PENDING_REVIEW
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    bitget_order_id: str | None = None
    client_oid: str | None = None

    @property
    def reference_entry(self) -> Decimal:
        return (self.entry_low + self.entry_high) / Decimal("2")

    @model_validator(mode="after")
    def validate_trade_geometry(self) -> "ParsedSignal":
        if self.entry_low <= 0 or self.entry_high <= 0:
            raise ValueError("entry prices must be positive")
        if self.entry_low > self.entry_high:
            raise ValueError("entry_low cannot exceed entry_high")
        if self.side == SignalSide.LONG:
            if self.stop_loss >= self.entry_low:
                raise ValueError("long stop loss must be below the entry range")
            if any(tp <= self.entry_high for tp in self.take_profits):
                raise ValueError("long take profits must be above the entry range")
        else:
            if self.stop_loss <= self.entry_high:
                raise ValueError("short stop loss must be above the entry range")
            if any(tp >= self.entry_low for tp in self.take_profits):
                raise ValueError("short take profits must be below the entry range")
        return self


class ApproveSignalRequest(BaseModel):
    size: Decimal = Field(gt=0)
    execution_method: ExecutionMethod = ExecutionMethod.LIMIT
    entry_price: Decimal | None = Field(default=None, gt=0)
    stop_loss: Decimal | None = Field(default=None, gt=0)
    take_profit: Decimal | None = Field(default=None, gt=0)
    confirm_demo_order: bool = False


class ConnectionState(BaseModel):
    configured: bool
    connected: bool
    detail: str


class SystemStatus(BaseModel):
    telegram: ConnectionState
    bitget: ConnectionState
    demo_order_execution_enabled: bool
    manual_review_enabled: bool
    auto_order_size: Decimal
    environment: str
    bitget_environment: str


class AutoExecutionSettings(BaseModel):
    enabled: bool = False
    execution_method: ExecutionMethod = ExecutionMethod.MARKET
    sizing_mode: SizingMode = SizingMode.FIXED_USDT
    fixed_usdt: Decimal = Field(default=Decimal("200"), gt=0)
    position_percent: Decimal = Field(default=Decimal("5"), ge=0, le=10)
    default_leverage: int = Field(default=10, ge=1, le=150)

    @model_validator(mode="after")
    def validate_enabled_size(self) -> "AutoExecutionSettings":
        if self.enabled and self.sizing_mode == SizingMode.POSITION_PERCENT and self.position_percent <= 0:
            raise ValueError("position_percent must be greater than 0 when automatic execution is enabled")
        return self


class AutoExecutionSettingsRequest(AutoExecutionSettings):
    pass


class LeverageOverride(BaseModel):
    symbol: str = Field(min_length=5, max_length=32)
    leverage: int = Field(ge=1, le=150)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        symbol = value.strip().upper().replace("/", "").replace("-", "")
        if not symbol.endswith("USDT") or not symbol[:-4].isalnum():
            raise ValueError("symbol must be a USDT futures symbol such as BTCUSDT")
        return symbol


class LeverageOverrides(BaseModel):
    items: list[LeverageOverride] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_unique_symbols(self) -> "LeverageOverrides":
        symbols = [item.symbol for item in self.items]
        if len(symbols) != len(set(symbols)):
            raise ValueError("leverage override symbols must be unique")
        return self


class SymbolLeverageLimit(BaseModel):
    symbol: str
    min_leverage: int
    max_leverage: int


class BitgetConnectionRequest(BaseModel):
    api_key: SecretStr | None = None
    api_secret: SecretStr | None = None
    passphrase: SecretStr | None = None
    environment: str = Field(pattern="^(demo|live)$")


class TelegramConnectionRequest(BaseModel):
    api_id: int = Field(gt=0)
    api_hash: SecretStr | None = None
    phone: str | None = Field(default=None, max_length=32)
    allowed_chat_ids: list[int] = Field(default_factory=list)


class ConnectionOverview(BaseModel):
    bitget: ConnectionState
    bitget_environment: str
    bitget_api_key_hint: str | None = None
    telegram: ConnectionState
    telegram_api_id: int | None = None
    telegram_phone_hint: str | None = None
    telegram_api_hash_configured: bool = False
    telegram_allowed_chat_ids: list[int] = Field(default_factory=list)


class BitgetAsset(BaseModel):
    coin: str
    equity: Decimal = Decimal("0")
    usd_value: Decimal = Decimal("0")
    balance: Decimal = Decimal("0")
    available: Decimal = Decimal("0")
    locked: Decimal = Decimal("0")
    debt: Decimal = Decimal("0")
    bonus: Decimal = Decimal("0")


class BitgetPosition(BaseModel):
    symbol: str
    side: str
    margin_mode: str
    margin_coin: str
    total: Decimal = Decimal("0")
    leverage: Decimal = Decimal("0")
    average_price: Decimal = Decimal("0")
    mark_price: Decimal = Decimal("0")
    liquidation_price: Decimal | None = None
    unrealised_pnl: Decimal = Decimal("0")
    realised_pnl: Decimal = Decimal("0")
    profit_rate: Decimal = Decimal("0")


class BitgetOpenOrder(BaseModel):
    symbol: str
    category: str
    side: str
    position_side: str | None = None
    order_type: str
    price: Decimal = Decimal("0")
    quantity: Decimal = Decimal("0")
    filled_quantity: Decimal = Decimal("0")
    status: str
    created_at: datetime | None = None


class BitgetAccountSnapshot(BaseModel):
    environment: str
    account_mode: str | None = None
    account_level: str | None = None
    asset_mode: str | None = None
    hold_mode: str | None = None
    permission_type: str | None = None
    permissions: list[str] = Field(default_factory=list)
    account_equity_usd: Decimal = Decimal("0")
    account_equity_usdt: Decimal = Decimal("0")
    account_equity_btc: Decimal = Decimal("0")
    effective_equity_usd: Decimal = Decimal("0")
    unrealised_pnl_usd: Decimal = Decimal("0")
    initial_margin_usd: Decimal = Decimal("0")
    maintenance_margin_usd: Decimal = Decimal("0")
    margin_ratio: Decimal = Decimal("0")
    position_value_usd: Decimal = Decimal("0")
    leverage: Decimal = Decimal("0")
    assets: list[BitgetAsset] = Field(default_factory=list)
    positions: list[BitgetPosition] = Field(default_factory=list)
    open_orders: list[BitgetOpenOrder] = Field(default_factory=list)
    realtime_connected: bool = False
    realtime_detail: str = "实时连接未启动"
    last_realtime_event_at: datetime | None = None
    updated_at: datetime


class ReviewModeRequest(BaseModel):
    enabled: bool


class PaperTradeStatus(StrEnum):
    PENDING = "pending"
    OPEN = "open"
    CLOSED = "closed"
    REJECTED = "rejected"


class PaperAccountResetRequest(BaseModel):
    initial_balance: Decimal = Field(gt=0)
    leverage: int = Field(default=10, ge=1, le=100)
    fee_rate: Decimal = Field(default=Decimal("0.0006"), ge=0, le=Decimal("0.01"))
    selected_sources: list[str] = Field(default_factory=list)


class PaperAutoExecuteRequest(BaseModel):
    enabled: bool


class PaperStrategyRequest(BaseModel):
    selected_sources: list[str]


class PaperTrade(BaseModel):
    id: str
    signal_id: str
    symbol: str
    side: SignalSide
    status: PaperTradeStatus
    entry_low: Decimal
    entry_high: Decimal
    entry_price: Decimal | None = None
    last_price: Decimal | None = None
    size: Decimal | None = None
    remaining_size: Decimal | None = None
    stop_loss: Decimal
    take_profits: list[Decimal]
    next_take_profit: int = 0
    leverage: int
    risk_percent: Decimal
    margin: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    close_reason: str | None = None
    created_at: datetime
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    updated_at: datetime


class PaperAccount(BaseModel):
    initialized: bool
    initial_balance: Decimal = Decimal("0")
    equity: Decimal = Decimal("0")
    available_balance: Decimal = Decimal("0")
    used_margin: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    fees_paid: Decimal = Decimal("0")
    return_percent: Decimal = Decimal("0")
    leverage: int = 10
    fee_rate: Decimal = Decimal("0.0006")
    auto_execute: bool = True
    selected_sources: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None
    trades: list[PaperTrade] = Field(default_factory=list)
