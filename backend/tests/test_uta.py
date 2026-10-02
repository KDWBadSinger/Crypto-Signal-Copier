import asyncio
from dataclasses import replace
from decimal import Decimal as D
import json

import httpx
import pytest

from app.bitget import BitgetDemoClient, BitgetError, BitgetOrderUncertain
from app.parser import parse_signal
from app.uta import build_order_preview, UtaReadiness
from test_desktop_workflow import settings

INSTRUMENT = {'symbol':'BTCUSDT','category':'USDT-FUTURES','status':'online','type':'perpetual',
              'quantityMultiplier':'0.0001','priceMultiplier':'0.1','minOrderQty':'0.0001',
              'minOrderAmount':'5','maxMarketOrderQty':'220','minLeverage':'1','maxLeverage':'50','takerFeeRate':'0.0006'}


def preview(**kwargs):
    signal = parse_signal('#BTC 市价多 100\n止损：90\n止盈：120',source_name='test')
    params = dict(margin=D(100),leverage=10,market_price=D(100),hold_mode='one_way_mode',account_scope='test')
    params.update(kwargs)
    return build_order_preview(signal,INSTRUMENT,**params)


def test_v3_fields_margin_and_protection():
    result = preview()
    assert result['payload']['qty'] == '10'
    assert 'size' not in result['payload'] and 'tradeSide' not in result['payload']
    assert 'posSide' not in result['payload']
    assert result['payload']['stopLoss'] == '90'
    assert result['payload']['takeProfit'] == '120'
    assert result['payload']['slTriggerBy'] == 'mark'
    assert D(result['estimated_opening_fee']) == D('.6')
    assert not result['execution_enabled']
    assert preview(hold_mode='hedge_mode')['payload']['posSide'] == 'long'
    assert preview(leverage=100)['effective_leverage'] == 50
    assert preview(account_scope='other')['payload']['clientOid'] != result['payload']['clientOid']


@pytest.mark.parametrize('changes', [{'margin':D('.1')},{'margin':D(100000)},{'market_price':D(105)},
                                  {'market_price':D('NaN')},{'hold_mode':'unknown'},{'leverage':0}])
def test_preview_rejects_unsafe_values(changes):
    with pytest.raises((ValueError,BitgetError)): preview(**changes)


def test_multiple_targets_not_misrepresented_as_single_tp():
    signal = parse_signal('#BTC 市价多 100\n止损：90\n止盈：120-130',source_name='test')
    result = build_order_preview(signal,INSTRUMENT,margin=100,leverage=10,market_price=100,hold_mode='one_way_mode',account_scope='x')
    assert result['multi_target_protection_required']
    assert 'takeProfit' not in result['payload']


def test_readiness_does_not_write_or_disclose_identity(tmp_path):
    async def run():
        client = BitgetDemoClient(settings(tmp_path,bitget_api_environment='live',bitget_api_key='dummy',bitget_api_secret='dummy',bitget_api_passphrase='dummy'))
        requests=[]
        def handle(request):
            requests.append(request)
            data = {'accountMode':'unified','holdMode':'hedge_mode','uid':'sensitive'} if request.url.path.endswith('/settings') else {'permType':'read-only','permissions':['uta_trade','withdraw'],'ips':'sensitive','userId':'sensitive'}
            return httpx.Response(200,json={'code':'00000','data':data})
        await client._http.aclose(); client._http = httpx.AsyncClient(base_url='https://api.bitget.com',transport=httpx.MockTransport(handle))
        try:
            result = await UtaReadiness(client).check()
            assert all(r.method == 'GET' for r in requests)
            assert 'sensitive' not in json.dumps(result)
            assert not result['execution_enabled']
            assert any('未授予提现权限' in b for b in result['blockers'])
        finally: await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('code',['40010','40725','45001'])
def test_uta_ambiguous_write_never_classified_as_rejected(tmp_path,code):
    async def run():
        client = BitgetDemoClient(settings(tmp_path,bitget_api_key='dummy',bitget_api_secret='dummy',bitget_api_passphrase='dummy'))
        await client._http.aclose()
        client._http=httpx.AsyncClient(base_url='https://api.bitget.com',transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'code':code,'msg':'unknown'})))
        try:
            with pytest.raises(BitgetOrderUncertain): await client._request('POST','/api/v3/trade/modify-strategy-order',payload={})
        finally: await client.close()
    asyncio.run(run())
