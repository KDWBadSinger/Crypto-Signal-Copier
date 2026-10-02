import pytest
from pydantic import ValidationError

from app.bitget import BitgetError
from app.storage import SignalStore
from app.uta_risk import UtaRiskLimits
from app.uta_runtime import UtaRuntime
from test_execution_journal import Exchange


def test_risk_parameters_persist_without_authorizing_orders(tmp_path):
    client = Exchange()
    store = SignalStore(tmp_path / 'risk.db')
    runtime = UtaRuntime(client, store)
    assert runtime.status()['limits']['position_percent'] == '6'
    runtime.configure(UtaRiskLimits(position_percent='7', max_leverage=30, max_positions=6))
    restored = UtaRuntime(client, store)
    assert restored.status()['limits'] == {
        'position_percent': '7', 'max_leverage': 30, 'max_positions': 6,
    }
    assert restored.status()['margin_mode'] == 'crossed'
    assert not restored.status()['enabled']
    with pytest.raises(BitgetError):
        restored._authorize_write()
    assert not client.calls


def test_invalid_persisted_limits_fail_closed(tmp_path):
    store = SignalStore(tmp_path / 'risk.db')
    store.set_setting('uta_risk_limits', '{"position_percent":8,"max_leverage":31,"max_positions":7}')
    runtime = UtaRuntime(Exchange(), store)
    with pytest.raises(ValidationError):
        runtime.limits()
