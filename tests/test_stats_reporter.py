"""Unit tests for Channel Stats Email Reporting and Send Report button.

Validates:
- All Channels report generation (subject, plain text, executive HTML)
- Single Channel report generation with video-level breakdown
- Multi-period timeframe support (All Time, Last 38 Days, Last 7 Days, Last 24 Hours, Last Session)
- SMTP delivery routing with custom and default recipient emails
- StatsView Send Report button and modal integration
"""
from unittest.mock import MagicMock, patch

import pytest

from src.automation.email_reporter import DEFAULT_ADMIN_RECIPIENT_EMAIL, EmailReporter
from src.automation.stats_reporter import (
    ChannelStatsReport,
    build_channel_stats_report,
    send_channel_stats_report,
)
from src.models.watch_stats import StatRecord
from src.ui.theme import setup_ttk_styles
from src.ui.views.stats_view import StatsView


@pytest.fixture
def tk_root(ctk_app):
    setup_ttk_styles(ctk_app, mode="Dark")
    for child in list(ctk_app.winfo_children()):
        try:
            child.destroy()
        except Exception:
            pass
    yield ctk_app


def test_build_channel_stats_report_all_channels():
    """Verify All Channels report subject, plain-text and HTML formatting."""
    stats = {
        "time_filter": "Last 38 Days",
        "total_watch_seconds": 5400.0,
        "total_watch_formatted": "1h 30m",
        "total_watch_hours_float": 1.5,
        "active_channels_count": 2,
        "total_channels_count": 2,
        "total_runs": 6,
        "total_regular_vids": 4,
        "total_shorts": 2,
        "total_goal_seconds": 18000.0,
        "total_goal_formatted": "5h 00m",
        "total_remaining_seconds": 12600.0,
        "total_remaining_formatted": "3h 30m",
        "total_goal_progress_pct": 30.0,
        "channels": [
            {
                "name": "Alpha Garage",
                "url": "https://youtube.com/@alphagarage",
                "watch_formatted": "1h 00m",
                "watch_seconds": 3600.0,
                "percentage_share": 66.7,
                "runs": 4,
                "regular_vids_count": 3,
                "shorts_count": 1,
                "success_rate_pct": 100.0,
                "goal_formatted": "3h 00m",
                "goal_seconds": 10800.0,
                "remaining_formatted": "2h 00m",
                "goal_progress_pct": 33.3,
                "rank": 1,
            },
            {
                "name": "Beta Motors",
                "url": "https://youtube.com/@betamotors",
                "watch_formatted": "30m 00s",
                "watch_seconds": 1800.0,
                "percentage_share": 33.3,
                "runs": 2,
                "regular_vids_count": 1,
                "shorts_count": 1,
                "success_rate_pct": 100.0,
                "goal_formatted": "2h 00m",
                "goal_seconds": 7200.0,
                "remaining_formatted": "1h 30m",
                "goal_progress_pct": 25.0,
                "rank": 2,
            },
        ],
    }

    report = build_channel_stats_report(
        stats=stats,
        selected_channel="All Channels",
        time_filter="Last 38 Days",
        system_name="WORKSTATION-01",
    )

    assert isinstance(report, ChannelStatsReport)
    assert not report.is_single_channel

    # 1. Subject line
    subj = report.generate_subject()
    assert "WORKSTATION-01 - All Channels Stats Report [Last 38 Days]" in subj
    assert "1h 30m Watch Time" in subj

    # 2. Plain text
    text = report.generate_plain_text()
    assert "WORKSTATION-01" in text
    assert "Last 38 Days" in text
    assert "Alpha Garage" in text
    assert "Beta Motors" in text
    assert "1h 30m" in text
    assert "5h 00m" in text

    # 3. HTML body
    html_body = report.generate_html()
    assert "Alpha Garage" in html_body
    assert "Beta Motors" in html_body
    assert "https://youtube.com/@alphagarage" in html_body
    assert "1h 30m" in html_body
    assert "5h 00m" in html_body
    assert "WORKSTATION-01" in html_body


