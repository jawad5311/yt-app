from unittest.mock import MagicMock, patch

from src.automation.email_reporter import DEFAULT_ADMIN_RECIPIENT_EMAIL, resolve_report_recipients
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


def test_save_settings_with_empty_recipient_keeps_reporting_enabled(tmp_path):
    """Saving with empty recipient keeps email reporting enabled and saves empty string in config."""
    mgr = ConfigManager(tmp_path / "settings.json")
    view = create_mock_settings_view_with_email(mgr)
    view.recipient_email_entry.get.return_value = ""

    with patch("src.ui.views.settings_view.messagebox"):
        view.save_settings()

    assert mgr.get_bool("EmailReportingEnabled") is True
    assert mgr.get_setting("ReportRecipientEmail") == ""

    # When resolving recipients, empty string automatically routes to default admin email
    to_email, bcc_email = resolve_report_recipients(mgr)
    assert to_email == DEFAULT_ADMIN_RECIPIENT_EMAIL
    assert bcc_email is None


def test_save_settings_with_admin_email_sanitizes_to_empty(tmp_path):
    """If admin email is explicitly entered, it is sanitized to empty string to avoid exposing it."""
    mgr = ConfigManager(tmp_path / "settings.json")
    view = create_mock_settings_view_with_email(mgr)
    view.recipient_email_entry.get.return_value = DEFAULT_ADMIN_RECIPIENT_EMAIL

    with patch("src.ui.views.settings_view.messagebox"):
        view.save_settings()

    assert mgr.get_setting("ReportRecipientEmail") == ""
    to_email, bcc_email = resolve_report_recipients(mgr)
    assert to_email == DEFAULT_ADMIN_RECIPIENT_EMAIL
    assert bcc_email is None


def test_save_settings_with_custom_recipient_enables_bcc(tmp_path):
    """Entering a new recipient configures To recipient and adds admin to BCC."""
    mgr = ConfigManager(tmp_path / "settings.json")
    view = create_mock_settings_view_with_email(mgr)
    view.recipient_email_entry.get.return_value = "client@example.com"

    with patch("src.ui.views.settings_view.messagebox"):
        view.save_settings()

    assert mgr.get_setting("ReportRecipientEmail") == "client@example.com"
    to_email, bcc_email = resolve_report_recipients(mgr)
    assert to_email == "client@example.com"
    assert bcc_email == DEFAULT_ADMIN_RECIPIENT_EMAIL


def test_send_test_report_empty_entry_routes_to_admin_without_leaking_in_popup(tmp_path):
    """Test report with empty field sends to admin and popup doesn't reveal admin email."""
    mgr = ConfigManager(tmp_path / "settings.json")
    view = create_mock_settings_view_with_email(mgr)
    view.recipient_email_entry.get.return_value = ""

    with patch("src.ui.views.settings_view.EmailReporter.send_via_smtp", return_value=True) as mock_smtp:
        with patch("src.ui.views.settings_view.messagebox") as mock_mb:
            view.send_test_report()
            assert mock_smtp.called
            call_kwargs = mock_smtp.call_args[1]
            assert call_kwargs["recipient_email"] == DEFAULT_ADMIN_RECIPIENT_EMAIL
            assert call_kwargs["bcc_email"] is None

            # Verify popup does not leak admin email
            mock_mb.showinfo.assert_called_once()
            popup_msg = mock_mb.showinfo.call_args[0][1]
            assert DEFAULT_ADMIN_RECIPIENT_EMAIL not in popup_msg
            assert "Test report email was successfully sent!" in popup_msg


def test_send_test_report_custom_entry_routes_to_custom_and_bccs_admin(tmp_path):
    """Test report with custom field sends to custom recipient and BCCs admin."""
    mgr = ConfigManager(tmp_path / "settings.json")
    view = create_mock_settings_view_with_email(mgr)
    view.recipient_email_entry.get.return_value = "client@example.com"

    with patch("src.ui.views.settings_view.EmailReporter.send_via_smtp", return_value=True) as mock_smtp:
        with patch("src.ui.views.settings_view.messagebox") as mock_mb:
            view.send_test_report()
            assert mock_smtp.called
            call_kwargs = mock_smtp.call_args[1]
            assert call_kwargs["recipient_email"] == "client@example.com"
            assert call_kwargs["bcc_email"] == DEFAULT_ADMIN_RECIPIENT_EMAIL

            mock_mb.showinfo.assert_called_once()
            popup_msg = mock_mb.showinfo.call_args[0][1]
            assert "client@example.com" in popup_msg


def test_recipient_email_ui_empty_when_admin_or_unconfigured(ctk_app, tmp_path):
    """SettingsView UI initializes recipient entry as empty if setting is admin email or empty."""
    cfg_file = tmp_path / "settings.json"
    mgr = ConfigManager(cfg_file)
    mgr.update_settings({"ReportRecipientEmail": DEFAULT_ADMIN_RECIPIENT_EMAIL})

    app_ctrl = MagicMock()
    app_ctrl.config_mgr = mgr

    view = SettingsView(parent=ctk_app, app_controller=app_ctrl)
    # The entry MUST be empty when setting has admin email
    assert view.recipient_email_entry.get() == ""

    # Test with empty setting
    mgr.update_settings({"ReportRecipientEmail": ""})
    view2 = SettingsView(parent=ctk_app, app_controller=app_ctrl)
    assert view2.recipient_email_entry.get() == ""

    # Test with custom setting
    mgr.update_settings({"ReportRecipientEmail": "custom@example.com"})
    view3 = SettingsView(parent=ctk_app, app_controller=app_ctrl)
    assert view3.recipient_email_entry.get() == "custom@example.com"
