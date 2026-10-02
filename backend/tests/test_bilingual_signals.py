import pytest
from decimal import Decimal
from app.parser import parse_signal,merge_reply,signal_fingerprint,SignalParseError
from app.management import parse_management


@pytest.mark.parametrize('simple,traditional',[
    ('#BTC 市价多 100\n止损：90\n止盈：110-120\n风险：1%', '#BTC 市價多 100\n止損：90\n止盈：110-120\n風險：1%'),
    ('#BTC 多单\n入场价：100\n止损：90\n止盈：110\n仓位：2%', '#BTC 多單\n入場價：100\n止損：90\n止盈：110\n倉位：2%'),
    ('#BTC 买入\n进场价：100\n止损：90\n止盈：110', '#BTC 買入\n進場價：100\n止損：90\n止盈：110'),
    ('#BTC 空单\n入场：100\n止损：110\n止盈：90-80', '#BTC 空單\n入場：100\n止損：110\n止盈：90-80'),
    ('#BTC 卖出\n进场：100\n止损：110\n止盈：90', '#BTC 賣出\n進場：100\n止損：110\n止盈：90'),
])
def test_opening_variants_have_identical_parameters_and_duplicate_identity(simple,traditional):
    a=parse_signal(simple,source_name='test'); b=parse_signal(traditional,source_name='test')
    assert signal_fingerprint(a)==signal_fingerprint(b)
    assert b.raw_text==traditional


@pytest.mark.parametrize('opening',['#FORM 市价多 0.3161','#FORM 市價多 0.3161'])
@pytest.mark.parametrize('stop',['止损','止損'])
def test_mixed_language_replies(opening,stop):
    signal=parse_signal(merge_reply(opening,f'止盈：0.332-0.355\n{stop}：0.305'),source_name='test')
    assert signal.stop_loss==Decimal('.305')


@pytest.mark.parametrize('simple,traditional,action',[
    ('#BTC 浮盈过半，稳健带成本损','#BTC 浮盈過半，穩健帶成本損','breakeven'),
    ('#BTC 直接手动TP1','#BTC 直接手動ＴＰ１','tp1'),
    ('#BTC 可留小仓做格局','#BTC 可留小倉做格局','runner'),
])
def test_management_variants_share_the_same_normalizer(simple,traditional,action):
    assert parse_management(simple)==parse_management(traditional)
    assert parse_management(traditional)['actions']==[action]


@pytest.mark.parametrize('prefix',['暂不','暫不','不要','如果'])
def test_both_languages_keep_negation_and_condition_guard(prefix):
    assert parse_management(f'#BTC {prefix}直接手動TP1')['ambiguous']


def test_traditional_excessive_risk_not_silently_ignored():
    with pytest.raises(SignalParseError,match='风险比例'):
        parse_signal('#BTC 市價多 100\n止損：90\n止盈：110\n風險：20%',source_name='test')
