from decimal import Decimal as D

import pytest

from app.follow_policy import correct_entry, temporary_stop, target_sizes, PROTECTION_WAIT_SECONDS


def test_nil_decimal_correction_and_boundary():
    assert correct_entry('0.9085','0.09085')['corrected']=='0.09085'
    assert correct_entry('0.009085','0.09085')['decimal_shift']==1
    assert correct_entry('100','110')['decimal_shift']==0
    assert correct_entry('100','90')['decimal_shift']==0
    with pytest.raises(ValueError): correct_entry('100','110.0001')
    with pytest.raises(ValueError): correct_entry('100','80')


@pytest.mark.parametrize('value',['0','-1','NaN','Infinity'])
def test_invalid_anchor_rejected(value):
    with pytest.raises(ValueError): correct_entry('100',value)


@pytest.mark.parametrize('side',['long','short'])
def test_stop_budget_includes_round_trip_fees(side):
    entry,qty,margin,fee=D('100'),D('3'),D('10'),D('.0006')
    stop=temporary_stop(entry,qty,margin,side,fee,D('.00001'))
    loss=(entry-stop)*qty if side=='long' else (stop-entry)*qty
    loss+=(entry+stop)*qty*fee
    assert D('9.9999') <= loss <= margin
    assert PROTECTION_WAIT_SECONDS==300


def test_target_original_size_and_rounding():
    assert target_sizes('10',3,'.01')==[D(4),D(4),D(2)]
    assert target_sizes('.24',3,'.0001')==[D('.096'),D('.096'),D('.048')]
    assert target_sizes('11',3,'1')==[D(4),D(4),D(3)]
    assert target_sizes('10',2,'1')==[D(5),D(5)]
    with pytest.raises(ValueError): target_sizes('1',3,'1')
