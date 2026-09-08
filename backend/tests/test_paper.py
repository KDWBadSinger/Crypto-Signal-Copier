from decimal import Decimal

from app.paper import PaperTradingStore
from app.parser import parse_signal


def _signal():
    return parse_signal(
        "BTCUSDT LONG\nEntry: 100 - 110\nSL: 90\nTP1: 120\nTP2: 130\nRisk: 1%",
        source_name="test",
        chat_id=-1001,
        message_id=99,
    )


def test_pending_order_fills_from_real_market_mark_and_calculates_equity(tmp_path) -> None:
    store = PaperTradingStore(tmp_path / "paper.sqlite3")
    store.reset(Decimal("1000"), leverage=10, fee_rate=Decimal("0"))
    signal = _signal()
    assert store.enqueue(signal) is True

    store.mark("BTCUSDT", Decimal("115"))
    assert store.snapshot().trades[0].status == "pending"

    store.mark("BTCUSDT", Decimal("105"))
    opened = store.snapshot()
    trade = opened.trades[0]
    assert trade.status == "open"
    assert trade.entry_price == Decimal("105")
    assert trade.size == Decimal("0.66666666")

    store.mark("BTCUSDT", Decimal("110"))
    marked = store.snapshot()
    assert marked.unrealized_pnl == Decimal("3.33333330")
    assert marked.equity == Decimal("1003.33333330")


def test_take_profits_close_equal_tranches_and_realize_pnl(tmp_path) -> None:
    store = PaperTradingStore(tmp_path / "paper.sqlite3")
    store.reset(Decimal("1000"), leverage=10, fee_rate=Decimal("0"))
    store.enqueue(_signal())
    store.mark("BTCUSDT", Decimal("105"))

    store.mark("BTCUSDT", Decimal("120"))
    partial = store.snapshot().trades[0]
    assert partial.status == "open"
    assert partial.next_take_profit == 1
    assert partial.remaining_size == Decimal("0.33333333")

    store.mark("BTCUSDT", Decimal("130"))
    account = store.snapshot()
    trade = account.trades[0]
    assert trade.status == "closed"
    assert trade.remaining_size == Decimal("0")
    assert trade.realized_pnl == Decimal("13.33333320")
    assert account.equity == Decimal("1013.33333320")


def test_stop_loss_closes_the_remaining_position(tmp_path) -> None:
    store = PaperTradingStore(tmp_path / "paper.sqlite3")
    store.reset(Decimal("1000"), leverage=10, fee_rate=Decimal("0"))
    store.enqueue(_signal())
    store.mark("BTCUSDT", Decimal("105"))
    store.mark("BTCUSDT", Decimal("90"))

    trade = store.snapshot().trades[0]
    assert trade.status == "closed"
    assert trade.close_reason == "stop_loss"
    assert trade.realized_pnl == Decimal("-9.99999990")


def test_auto_execute_only_matches_selected_bloggers(tmp_path) -> None:
    store = PaperTradingStore(tmp_path / "paper.sqlite3")
    store.reset(
        Decimal("1000"),
        leverage=10,
        fee_rate=Decimal("0"),
        selected_sources=["Mia Crypto"],
    )

    assert store.should_auto_execute("Mia Crypto") is True
    assert store.should_auto_execute("Another Blogger") is False
    assert store.snapshot().selected_sources == ["Mia Crypto"]
