from unittest.mock import MagicMock, patch
import pytest

from src.config.manager import ConfigManager
from src.ui.views.settings_view import EMAIL_METHOD_OPTIONS, SettingsView


def make_dummy_entry(val=""):
    m = MagicMock()
    m.get.return_value = str(val)
    return m


def make_dummy_switch(val=True):
    m = MagicMock()
    m.get.return_value = 1 if val else 0
    return m


def make_dummy_menu(val=""):
    m = MagicMock()
    m.get.return_value = str(val)
    return m


def create_mock_settings_view_with_email(config_mgr):
    view = SettingsView.__new__(SettingsView)
    view.config_mgr = config_mgr
    view.app_controller = MagicMock()

    view.fallback_switch = make_dummy_switch(False)
    view.skip_ads_switch = make_dummy_switch(True)
    view.threads_entry = make_dummy_entry("2")
    view.duration_menu = make_dummy_menu("Full Video")
    view.max_ad_entry = make_dummy_entry("45")
    view.full_timeout_entry = make_dummy_entry("10")
    view.stop_buffer_entry = make_dummy_entry("15")
    view.cooldown_entry = make_dummy_entry("2.5")
    view.loop_entry = make_dummy_entry("3")
    view.browsing_limit_entry = make_dummy_entry("5")
    view.browsing_unlimited_switch = make_dummy_switch(True)

    view.interactivity_switch = make_dummy_switch(True)
    view.scroll_switch = make_dummy_switch(True)
    view.caption_switch = make_dummy_switch(True)
    view.quality_switch = make_dummy_switch(True)
    view.quality_menu = make_dummy_menu("360p")
    view.micro_pause_switch = make_dummy_switch(True)
    view.like_video_switch = make_dummy_switch(True)
    view.like_comments_switch = make_dummy_switch(True)

    view.email_report_switch = make_dummy_switch(True)
    view.recipient_email_entry = make_dummy_entry("test@example.com")
    view.email_method_menu = make_dummy_menu(EMAIL_METHOD_OPTIONS[0])
    view.smtp_host_entry = make_dummy_entry("smtp.test.com")
    view.smtp_port_entry = make_dummy_entry("587")
    view.smtp_user_entry = make_dummy_entry("user@test.com")
    view.smtp_pass_entry = make_dummy_entry("secretpass")

    view.appearance_menu = make_dummy_menu("Dark")
    return view


def test_email_reporting_settings_saved(tmp_path):
    mgr = ConfigManager(tmp_path / "settings.json")
    view = create_mock_settings_view_with_email(mgr)

    with patch("src.ui.views.settings_view.messagebox") as mock_mb:
        view.save_settings()
        assert mock_mb.showinfo.called

    assert mgr.get_bool("EmailReportingEnabled") is True
    assert mgr.get_setting("ReportRecipientEmail") == "test@example.com"
    assert mgr.get_setting("ReportEmailMethod") == EMAIL_METHOD_OPTIONS[0]
    assert mgr.get_setting("SmtpHost") == "smtp.test.com"
    assert mgr.get_int("SmtpPort") == 587
    assert mgr.get_setting("SmtpUsername") == "user@test.com"
    assert mgr.get_setting("SenderEmail") == "user@test.com"
    assert mgr.get_setting("SmtpPassword") == "secretpass"


def test_invalid_recipient_email_rejected_when_enabled(tmp_path):
    mgr = ConfigManager(tmp_path / "settings.json")
    view = create_mock_settings_view_with_email(mgr)
    view.recipient_email_entry.get.return_value = "invalid-email"

    with patch("src.ui.views.settings_view.messagebox") as mock_mb:
        view.save_settings()
        assert mock_mb.showerror.called


def test_send_test_report_requires_valid_recipient(tmp_path):
    mgr = ConfigManager(tmp_path / "settings.json")
    view = create_mock_settings_view_with_email(mgr)
    view.recipient_email_entry.get.return_value = "bademail"

    with patch("src.ui.views.settings_view.messagebox") as mock_mb:
        view.send_test_report()
        assert mock_mb.showerror.called


def test_send_test_report_calls_smtp(tmp_path):
    mgr = ConfigManager(tmp_path / "settings.json")
    view = create_mock_settings_view_with_email(mgr)

    with patch("src.ui.views.settings_view.EmailReporter.send_via_smtp", return_value=True) as mock_smtp:
        with patch("src.ui.views.settings_view.messagebox") as mock_mb:
            view.send_test_report()
            assert mock_smtp.called
            assert mock_mb.showinfo.called
