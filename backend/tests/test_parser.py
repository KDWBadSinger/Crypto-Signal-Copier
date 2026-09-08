from decimal import Decimal

import pytest

from app.models import SignalSide
from app.parser import SignalParseError, parse_signal


def test_parses_english_short_signal() -> None:
    signal = parse_signal(
        """ETHUSDT SHORT
Entry: 3680 - 3720
SL: 3785
TP1: 3560
TP2: 3440
Risk: 1%""",
        source_name="test",
        chat_id=-1001,
        message_id=42,
    )

    assert signal.symbol == "ETHUSDT"
    assert signal.side == SignalSide.SHORT
    assert signal.entry_low == Decimal("3680")
    assert signal.entry_high == Decimal("3720")
    assert signal.stop_loss == Decimal("3785")
    assert signal.take_profits == [Decimal("3560"), Decimal("3440")]
    assert signal.risk_percent == Decimal("1")
    assert signal.confidence == 0.91


def test_parses_chinese_long_signal_with_hashtag() -> None:
    signal = parse_signal(
        """#BTC 做多
入场价：115800 至 116200
止损：113900
止盈1：118500
止盈2：121000
风险：0.8%""",
        source_name="test",
    )

    assert signal.symbol == "BTCUSDT"
    assert signal.side == SignalSide.LONG
    assert signal.take_profits[-1] == Decimal("121000")


def test_rejects_incomplete_message() -> None:
    with pytest.raises(SignalParseError, match="missing required fields"):
        parse_signal("BTC is looking strong today", source_name="chat")


def test_signal_id_is_stable_for_deduplication() -> None:
    kwargs = {
        "text": "SOLUSDT LONG\nEntry: 180\nSL: 170\nTP1: 200",
        "source_name": "test",
        "chat_id": -1002,
        "message_id": 8,
    }
    assert parse_signal(**kwargs).id == parse_signal(**kwargs).id