def test_build_channel_stats_report_single_channel():
    """Verify Single Channel report subject, video records table, and goal breakdown."""
    stats = {
        "time_filter": "All Time",
        "total_watch_seconds": 3600.0,
        "total_watch_formatted": "1h 00m",
        "channels": [
            {
                "name": "Performance Lab",
                "key": "performancelab",
                "url": "https://youtube.com/@performancelab",
                "watch_formatted": "1h 00m",
                "watch_seconds": 3600.0,
                "percentage_share": 100.0,
                "runs": 2,
                "regular_vids_count": 1,
                "shorts_count": 1,
                "success_rate_pct": 100.0,
                "goal_formatted": "3h 00m",
                "goal_seconds": 10800.0,
                "remaining_formatted": "2h 00m",
                "goal_progress_pct": 33.3,
            }
        ],
    }

    v_records = [
        {
            "video_key": "v1",
            "title": "Turbo Installation Guide",
            "url": "https://www.youtube.com/watch?v=v1",
            "is_short": False,
            "watch_formatted": "50m 00s",
            "duration_formatted": "25m 00s",
            "runs": 2,
        },
        {
            "video_key": "s1",
            "title": "Boost Gauge Sound",
            "url": "https://www.youtube.com/shorts/s1",
            "is_short": True,
            "watch_formatted": "10m 00s",
            "duration_formatted": "30s",
            "runs": 20,
        },
    ]

    report = build_channel_stats_report(
        stats=stats,
        selected_channel="Performance Lab",
        time_filter="All Time",
        system_name="SERVER-EAST",
        video_records=v_records,
    )

    assert report.is_single_channel

    # 1. Subject line
    subj = report.generate_subject()
    assert "SERVER-EAST - Channel Report: Performance Lab [All Time]" in subj
    assert "1h 00m Watch Time" in subj

    # 2. Plain text
    text = report.generate_plain_text()
    assert "Performance Lab" in text
    assert "Turbo Installation Guide" in text
    assert "Boost Gauge Sound" in text
    assert "50m 00s" in text

    # 3. HTML
    html_doc = report.generate_html()
    assert "Performance Lab" in html_doc
    assert "Turbo Installation Guide" in html_doc
    assert "https://www.youtube.com/watch?v=v1" in html_doc
    assert "Short" in html_doc
    assert "Video" in html_doc


def test_send_channel_stats_report_custom_email_smtp():
    """Verify dispatching channel stats report directly to a user-provided email address via SMTP."""
    controller = MagicMock()
    controller.config_mgr.get_setting.side_effect = lambda k, default="": {
        "SmtpHost": "smtp.customhost.com",
        "SmtpPort": 465,
        "SmtpUsername": "sender@company.com",
        "SmtpPassword": "secret_app_password",
        "SenderEmail": "sender@company.com",
        "SystemName": "TEST-NODE",
    }.get(k, default)
    controller.config_mgr.get_int.return_value = 465
    controller.loaded_accounts = []
    controller.log_queue = None

    stats = {
        "time_filter": "Last 7 Days",
        "total_watch_seconds": 1800.0,
        "total_watch_formatted": "30m 00s",
        "total_watch_hours_float": 0.5,
        "active_channels_count": 1,
        "total_channels_count": 1,
        "total_runs": 2,
        "total_regular_vids": 2,
        "total_shorts": 0,
        "total_goal_seconds": 3600.0,
        "total_goal_formatted": "1h 00m",
        "total_remaining_seconds": 1800.0,
        "total_remaining_formatted": "30m 00s",
        "total_goal_progress_pct": 50.0,
        "channels": [
            {
                "name": "Custom Channel",
                "watch_formatted": "30m 00s",
                "watch_seconds": 1800.0,
                "percentage_share": 100.0,
                "runs": 2,
                "regular_vids_count": 2,
                "shorts_count": 0,
                "success_rate_pct": 100.0,
                "goal_formatted": "1h 00m",
                "goal_seconds": 3600.0,
                "remaining_formatted": "30m 00s",
                "goal_progress_pct": 50.0,
                "rank": 1,
            }
        ],
    }

    with patch.object(EmailReporter, "send_via_smtp", return_value=True) as mock_smtp:
        ok, msg = send_channel_stats_report(
            app_controller=controller,
            stats=stats,
            selected_channel="All Channels",
            recipient_email="client@example.com",
            time_filter="Last 7 Days",
        )

        assert ok is True
        assert "client@example.com" in msg
        mock_smtp.assert_called_once()
        kwargs = mock_smtp.call_args[1]
        assert kwargs["recipient_email"] == "client@example.com"
        assert kwargs["host"] == "smtp.customhost.com"
        assert kwargs["port"] == 465
        assert kwargs["username"] == "sender@company.com"
        assert kwargs["password"] == "secret_app_password"


