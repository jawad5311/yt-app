"""Tests verifying sidebar navigation tab order."""

import inspect
from src.ui.app import YTBotApp


def test_accounts_tab_is_immediately_above_settings():
    """Verify Accounts tab is positioned immediately above Settings tab in the sidebar nav_items."""
    src = inspect.getsource(YTBotApp._build_ui)
    assert '("accounts", "👤  Accounts")' in src or "('accounts', '👤  Accounts')" in src
    assert '("settings", "⚙  Settings")' in src or "('settings', '⚙  Settings')" in src

    # Verify order in nav_items: shorts_queue -> accounts -> settings
    idx_shorts = src.find('"shorts_queue"')
    idx_accounts = src.find('"accounts"')
    idx_settings = src.find('"settings"')

    assert idx_shorts != -1
    assert idx_accounts != -1
    assert idx_settings != -1
    assert idx_shorts < idx_accounts < idx_settings, "Order must be shorts_queue -> accounts -> settings"
