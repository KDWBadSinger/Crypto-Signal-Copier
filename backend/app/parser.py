from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .models import ParsedSignal, SignalSide

NUMBER = r"([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)"
RANGE_SEPARATOR = r"(?:-|–|—|~|至|到)"
SYMBOL_RE = re.compile(r"(?:#|\b)([A-Z0-9]{2,12})(?:[/_-]?USDT)\b", re.IGNORECASE)
HASHTAG_RE = re.compile(r"#([A-Z0-9]{2,12})\b", re.IGNORECASE)
LONG_RE = re.compile(r"\bLONG\b|做多|多单|买入|市[价價]多", re.IGNORECASE)
SHORT_RE = re.compile(r"\bSHORT\b|做空|空单|卖出|市[价價]空", re.IGNORECASE)
ENTRY_RE = re.compile(
    rf"(?:ENTRY|ENTER|入[场場](?:价)?|[进進][场場](?:价)?|BUY\s*ZONE|SELL\s*ZONE|市[价價][多空])\s*[:：]?\s*{NUMBER}"
    rf"(?:\s*{RANGE_SEPARATOR}\s*{NUMBER})?",
    re.IGNORECASE,
)
STOP_RE = re.compile(
    rf"(?:SL|STOP\s*LOSS|STOPLOSS|止损)\s*[:：]?\s*{NUMBER}",
    re.IGNORECASE,
)
TAKE_PROFIT_RE = re.compile(
    rf"(?:TP\s*\d*|TAKE\s*PROFIT\s*\d*|止盈\s*\d*)\s*[:：]?\s*({NUMBER}(?:\s*[-–—/、]\s*{NUMBER})*)",
    re.IGNORECASE,
)
RISK_RE = re.compile(
    rf"(?:RISK|风险|仓位)\s*[:：]?\s*{NUMBER}\s*%",
    re.IGNORECASE,
)


class SignalParseError(ValueError):
    pass


PARSER_VERSION = '2026-09-23-shared-traditional-2'


def normalize_signal_text(text: str) -> str:
    # Normalize only presentation/language variants, never decimal positions.
    return unicodedata.normalize('NFKC', text).translate(str.maketrans({
        '損':'损','價':'价','場':'场','進':'进','單':'单','買':'买','賣':'卖',
        '風':'风','險':'险','倉':'仓','穩':'稳','帶':'带','動':'动',
        '過':'过','虧':'亏','暫':'暂','減':'减','餘':'余','獲':'获',
        '\u200b':None,'\u200c':None,'\u200d':None,'\ufeff':None,
    })).strip()


def signal_fingerprint(signal: ParsedSignal) -> str:
    parts=[signal.symbol,signal.side.value,str(signal.market_entry)]
    parts.extend(format(value.normalize(),'f') for value in
                 [signal.entry_low,signal.entry_high,signal.stop_loss,*signal.take_profits])
    parts.append(str(signal.risk_percent))
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()


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
    market = re.match(r"^\s*([A-Z0-9]{2,12})\s+市[价價][多空]", text, re.IGNORECASE)
    if market:
        return f"{market.group(1).upper()}USDT"
    return None


def _signal_id(chat_id: int | None, message_id: int | None, text: str) -> str:
    # Telegram message identity must survive edits and reconnect redelivery.
    identity = (f"{chat_id}:{message_id}" if chat_id is not None and message_id is not None
                else f"manual:{text.strip()}").encode()
    return hashlib.sha256(identity).hexdigest()[:24]