def test_send_channel_stats_report_single_channel_default_email():
    """Verify single channel stats report falls back to default system recipient when none provided."""
    controller = MagicMock()
    controller.config_mgr.get_setting.side_effect = lambda k, default="": {
        "ReportRecipientEmail": "manager@studio.com",
        "SmtpPassword": "mock_password",
        "SmtpUsername": "sender@studio.com",
        "SystemName": "PRIMARY-PC",
    }.get(k, default)
    controller.config_mgr.get_int.return_value = 587
    controller.loaded_accounts = []
    controller.log_queue = None

    stats = {
        "time_filter": "Last Session",
        "total_watch_seconds": 600.0,
        "total_watch_formatted": "10m 00s",
        "channels": [
            {
                "name": "Studio Channel",
                "key": "studiochannel",
                "watch_formatted": "10m 00s",
                "watch_seconds": 600.0,
                "percentage_share": 100.0,
                "runs": 1,
                "regular_vids_count": 1,
                "shorts_count": 0,
                "success_rate_pct": 100.0,
                "goal_formatted": "30m 00s",
                "goal_seconds": 1800.0,
                "remaining_formatted": "20m 00s",
                "goal_progress_pct": 33.3,
            }
        ],
    }

    with patch.object(EmailReporter, "send_via_smtp", return_value=True) as mock_smtp:
        ok, msg = send_channel_stats_report(
            app_controller=controller,
            stats=stats,
            selected_channel="Studio Channel",
            recipient_email=None,  # Falls back to manager@studio.com
            time_filter="Last Session",
        )

        assert ok is True
        mock_smtp.assert_called_once()
        kwargs = mock_smtp.call_args[1]
        assert kwargs["recipient_email"] == "manager@studio.com"
        assert kwargs["report"].is_single_channel
        assert "Studio Channel" in kwargs["report"].generate_subject()


def test_stats_view_send_report_button_and_direct_send(tk_root):
    """Verify StatsView includes the Send Report button and supports programmatically sending reports."""
    controller = MagicMock()
    controller.config_mgr.get_channels.return_value = [
        {"name": "Iamcarhacker", "url": "https://youtube.com/@iamcarhacker", "target_hours": 10.0},
    ]
    controller.config_mgr.get_setting.side_effect = lambda k, default="": {
        "ReportRecipientEmail": DEFAULT_ADMIN_RECIPIENT_EMAIL,
        "SmtpPassword": "password",
        "SmtpUsername": "sender@gmail.com",
    }.get(k, default)
    controller.config_mgr.get_int.return_value = 587
    controller.stats_store.all.return_value = {
        "v1": StatRecord(channel="Iamcarhacker", watch_seconds=1200.0, runs=1, successes=1),
    }
    controller.queued_video_tasks = []
    controller.loaded_accounts = []

    view = StatsView(tk_root, controller)
    view.pack(fill="both", expand=True)
    tk_root.update()

    # 1. Verify button presence in header
    assert hasattr(view, "send_report_btn")
    assert "Send Report" in view.send_report_btn.cget("text")

    # 2. Test programmatic send_report to custom address
    with patch.object(EmailReporter, "send_via_smtp", return_value=True) as mock_smtp:
        ok, msg = view.send_report(recipient_email="executive@partner.org")
        assert ok is True
        assert "executive@partner.org" in msg
        mock_smtp.assert_called_once()
        assert mock_smtp.call_args[1]["recipient_email"] == "executive@partner.org"

    # 3. Test open_send_report_modal creates dialog
    modal = view.open_send_report_modal()
    assert modal is not None
    assert hasattr(modal, "email_entry")
    assert modal.email_entry.get() == DEFAULT_ADMIN_RECIPIENT_EMAIL
    modal._on_close()

    view.destroy()
