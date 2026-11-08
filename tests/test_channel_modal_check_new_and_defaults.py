from unittest.mock import MagicMock, patch
import pytest

from src.models.channel import Channel
from src.ui.views.channels_view import ChannelsView, FETCH_DATA_OPTIONS


def test_channel_modal_fetch_dropdowns_default_to_fetch_no_data():
    view = ChannelsView.__new__(ChannelsView)
    view.channels = [Channel(name="TestChan", url="https://youtube.com/@testchan")]
    view.app_controller = MagicMock()
    view.get_existing_video_counts_for_channel = MagicMock(return_value=(0, 0, 0))
    view.channel_stats_line = MagicMock(return_value="")
    view.refresh_list = MagicMock()
    view.save_channels = MagicMock()
    view._queue_view = MagicMock(return_value=None)

    created_option_menus = []

    with patch("customtkinter.CTkToplevel"), \
         patch("customtkinter.CTkFrame"), \
         patch("customtkinter.CTkLabel"), \
         patch("customtkinter.CTkButton"), \
         patch("customtkinter.CTkScrollableFrame"), \
         patch("customtkinter.CTkOptionMenu") as mock_menu:

        mock_menu.side_effect = lambda parent, values, width: (
            menu_mock := MagicMock(),
            created_option_menus.append((values, menu_mock)),
            menu_mock
        )[-1]

        view.open_channel_videos_modal(view.channels[0])

        # Expect at least two option menus created (videos to fetch & shorts to fetch)
        assert len(created_option_menus) >= 2
        
        # Check that both were initialized with FETCH_DATA_OPTIONS and set to "Fetch No Data"
        values_v, menu_v = created_option_menus[0]
        assert values_v == FETCH_DATA_OPTIONS
        menu_v.set.assert_called_with("Fetch No Data")

        values_s, menu_s = created_option_menus[1]
        assert values_s == FETCH_DATA_OPTIONS
        menu_s.set.assert_called_with("Fetch No Data")


def test_channel_modal_has_check_new_button_beside_open_channel():
    view = ChannelsView.__new__(ChannelsView)
    channel = Channel(name="CoolChannel", url="https://youtube.com/@coolchannel")
    view.channels = [channel]
    view.app_controller = MagicMock()
    view.get_existing_video_counts_for_channel = MagicMock(return_value=(5, 2, 7))
    view.channel_stats_line = MagicMock(return_value="")
    view.save_channels = MagicMock()
    view.refresh_list = MagicMock()
    view._queue_view = MagicMock(return_value=None)
    view._widget_exists = MagicMock(return_value=True)

    button_configs = {}

    with patch("customtkinter.CTkToplevel"), \
         patch("customtkinter.CTkFrame"), \
         patch("customtkinter.CTkLabel"), \
         patch("customtkinter.CTkScrollableFrame"), \
         patch("customtkinter.CTkOptionMenu"), \
         patch("customtkinter.CTkButton") as mock_btn:

        def fake_button(parent, text, **kwargs):
            btn = MagicMock()
            btn.cget = lambda key: text if key == "text" else kwargs.get(key)
            button_configs[text] = (btn, kwargs.get("command"))
            return btn

        mock_btn.side_effect = fake_button

        view.open_channel_videos_modal(channel)

        assert "Open Channel" in button_configs
        assert "Check New" in button_configs

        check_btn, check_cmd = button_configs["Check New"]
        open_btn, open_cmd = button_configs["Open Channel"]

        assert check_btn is not None
        assert open_btn is not None
        assert callable(check_cmd)

        # Verify executing check_cmd triggers channel check
        with patch("src.ui.views.channels_view.fetch_channel_data") as mock_fetch, \
             patch("tkinter.messagebox.showinfo") as mock_info:

            # Mock fetch returns higher video count than existing (5 vids, 2 shorts)
            mock_res = MagicMock()
            mock_res.error = None
            mock_res.channel_title = "CoolChannel"
            mock_res.total_videos = 10  # 5 new videos!
            mock_res.total_shorts = 2
            mock_fetch.return_value = mock_res

            # When jobs.submit is called, immediately run the work & done callback
            def fake_submit(work, done, error=None, current=None):
                res = work()
                done(res)

            view.app_controller.background_jobs.submit.side_effect = fake_submit

            check_cmd()

            mock_fetch.assert_called_once_with(
                "https://youtube.com/@coolchannel",
                video_limit=None,
                shorts_limit=None,
                config_mgr=view.app_controller.config_mgr,
            )
            assert channel.has_new_videos is True
            assert channel.total_videos == 10
            view.save_channels.assert_called()
            mock_info.assert_called()
            assert "New content is published" in mock_info.call_args[0][1]
