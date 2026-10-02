"""Unit tests always use dummy credentials, never a developer's saved keys."""
import pytest


@pytest.fixture(autouse=True)
def dummy_credentials(monkeypatch):
    for key, value in {"BITGET_API_KEY": "test-key", "BITGET_API_SECRET": "test-secret",
                       "BITGET_API_PASSPHRASE": "test-pass", "BITGET_API_ENVIRONMENT": "demo",
                       "TELEGRAM_API_ID": "", "TELEGRAM_API_HASH": "", "TELEGRAM_PHONE": "",
                       "TELEGRAM_ALLOWED_CHAT_IDS": ""}.items():
        monkeypatch.setenv(key, value)