def parse_signal(
    text: str,
    *,
    source_name: str,
    chat_id: int | None = None,
    message_id: int | None = None,
    market_price: Decimal | None = None,
    allow_pending: bool = False,
) -> ParsedSignal:
    normalized = normalize_signal_text(text)
    if not normalized:
        raise SignalParseError("empty message")
    if allow_pending and re.search(r'不要|暂不|取消|如果|假如|[?？]',normalized):
        raise SignalParseError('条件、否定或疑问消息不作为立即开仓指令')

    symbols = {m.group(1).upper() for m in SYMBOL_RE.finditer(normalized)}
    symbols |= {m.group(1).upper().removesuffix('USDT') for m in HASHTAG_RE.finditer(normalized)}
    if len(symbols) > 1:
        raise SignalParseError('消息包含多个币种，不猜测开单目标')
    if len(list(ENTRY_RE.finditer(normalized))) > 1 or len(list(STOP_RE.finditer(normalized))) > 1:
        raise SignalParseError('消息包含多个入场或止损字段，请提供明确唯一的参数')

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
    pending = allow_pending and is_entry_fragment(normalized) and bool(re.search(r'市价[多空]',normalized))
    if not stop_match and not pending:
        missing.append("stop loss")
    if not take_profit_matches and not pending:
        missing.append("take profit")
    if missing:
        raise SignalParseError(f"missing required fields: {', '.join(missing)}")

    entry_low = _decimal(entry_match.group(1))
    entry_high = _decimal(entry_match.group(2)) if entry_match.group(2) else entry_low
    correction = None
    if market_price is not None and re.search(r'市价[多空]',normalized):
        from .follow_policy import correct_entry
        if entry_low != entry_high:
            raise SignalParseError('市价纠错不猜测入场区间')
        correction=correct_entry(entry_low,market_price)
        entry_low=entry_high=Decimal(correction['corrected'])
    take_profits = [_decimal(value) for match in take_profit_matches
                    for value in re.findall(NUMBER, match.group(1))]
    confidence = 0.91 if entry_low != entry_high else 0.98
    risk = _decimal(risk_match.group(1)) if risk_match else None
    if risk is not None and not Decimal('0') < risk <= Decimal('10'):
        raise SignalParseError('信号风险比例必须大于 0 且不超过 10%，不执行异常仓位请求')
    stop = _decimal(stop_match.group(1)) if stop_match else Decimal(0)
    if is_long and any(tp <= entry_high for tp in take_profits):
        raise SignalParseError(f"多单参数冲突：入场 {entry_low}，止盈 {' / '.join(map(str,take_profits))} 必须全部高于入场区间上沿 {entry_high}；请核对原文，不自动修正小数位")
    if is_short and any(tp >= entry_low for tp in take_profits):
        raise SignalParseError(f"空单参数冲突：入场 {entry_low}，止盈 {' / '.join(map(str,take_profits))} 必须全部低于入场区间下沿 {entry_low}；请核对原文，不自动修正小数位")
    if is_long and stop >= entry_low:
        raise SignalParseError(f'多单参数冲突：止损 {stop} 必须低于入场 {entry_low}')
    if not pending and is_short and stop <= entry_high:
        raise SignalParseError(f'空单参数冲突：止损 {stop} 必须高于入场 {entry_high}')

    return ParsedSignal(
        id=_signal_id(chat_id, message_id, normalized),
        source_chat_id=chat_id,
        source_name=source_name,
        source_message_id=message_id,
        raw_text=text.strip(),
        market_entry=bool(re.search(r"市[价價][多空]", normalized)),
        awaiting_protection=pending,
        entry_correction=correction,
        symbol=symbol,
        side=SignalSide.LONG if is_long else SignalSide.SHORT,
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=stop,
        take_profits=take_profits,
        risk_percent=risk,
        confidence=confidence,
    )


def is_entry_fragment(text: str) -> bool:
    """Only explicit entries qualify; ordinary conversation is not a pending order."""
    text = normalize_signal_text(text)
    return bool(_extract_symbol(text) and ENTRY_RE.search(text)
                and bool(LONG_RE.search(text)) != bool(SHORT_RE.search(text))
                and not STOP_RE.search(text) and not TAKE_PROFIT_RE.search(text))


def merge_reply(entry: str, protection: str) -> str:
    original_entry,original_protection=entry,protection
    entry,protection=normalize_signal_text(entry),normalize_signal_text(protection)
    if not is_entry_fragment(entry):
        raise SignalParseError("回复目标不是等待止盈止损的开单消息，不自动合并")
    if not STOP_RE.search(protection) or not TAKE_PROFIT_RE.search(protection):
        raise SignalParseError("回复尚未同时提供止盈和止损，不自动下单")
    symbol = _extract_symbol(protection)
    if symbol and symbol != _extract_symbol(entry):
        raise SignalParseError("回复币种与开单币种冲突，不自动合并")
    if ENTRY_RE.search(protection) or LONG_RE.search(protection) or SHORT_RE.search(protection):
        raise SignalParseError("回复含新的开单方向或入场信息，存在歧义，不自动合并")
    if len(list(STOP_RE.finditer(protection))) != 1:
        raise SignalParseError("回复包含多个止损，不自动合并")
    return original_entry + "\n" + original_protection
