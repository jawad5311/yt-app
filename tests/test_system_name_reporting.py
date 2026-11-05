"""Tests for System Name setting and reporting integration across email subjects and reports."""

import tkinter as tk
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.automation.email_reporter import EmailReporter, SessionReport
from src.automation.watching_worker import WatchingWorker
from src.automation.worker import AutomationWorker
from src.config.manager import ConfigManager
from src.models.account import Account
from src.models.channel import Channel
from src.ui.views.settings_view import SettingsView


@pytest.fixture
def temp_config(tmp_path):
    config_file = tmp_path / "settings.json"
    cfg = ConfigManager(config_file=config_file)
    return cfg


class TestSystemNameConfig:
    def test_default_system_name(self, temp_config):
        assert "SystemName" in temp_config.data["settings"]
        assert temp_config.get_setting("SystemName", "") == ""

    def test_set_and_reload_system_name(self, temp_config):
        temp_config.set_setting("SystemName", "Alpha-Rig-01")
        reloaded = ConfigManager(config_file=temp_config.config_file)
        assert reloaded.get_setting("SystemName") == "Alpha-Rig-01"


class TestSettingsViewSystemName:
    @pytest.fixture(autouse=True)
    def setup_tk(self):
        try:
            root = tk.Tk()
            root.withdraw()
            yield root
            root.destroy()
        except tk.TclError:
            pytest.skip("Tkinter display not available")

    def test_settings_view_loads_and_saves_system_name(self, setup_tk, temp_config):
        temp_config.set_setting("SystemName", "Worker-Node-07")
        controller = MagicMock()
        controller.config_mgr = temp_config
        view = SettingsView(setup_tk, app_controller=controller)

        assert hasattr(view, "system_name_entry")
        assert view.system_name_entry.get() == "Worker-Node-07"

        # Update the entry
        view.system_name_entry.delete(0, "end")
        view.system_name_entry.insert(0, "Worker-Node-99")

        view.save_settings()

        assert temp_config.get_setting("SystemName") == "Worker-Node-99"
        view.dismiss_bubble()


class TestSessionReportSystemName:
    @pytest.fixture
    def base_report(self):
        start = datetime(2026, 11, 6, 14, 30, 0)
        end = start + timedelta(hours=1)
        return SessionReport(
            account_email="user@test.com",
            session_mode="Continuous Browsing",
            start_time=start,
            end_time=end,
            duration_seconds=3600.0,
            total_session_watch_seconds=3000.0,
            channel_reports=[
                {
                    "name": "Test Brand",
                    "session_watch_seconds": 3000.0,
                    "initial_watch_seconds": 1000.0,
                    "final_watch_seconds": 4000.0,
                }
            ],
            status="Completed",
            system_name="Worker-PC-01",
        )

    def test_subject_starts_with_system_name(self, base_report):
        subject = base_report.generate_subject()
        assert subject.startswith("Worker-PC-01 - ")
        assert "Entertainment Hub Session" in subject
        assert "user@test.com" in subject

    def test_custom_subject_prepends_system_name(self, base_report):
        base_report.custom_subject = "Custom Entertainment Thread [123] - user@test.com"
        subject = base_report.generate_subject()
        assert subject.startswith("Worker-PC-01 - ")
        assert "Custom Entertainment Thread [123]" in subject

    def test_custom_subject_does_not_double_prepend(self, base_report):
        base_report.custom_subject = "Worker-PC-01 - Custom Entertainment Thread [123]"
        subject = base_report.generate_subject()
        assert subject == "Worker-PC-01 - Custom Entertainment Thread [123]"

    def test_subject_without_system_name_remains_backward_compatible(self, base_report):
        base_report.system_name = ""
        subject = base_report.generate_subject()
        assert subject.startswith("Entertainment Hub Session")

    def test_plain_text_displays_system_name_before_account_and_above_stats(self, base_report):
        text = base_report.generate_plain_text()
        assert "System Name:          Worker-PC-01" in text
        assert "Account:              user@test.com" in text

        # Verify System Name is placed before Account
        idx_sys = text.index("System Name:          Worker-PC-01")
        idx_acc = text.index("Account:              user@test.com")
        idx_stats = text.index("COMBINED ENTERTAINMENT TIME")
        assert idx_sys < idx_acc < idx_stats

    def test_html_displays_system_name_above_account_and_stats(self, base_report):
        html_code = base_report.generate_html()
        assert "System Name:" in html_code
        assert "Worker-PC-01" in html_code
        assert "user@test.com" in html_code

        idx_sys = html_code.index("Worker-PC-01")
        idx_acc = html_code.index("user@test.com")
        idx_kpi = html_code.index("Combined Entertainment Time")
        assert idx_sys < idx_acc < idx_kpi


