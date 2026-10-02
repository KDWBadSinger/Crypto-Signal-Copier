import asyncio
from decimal import Decimal as D

import pytest

from app.management import parse_management
from app.paper import PaperTradingStore, PaperTradingError
from app.parser import parse_signal
from app.service import CopierService
from test_desktop_workflow import settings, event, FakeTelegram


def opened(tmp_path, side='多', leverage=10):
    paper = PaperTradingStore(tmp_path/'paper.sqlite3')
    paper.reset(D('1000'), leverage, D('0.001'), ['test'], 'management')
    signal = parse_signal(f'#BTC 市价{side} 100\n止损：{90 if side == "多" else 110}\n止盈：{ "120-130-140" if side == "多" else "80-70-60"}',
                          source_name='test', chat_id=-100123, message_id=1)
    paper.enqueue(signal)
    paper.mark('BTCUSDT', D(100))
    return paper, signal


@pytest.mark.parametrize('text,action', [('#BTC 浮盈過半，穩健帶成本損','breakeven'),
    ('#BTC 直接手動TP1','tp1'), ('可留小仓做格局','runner')])
def test_vocabulary(text, action):
    assert parse_management(text)['actions'] == [action]


def test_negated_and_multi_symbol_commands_are_not_executable():
    assert parse_management('#BTC 不要直接手動TP1')['ambiguous']
    assert parse_management('#BTC #ETH 可留小仓做格局')['ambiguous']


@pytest.mark.parametrize('side,price', [('多','110'),('空','90')])
def test_fee_inclusive_cost_stop(tmp_path, side, price):
    paper, signal = opened(tmp_path, side)
    trade = paper.snapshot().trades[0]
    paper.manage(-100123,2,trade.id,['breakeven'],D(price))
    stop = paper.snapshot().trades[0].stop_loss
    gross = (stop-D(100)) if side == '多' else (D(100)-stop)
    assert abs(gross - (D(100)+stop)*D('.001')) < D('1e-20')
    paper.mark('BTCUSDT', stop)
    account = paper.snapshot()
    assert abs(account.trades[0].realized_pnl-account.trades[0].fees) < D('1e-20')


def test_tp1_then_runner_reduces_current_not_original_and_survives_restart(tmp_path):
    paper, signal = opened(tmp_path)
    trade = paper.snapshot().trades[0]
    paper.manage(-100123,2,trade.id,['tp1'],D(110))
    remaining = paper.snapshot().trades[0].remaining_size
    assert remaining < trade.remaining_size
    paper.manage(-100123,3,trade.id,['runner'],D(110))
    runner = paper.snapshot().trades[0]
    assert abs(runner.remaining_size-remaining*D('.1')) < D('.00000001')
    assert runner.take_profits == [D(150)]
    paper = PaperTradingStore(tmp_path/'paper.sqlite3')
    paper.manage(-100123,3,trade.id,['runner'],D(111))
    paper.manage(-100123,4,trade.id,['runner','tp1'],D(111))
    assert paper.snapshot().trades[0].remaining_size == runner.remaining_size
    paper.mark('BTCUSDT',D(150))
    assert paper.snapshot().trades[0].status == 'closed'


def test_impossible_short_runner_rolls_back_all_actions(tmp_path):
    paper, signal = opened(tmp_path,'空',5)
    before = paper.snapshot().trades[0]
    with pytest.raises(PaperTradingError):
        paper.manage(-100123,2,before.id,['breakeven','runner'],D(90))
    assert paper.snapshot().trades[0] == before


def test_channel_and_ambiguous_match_fail_closed(tmp_path):
    paper, signal = opened(tmp_path)
    with pytest.raises(PaperTradingError): paper.management_target(-999,'BTCUSDT')
    other = signal.model_copy(update={'id':'another','source_message_id':2})
    paper.enqueue(other)
    paper.mark('BTCUSDT',D(100))
    with pytest.raises(PaperTradingError): paper.management_target(-100123,'BTCUSDT')
    assert paper.management_target(-100123,'BTCUSDT',1)['signal_id'] == signal.id


@pytest.mark.parametrize('kind', ['live','edit','stale','history','missing_symbol','reply'])
def test_telegram_management_routing(tmp_path,kind):
    async def run():
        service = CopierService(settings(tmp_path))
        service.telegram.client = FakeTelegram()
        try:
            service.paper.reset(D(1000),10,D('.001'),['test'],'routing')
            signal = parse_signal('#BTC 市价多 100\n止损：90\n止盈：120-130-140',source_name='test',chat_id=-100123,message_id=1)
            service.paper.enqueue(signal)
            service.paper.mark('BTCUSDT',D(100))
            async def price(symbol): return D(110)
            service.public_price = price
            text = '可留小仓做格局' if kind in ('missing_symbol','reply') else '#BTC 直接手動TP1'
            e = event(text,message_id=20,age=130 if kind == 'stale' else 0)
            if kind == 'reply': e.message.reply_to_msg_id = 1
            if kind == 'history':
                message = service.telegram.describe_message(e.message,-100123,'test',origin='history')
                message['text'] = text
                assert await service.telegram.inspect_message(message) is None
                assert message['status'] == 'management'
            else:
                await service.telegram._handle_message(e,edited=kind == 'edit')
                msg = service.store.get_message(-100123,20)
                if kind in ('live','reply'): assert msg['status'] == 'managed', msg
            trade = service.paper.snapshot().trades[0]
            assert (trade.remaining_size < trade.size) == (kind in ('live','reply'))
        finally:
            await service.bitget.close()
    asyncio.run(run())
