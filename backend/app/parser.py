from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .models import ParsedSignal, SignalSide

NUMBER = r"([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)"
RANGE_SEPARATOR = r"(?:-|–|—|~|至|到)"
SYMBOL_RE = re.compile(r"(?:#|\b)([A-Z0-9]{2,12})(?:[/_-]?USDT)\b", re.IGNORECASE)
HASHTAG_RE = re.compile(r"#([A-Z0-9]{2,12})\b", re.IGNORECASE)
LONG_RE = re.compile(r"\bLONG\b|做多|多单|买入", re.IGNORECASE)
SHORT_RE = re.compile(r"\bSHORT\b|做空|空单|卖出", re.IGNORECASE)
ENTRY_RE = re.compile(
    rf"(?:ENTRY|ENTER|入场(?:价)?|进场(?:价)?|BUY\s*ZONE|SELL\s*ZONE)\s*[:：]?\s*{NUMBER}"
    rf"(?:\s*{RANGE_SEPARATOR}\s*{NUMBER})?",
    re.IGNORECASE,
)
STOP_RE = re.compile(
    rf"(?:SL|STOP\s*LOSS|STOPLOSS|止损)\s*[:：]?\s*{NUMBER}",
    re.IGNORECASE,
)
TAKE_PROFIT_RE = re.compile(
    rf"(?:TP\s*\d*|TAKE\s*PROFIT\s*\d*|止盈\s*\d*)\s*[:：]?\s*{NUMBER}",
    re.IGNORECASE,
)
RISK_RE = re.compile(
    rf"(?:RISK|风险|仓位)\s*[:：]?\s*{NUMBER}\s*%",
    re.IGNORECASE,
)


class SignalParseError(ValueError):
    pass


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation as exc:
        raise SignalParseError(f"invalid numeric value: {value}") from exc


def _extract_symbol(text: str) -> str | None:
    match = SYMBOL_RE.search(text)
    if match:
        return f"{match.group(1).upper()}USDT"
    hashtag = HASHTAG_RE.search(text)
    if hashtag:
        return f"{hashtag.group(1).upper()}USDT"
    return None


def _signal_id(chat_id: int | None, message_id: int | None, text: str) -> str:
    identity = f"{chat_id or 'manual'}:{message_id or 'none'}:{text.strip()}".encode()
    return hashlib.sha256(identity).hexdigest()[:24]


def parse_signal(
    text: str,
    *,
    source_name: str,
    chat_id: int | None = None,
    message_id: int | None = None,
) -> ParsedSignal:
    normalized = text.strip()
    if not normalized:
        raise SignalParseError("empty message")

    symbol = _extract_symbol(normalized)
    is_long = bool(LONG_RE.search(normalized))
    is_short = bool(SHORT_RE.search(normalized))
    entry_match = ENTRY_RE.search(normalized)
    stop_match = STOP_RE.search(normalized)
    take_profit_matches = list(TAKE_PROFIT_RE.finditer(normalized))
    risk_match = RISK_RE.search(normalized)

    missing: list[str] = []
    if not symbol:
        missing.append("symbol")
    if is_long == is_short:
        missing.append("unambiguous side")
    if not entry_match:
        missing.append("entry")
    if not stop_match:
        missing.append("stop loss")
    if not take_profit_matches:
        missing.append("take profit")
    if missing:
        raise SignalParseError(f"missing required fields: {', '.join(missing)}")

    entry_low = _decimal(entry_match.group(1))
    entry_high = _decimal(entry_match.group(2)) if entry_match.group(2) else entry_low
    take_profits = [_decimal(match.group(1)) for match in take_profit_matches]
    confidence = 0.91 if entry_low != entry_high else 0.98

    return ParsedSignal(
        id=_signal_id(chat_id, message_id, normalized),
        source_chat_id=chat_id,
        source_name=source_name,
        source_message_id=message_id,
        raw_text=normalized,
        symbol=symbol,
        side=SignalSide.LONG if is_long else SignalSide.SHORT,
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=_decimal(stop_match.group(1)),
        take_profits=take_profits,
        risk_percent=_decimal(risk_match.group(1)) if risk_match else None,
        confidence=confidence,
    )
