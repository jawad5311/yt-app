from unittest.mock import MagicMock, patch

from src.models.video_task import VideoTask
from src.ui.views.queue_view import QueueView


def test_queue_card_inline_entries_behavior():
    view = QueueView.__new__(QueueView)
    view.app_controller = MagicMock()
    view.save_queue = MagicMock()
    view.refresh_list = MagicMock()
    view.show_popup_notification = MagicMock()
    view._stats = MagicMock(return_value=None)

    task = VideoTask(
        title="Test Video",
        url="https://youtube.com/watch?v=123",
        target_multiplier=3.0,
        max_runs=5,
    )

    with patch("customtkinter.CTkCheckBox"), \
         patch("customtkinter.CTkLabel"), \
         patch("customtkinter.CTkFrame"), \
         patch("customtkinter.CTkButton"), \
         patch("customtkinter.CTkProgressBar"), \
         patch("customtkinter.CTkFont"), \
         patch("customtkinter.CTkEntry") as mock_entry_cls:

        mock_target_entry = MagicMock()
        mock_views_entry = MagicMock()
        mock_entry_cls.side_effect = [mock_target_entry, mock_views_entry]

        mock_parent = MagicMock()
        view._build_task_card(mock_parent, index=0, position=1, task=task, channel_enabled=True)

        # Retrieve bound callbacks
        target_binds = {call[0][0]: call[0][1] for call in mock_target_entry.bind.call_args_list}
        views_binds = {call[0][0]: call[0][1] for call in mock_views_entry.bind.call_args_list}

        assert "<FocusOut>" in target_binds
        assert "<Return>" in target_binds
        assert "<FocusOut>" in views_binds
        assert "<Return>" in views_binds

        # 1. FocusOut on target_entry: user cleared it, but clicks away -> reverts, does not save
        mock_target_entry.get.return_value = ""
        target_binds["<FocusOut>"]()
        assert task.target_multiplier == 3.0
        assert view.save_queue.call_count == 0
        assert view.show_popup_notification.call_count == 0
        mock_target_entry.insert.assert_called_with(0, "3")

        # 2. Return on target_entry with new value "2.5" -> updates, saves, shows popup
        mock_target_entry.get.return_value = "2.5"
        target_binds["<Return>"]()
        assert task.target_multiplier == 2.5
        assert view.save_queue.call_count == 1
        assert view.show_popup_notification.call_count == 1
        assert "2.5x" in view.show_popup_notification.call_args[0][1]

        # 3. Return on target_entry with blank "" -> updates to 0.0, saves, shows popup
        mock_target_entry.get.return_value = ""
        target_binds["<Return>"]()
        assert task.target_multiplier == 0.0
        assert view.save_queue.call_count == 2
        assert "0x" in view.show_popup_notification.call_args[0][1]

        # 4. FocusOut on views_entry: user typed "99", but clicks away -> reverts, does not save
        mock_views_entry.get.return_value = "99"
        views_binds["<FocusOut>"]()
        assert task.max_runs == 5
        assert view.save_queue.call_count == 2
        mock_views_entry.insert.assert_called_with(0, "5")

        # 5. Return on views_entry with new value "10" -> updates, saves, shows popup
        mock_views_entry.get.return_value = "10"
        views_binds["<Return>"]()
        assert task.max_runs == 10
        assert view.save_queue.call_count == 3
        assert "10" in view.show_popup_notification.call_args[0][1]

        # 6. Return on views_entry with blank "" -> updates to 0, saves, shows popup
        mock_views_entry.get.return_value = ""
        views_binds["<Return>"]()
        assert task.max_runs == 0
        assert view.save_queue.call_count == 4
        assert "0 (unlimited)" in view.show_popup_notification.call_args[0][1]


