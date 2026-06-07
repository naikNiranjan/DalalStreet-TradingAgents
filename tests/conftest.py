"""Shared pytest fixtures that prevent CI hangs when API keys are absent."""

import os
from unittest.mock import MagicMock, patch

import pytest


def pytest_configure(config):
    for marker in ("unit", "integration", "smoke"):
        config.addinivalue_line("markers", f"{marker}: {marker}-level tests")


_API_KEY_ENV_VARS = (
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_CN_API_KEY",
    "ZHIPU_API_KEY",
    "ZHIPU_CN_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
    "OPENROUTER_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
)


# Angel One (SmartAPI) credential vars. Importing ``tradingagents`` loads the
# developer's real ``.env`` into os.environ, so without this guard any test that
# routes ``get_stock_data``/``get_indicators`` through the default vendor would fire
# a LIVE Angel login + network call. We blank them so the adapter disables itself
# (no network) and the router falls back to yfinance, keeping the suite offline.
_ANGEL_ENV_VARS = (
    "ANGELONE_API_KEY",
    "ANGELONE_CLIENT_CODE",
    "ANGELONE_PIN",
    "ANGELONE_TOTP_SECRET",
)


@pytest.fixture(autouse=True)
def _dummy_api_keys(monkeypatch):
    for env_var in _API_KEY_ENV_VARS:
        monkeypatch.setenv(env_var, os.environ.get(env_var, "placeholder"))
    # Disable Angel One for the whole suite (see note above). Tests that want the
    # adapter active set these explicitly inside the test.
    for env_var in _ANGEL_ENV_VARS:
        monkeypatch.setenv(env_var, "")


@pytest.fixture()
def mock_llm_client():
    client = MagicMock()
    client.get_llm.return_value = MagicMock()
    with patch(
        "tradingagents.llm_clients.factory.create_llm_client",
        return_value=client,
    ):
        yield client