class TestEmailReporterNotificationsSystemName:
    @patch.object(EmailReporter, "send_via_headless_browser_gmail", return_value=True)
    def test_send_start_notification_includes_system_name(self, mock_headless, temp_config):
        temp_config.update_settings({
            "SystemName": "Production-Box-05",
            "ReportRecipientEmail": "admin@example.com",
            "ReportEmailMethod": "Headless Browser Gmail (Recommended)",
        })
        account = Account(email="active@gmail.com", password="123")
        start_dt = datetime(2026, 11, 6, 15, 0, 0)

        ok = EmailReporter.send_start_notification(
            account=account,
            session_mode="Continuous Browsing",
            config_mgr=temp_config,
            start_time=start_dt,
            total_app_watch_seconds=3600.0,
        )

        assert ok is True
        mock_headless.assert_called_once()
        args = mock_headless.call_args[1]

        # Subject must start with System Name
        assert args["subject"].startswith("Production-Box-05 - ")
        assert "Entertainment Hub Session" in args["subject"]

        # Body must include System Name before Account
        body = args["body_text"]
        assert "- System Name: Production-Box-05" in body
        assert "- Account: active@gmail.com" in body
        assert body.index("- System Name: Production-Box-05") < body.index("- Account: active@gmail.com")

    @patch("smtplib.SMTP")
    def test_send_alert_notification_includes_system_name(self, mock_smtp, temp_config):
        temp_config.update_settings({
            "SystemName": "Alert-Rig-09",
            "ReportRecipientEmail": "admin@example.com",
            "SmtpPassword": "test-password",
            "SmtpUsername": "sender@gmail.com",
        })
        account = Account(email="alert@gmail.com", password="123")
        start_dt = datetime(2026, 11, 6, 15, 10, 0)

        ok = EmailReporter.send_alert_notification(
            account=account,
            alert_type="Bot Challenge",
            message_details="Cloudflare captcha displayed",
            config_mgr=temp_config,
            start_time=start_dt,
        )

        assert ok is True
        mock_smtp.assert_called_once()
        instance = mock_smtp.return_value
        call_args = instance.sendmail.call_args[0]
        import email as em
        parsed_msg = em.message_from_string(call_args[2])

        # Subject starts with System Name
        subject = parsed_msg["Subject"]
        assert subject.startswith("Alert-Rig-09 - ")

        payload = parsed_msg.get_payload(decode=True).decode("utf-8")
        assert "- System Name:  Alert-Rig-09" in payload
        assert "- Account:      alert@gmail.com" in payload
        assert payload.index("- System Name:  Alert-Rig-09") < payload.index("- Account:      alert@gmail.com")


class TestWorkersSystemNameIntegration:
    def test_watching_worker_subject_and_report_system_name(self, temp_config):
        temp_config.update_settings({"SystemName": "Node-Watch-02"})
        account = Account(email="worker@gmail.com", password="123")
        worker = WatchingWorker(
            account=account,
            channels=[Channel(name="Worker Ch", watch_seconds=100.0)],
            config_mgr=temp_config,
        )

        assert worker.session_thread_subject.startswith("Node-Watch-02 - ")
        assert "worker@gmail.com" in worker.session_thread_subject

    def test_automation_worker_subject_and_report_system_name(self, temp_config):
        temp_config.update_settings({"SystemName": "Node-Auto-03"})
        account = Account(email="auto@gmail.com", password="123")
        worker = AutomationWorker(
            account=account,
            video_tasks=[],
            config_mgr=temp_config,
        )

        assert worker.session_thread_subject.startswith("Node-Auto-03 - ")
        assert "auto@gmail.com" in worker.session_thread_subject