def test_reorder_buttons_outside_card_frame():
    view = QueueView.__new__(QueueView)
    view.app_controller = MagicMock()
    view.save_queue = MagicMock()
    view.refresh_list = MagicMock()
    view._stats = MagicMock(return_value=None)

    task = VideoTask(title="Test Video", url="https://youtube.com/watch?v=123")

    with patch("customtkinter.CTkCheckBox"),          patch("customtkinter.CTkLabel"),          patch("customtkinter.CTkFrame") as mock_frame_cls,          patch("customtkinter.CTkButton") as mock_btn_cls,          patch("customtkinter.CTkProgressBar"),          patch("customtkinter.CTkFont"),          patch("customtkinter.CTkEntry"):

        mock_parent = MagicMock()
        mock_row_frame = MagicMock()
        mock_reorder_frame = MagicMock()
        mock_card_frame = MagicMock()
        mock_action_frame = MagicMock()
        mock_frame_cls.side_effect = [mock_row_frame, mock_reorder_frame, mock_card_frame, mock_action_frame]

        result = view._build_task_card(mock_parent, index=0, position=1, task=task, channel_enabled=True)

        # The returned widget is the outer row frame
        assert result == mock_row_frame

        # Verify buttons were created inside reorder_frame with increased size (24x20)
        btn_calls = mock_btn_cls.call_args_list
        assert len(btn_calls) >= 2
        up_btn_kwargs = btn_calls[0][1]
        down_btn_kwargs = btn_calls[1][1]
        assert btn_calls[0][0][0] == mock_reorder_frame
        assert btn_calls[1][0][0] == mock_reorder_frame
        assert up_btn_kwargs.get("width") == 24
        assert up_btn_kwargs.get("height") == 20
        assert down_btn_kwargs.get("width") == 24
        assert down_btn_kwargs.get("height") == 20


def test_queue_card_title_and_stats_lines_display():
    view = QueueView.__new__(QueueView)
    view.app_controller = MagicMock()
    view.save_queue = MagicMock()
    view.refresh_list = MagicMock()
    view._stats = MagicMock(return_value=None)

    # 1. Single title test
    task_single = VideoTask(
        title="Stop Wasting Money on Meta Ads! Try This Strategy",
        url="https://youtube.com/watch?v=123",
        duration_seconds=480,
    )

    with patch("customtkinter.CTkCheckBox"), \
         patch("customtkinter.CTkLabel") as mock_label_cls, \
         patch("customtkinter.CTkFrame"), \
         patch("customtkinter.CTkButton"), \
         patch("customtkinter.CTkProgressBar"), \
         patch("customtkinter.CTkFont"), \
         patch("customtkinter.CTkEntry"):

        mock_parent = MagicMock()
        view._build_task_card(mock_parent, index=0, position=1, task=task_single, channel_enabled=True)

        # First label created is lbl (line 1 & line 2)
        lbl_call = mock_label_cls.call_args_list[0]
        text_arg = lbl_call[1].get("text")
        lines = text_arg.split("\n")
        assert len(lines) == 2
        # Line 1: Single title, no separator
        assert lines[0] == "#1. Stop Wasting Money on Meta Ads! Try This Strategy"
        # Line 2: Stats only, no "Titles:", no "Max Views:", no "Multiplier:"
        assert "Titles:" not in lines[1]
        assert "Max Views:" not in lines[1]
        assert "Multiplier:" not in lines[1]
        assert "Watchtime:" not in lines[1]
        assert "Watch Time:" not in lines[1]
        assert "Length: 8 min" in lines[1]
        assert "0s" in lines[1]

        # Second label created is stats_lbl (line 3)
        stats_lbl_call = mock_label_cls.call_args_list[1]
        line3_text = stats_lbl_call[1].get("text")
        assert "runs --" in line3_text
        assert "Length:" not in line3_text
        assert "watched · Last Played: Never" in line3_text

    # 2. Multiple titles test
    task_multi = VideoTask(
        title="Strategy A",
        title_2="Strategy B",
        title_3="Strategy C",
        url="https://youtube.com/watch?v=456",
        duration_seconds=300,
    )

    with patch("customtkinter.CTkCheckBox"), \
         patch("customtkinter.CTkLabel") as mock_label_cls, \
         patch("customtkinter.CTkFrame"), \
         patch("customtkinter.CTkButton"), \
         patch("customtkinter.CTkProgressBar"), \
         patch("customtkinter.CTkFont"), \
         patch("customtkinter.CTkEntry"):

        mock_parent = MagicMock()
        view._build_task_card(mock_parent, index=1, position=2, task=task_multi, channel_enabled=True)

        lbl_call = mock_label_cls.call_args_list[0]
        text_arg = lbl_call[1].get("text")
        lines = text_arg.split("\n")
        assert len(lines) == 2
        # Line 1: All titles joined with " | "
        assert lines[0] == "#2. Strategy A | Strategy B | Strategy C"
        # Line 2: Stats only, no "Titles:" prefix, displays Length and Watch Time ratio (without Watch Time text)
        assert "Titles:" not in lines[1]
        assert "Watchtime:" not in lines[1]
        assert "Length: 5 min" in lines[1]
        assert "Watch Time:" not in lines[1]
        assert "0s" in lines[1]


