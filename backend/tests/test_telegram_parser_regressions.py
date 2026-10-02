import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.parser import parse_signal,merge_reply,SignalParseError
from app.service import CopierService
from app.storage import SignalStore
from test_desktop_workflow import FakeTelegram,event,settings

ONE='#ONE 市價空 0.0031410'
ONE_PROTECTION='止盈：0.0029687-0.0027859-0.0025235\n\n止損：0.0033155'
FORM='#FORM 市價多 0.3161'
FORM_PROTECTION='止盈：0.332-0.355-0.382\n\n止損：0.305'


@pytest.mark.parametrize('entry,protection,side,stop',[(ONE,ONE_PROTECTION,'short','0.0033155'),(FORM,FORM_PROTECTION,'long','0.305')])
def test_exact_screenshot_text(entry,protection,side,stop):
    signal=parse_signal(merge_reply(entry,protection),source_name='fixture')
    assert signal.side==side and signal.stop_loss==D(stop)
    assert len(signal.take_profits)==3
    assert '止損' in signal.raw_text


def test_nil_decimal_typo_must_not_be_guessed_or_traded():
    with pytest.raises(SignalParseError) as exc:
        parse_signal(merge_reply('#NIL 市價多 進場0.9085','止盈：0.09474-0.10388\n止损：0.08687'),source_name='fixture')
    assert '0.9085' in str(exc.value) and '0.09474' in str(exc.value)
    assert '不自动修正小数位' in str(exc.value)
    assert 'pydantic' not in str(exc.value)


def test_formatting_variants_preserve_all_decimal_digits():
    signal=parse_signal(merge_reply('#FORM 市價多 ０．３１６１','止盈：０．３３２－０．３５５－０．３８２\n止\u200b損：０．３０５'),source_name='fixture')
    assert signal.reference_entry==D('.3161')
    assert signal.take_profits==[D('.332'),D('.355'),D('.382')]


def test_distinct_telegram_ids_only_dispatch_one_complete_signal(tmp_path):
    async def run():
        service=CopierService(settings(tmp_path)); service.telegram.client=FakeTelegram()
        calls=[]
        async def execute(signal): calls.append(signal.id)
        service.telegram.on_signal=execute
        try:
            for mid in [7671,7672]: await service.telegram._handle_message(event(ONE,message_id=mid,age=60))
            for mid,parent in [(7673,7671),(7674,7672)]:
                incoming=event(ONE_PROTECTION,message_id=mid); incoming.message.reply_to_msg_id=parent
                await service.telegram._handle_message(incoming)
            assert len(calls)==1
            assert service.store.get_message(-100123,7671)['status']=='parsed'
            assert service.store.get_message(-100123,7672)['status']=='duplicate'
            assert service.store.get_message(-100123,7674)['duplicate_of']==7671
            assert len(service.store.messages())==4 # source evidence retained
            other=CopierService(settings(tmp_path)); other.telegram.client=FakeTelegram(); other.telegram.on_signal=execute
            try:
                await other.telegram._handle_message(event(ONE,message_id=7691,age=60))
                incoming=event(ONE_PROTECTION,message_id=7692); incoming.message.reply_to_msg_id=7691
                await other.telegram._handle_message(incoming)
                assert len(calls)==1
            finally: await other.bitget.close()
        finally: await service.bitget.close()
    asyncio.run(run())


@pytest.mark.parametrize('difference',['channel','time','stop','targets','side'])
def test_same_coin_alone_never_deduplicates(tmp_path,difference):
    store=SignalStore(tmp_path/'aliases.db'); at=datetime.now(UTC)
    def resolve(identifier,chat,text,timestamp):
        signal=parse_signal(text,source_name='fixture',chat_id=chat,message_id=identifier)
        root={'sent_at':timestamp.isoformat()}; message={'origin':'live','detail':'parsed'}
        return store.canonicalize_signal(signal,message,root),message
    original=merge_reply(FORM,FORM_PROTECTION)
    first,_=resolve(1,-1,original,at)
    text=original
    if difference=='stop': text=text.replace('.305','.304')
    if difference=='targets': text=text.replace('.382','.383')
    if difference=='side': text='#FORM 市價空 0.3161\n止損：0.4\n止盈：0.3'
    second,message=resolve(2,-2 if difference=='channel' else -1,text,at+timedelta(seconds=121 if difference=='time' else 1))
    assert second.id!=first.id and 'duplicate_of' not in message


def test_cached_repair_updates_both_roots_and_replies_without_orders(tmp_path):
    async def run():
        service=CopierService(settings(tmp_path)); now=datetime.now(UTC)-timedelta(days=1)
        async def forbidden(*args): raise AssertionError('repair must never execute or call network')
        service.telegram.on_signal=forbidden; service.telegram.on_management=forbidden
        class NoNetwork:
            get_messages=forbidden
        service.telegram.client=NoNetwork()
        texts=[(7681,FORM,None),(7682,FORM,None),(7683,FORM_PROTECTION,7681),(7684,FORM_PROTECTION,7682),
               (7677,'#NIL 市價多 進場0.9085',None),(7679,'止盈：0.09474-0.10388\n止损：0.08687',7677)]
        for index,(mid,text,parent) in enumerate(texts):
            service.store.record_message({'chat_id':-100123,'message_id':mid,'source_name':'fixture','text':text,'origin':'live',
                'sent_at':(now+timedelta(seconds=index)).isoformat(),'reply_to_message_id':parent,'status':'unparsed' if parent else 'waiting'})
        try:
            assert await service.reparse_cached_messages()==6
            assert service.store.get_message(-100123,7681)['status']=='parsed'
            assert service.store.get_message(-100123,7682)['status']=='duplicate'
            assert service.store.get_message(-100123,7683)['display_only']
            assert service.store.get_message(-100123,7683)['origin']=='live'
            assert service.store.get_message(-100123,7677)['status']=='invalid'
            assert '0.9085' in service.store.get_message(-100123,7679)['detail']
            assert not service.store.list()
            await service.reparse_cached_messages()
            assert not service.store.list()
            assert service.store.get_message(-100123,7682)['duplicate_of']==7681
        finally: await service.bitget.close()
    asyncio.run(run())