def test_queue_card_displays_video_watch_time_ratio():
    from src.models.watch_stats import StatRecord
    view = QueueView.__new__(QueueView)
    view.app_controller = MagicMock()
    view.save_queue = MagicMock()
    view.refresh_list = MagicMock()

    # 8 min video (480s) with 3x target (1440s), watched 7 min (420s)
    task = VideoTask(
        title="Stop Wasting Money on Meta Ads! Try This Strategy",
        url="https://youtube.com/watch?v=metaads",
        duration_seconds=480,
        target_multiplier=3.0,
        max_runs=5,
        published_at="2026-01-01T12:00:00Z",
    )
    rec = StatRecord(runs=1, watch_seconds=420.0, video_duration=480.0)
    store = MagicMock()
    store.find_for_task.return_value = rec
    view._stats = MagicMock(return_value=store)

    with patch("customtkinter.CTkCheckBox"), \
         patch("customtkinter.CTkLabel") as mock_label_cls, \
         patch("customtkinter.CTkFrame"), \
         patch("customtkinter.CTkButton"), \
         patch("customtkinter.CTkProgressBar"), \
         patch("customtkinter.CTkFont"), \
         patch("customtkinter.CTkEntry"):

        mock_parent = MagicMock()
        view._build_task_card(mock_parent, index=0, position=1, task=task, channel_enabled=True)

        lbl_call = mock_label_cls.call_args_list[0]
        text_arg = lbl_call[1].get("text")
        lines = text_arg.split("\n")
        assert len(lines) == 2
        assert lines[0] == "#1. Stop Wasting Money on Meta Ads! Try This Strategy"
        assert lines[1] == "Length: 8 min | 7 / 24 min (29%) | 01 Jan 2026"
        stats_lbl_call = mock_label_cls.call_args_list[1]
        line3_text = stats_lbl_call[1].get("text")
        assert "watched · Last Played: Never" in line3_text


def test_format_published_date():
    from src.ui.views.queue_view import format_published_date

    assert format_published_date("2026-01-01T15:30:00Z") == "01 Jan 2026"
    assert format_published_date("2026-01-01T15:30:00.000Z") == "01 Jan 2026"
    assert format_published_date("2026-01-01") == "01 Jan 2026"
    assert format_published_date("2025-10-15T08:00:00Z") == "15 Oct 2025"
    assert format_published_date("2026-11-07") == "07 Nov 2026"
    assert format_published_date("") == ""
    assert format_published_date(None) == ""


def test_queue_channel_buttons_rearranged_order(ctk_app):
    """Verify channel buttons in Video Queue and Shorts Queue are in order: Manage, Open Channel, Archive."""
    from unittest.mock import MagicMock
    import customtkinter as ctk
    from src.models.channel import Channel
    from src.models.video_task import VideoTask

    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.stats_store.find_for_task.return_value = MagicMock(runs=0, watch_seconds=0.0)
    app.on_queue_changed = MagicMock()
    app.current_state = "IDLE"
    app.queue_service = MagicMock()

    channel = Channel(name="OrderTestChannel", url="https://youtube.com/@ordertest", archived=False)

    for is_shorts in (False, True):
        view = QueueView(app, app, shorts_only=is_shorts)
        view.video_tasks = [
            VideoTask(title="Item 1", url="https://youtube.com/watch?v=1", channel="OrderTestChannel", is_short_override=is_shorts),
        ]
        view._channels = MagicMock(return_value=[channel])
        view.refresh_list()
        app.update_idletasks()

        # Find channel header container
        containers = [w for w in view.scroll_frame.winfo_children() if isinstance(w, ctk.CTkFrame)]
        assert len(containers) >= 1

        # The header is row 0 in container
        header = containers[0].grid_slaves(row=0, column=0)[0]
        # header_btns is column 2 (or 1) with sticky="e"
        btn_frames = [w for w in header.winfo_children() if isinstance(w, ctk.CTkFrame)]
        assert len(btn_frames) >= 1
        header_btns = btn_frames[0]

        # Inspect button children in header_btns
        buttons = [w for w in header_btns.winfo_children() if isinstance(w, ctk.CTkButton)]
        assert len(buttons) == 3, f"Expected 3 buttons in channel header, got {len(buttons)}"

        btn_texts = [b.cget("text") for b in buttons]
        # 1. Manage
        assert "Manage" in btn_texts[0], f"First button should be Manage, got {btn_texts[0]}"
        # 2. Open Channel
        assert "Open Channel" in btn_texts[1], f"Second button should be Open Channel, got {btn_texts[1]}"
        # 3. Archive
        assert "Archive" in btn_texts[2], f"Third button should be Archive, got {btn_texts[2]}"

        view.destroy()
