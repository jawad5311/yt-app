"""Unit tests verifying that total long-form videos and shorts are fetched, stored, and displayed."""

import json
from unittest.mock import MagicMock, patch

import customtkinter as ctk

from src.models.channel import Channel
from src.ui.views.channels_view import ChannelsView
from src.utils.channel_fetcher import fetch_channel_data_api


def test_channel_model_stores_and_serializes_video_totals():
    ch = Channel(name="Test Channel", url="https://youtube.com/@test", total_videos=142, total_shorts=58)
    d = ch.to_dict()
    assert d["total_videos"] == 142
    assert d["total_shorts"] == 58

    reloaded = Channel.from_dict(d)
    assert reloaded.total_videos == 142
    assert reloaded.total_shorts == 58
    assert ch.total_combined_videos == 200
    assert reloaded.total_combined_videos == 200


def test_fetch_channel_data_api_calculates_totals():
    channel_lookup_resp = {
        "items": [
            {
                "id": "UC9876543210987654321098",
                "snippet": {"title": "Sample Channel"},
                "contentDetails": {
                    "relatedPlaylists": {"uploads": "UU9876543210987654321098"}
                },
                "statistics": {
                    "videoCount": "200"
                }
            }
        ]
    }

    uulf_resp = {
        "pageInfo": {"totalResults": 150},
        "items": [
            {"contentDetails": {"videoId": "v1"}, "snippet": {"title": "V1"}},
        ]
    }

    uush_resp = {
        "pageInfo": {"totalResults": 50},
        "items": [
            {"contentDetails": {"videoId": "s1"}, "snippet": {"title": "S1"}},
        ]
    }

    def mock_urlopen(req, timeout=15):
        url = req.full_url
        mock_resp = MagicMock()
        if "channels?" in url:
            mock_resp.read.return_value = json.dumps(channel_lookup_resp).encode("utf-8")
        elif "playlistId=UULF" in url:
            mock_resp.read.return_value = json.dumps(uulf_resp).encode("utf-8")
        elif "playlistId=UUSH" in url:
            mock_resp.read.return_value = json.dumps(uush_resp).encode("utf-8")
        else:
            mock_resp.read.return_value = b"{}"
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        res = fetch_channel_data_api(
            channel_input="@samplechannel",
            api_key="AIzaSyDummyKeyForTesting",
            video_limit=1,
            shorts_limit=1,
        )
        assert res.channel_title == "Sample Channel"
        assert res.total_videos == 150
        assert res.total_shorts == 50


def test_channels_view_displays_totals_in_card_and_modal(ctk_app):
    app = ctk_app
    app.config_mgr = MagicMock()
    app.config_mgr.get_channels.return_value = []
    app.config_mgr.get_video_queue.return_value = []
    app.stats_store = MagicMock()
    app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)

    view = ChannelsView(app, app)
    channel = Channel(
        name="Showcase Channel",
        url="https://youtube.com/@showcase",
        total_videos=1420,
        total_shorts=580,
    )
    view.channels = [channel]
    view.refresh_list()

    # Verify channel card displays totals
    cards = [w for w in view.scroll_frame.winfo_children() if isinstance(w, ctk.CTkFrame)]
    assert len(cards) >= 1
    labels = []
    def _collect_labels(w):
        for child in w.winfo_children():
            if isinstance(child, ctk.CTkLabel):
                labels.append(child.cget("text"))
            _collect_labels(child)

    for card in cards:
        _collect_labels(card)

    card_text = "\n".join(labels)
    assert "Channel Total:" in card_text
    assert "1,420 Videos" in card_text
    assert "580 Shorts" in card_text
    assert "2,000 Total Combined Videos" in card_text

    # Verify modal header also displays totals
    view.open_channel_videos_modal(channel)
    modal = None
    for child in view.winfo_children():
        if isinstance(child, ctk.CTkToplevel):
            modal = child
            break
    assert modal is not None

    modal_labels = []
    def _scan(w):
        for c in w.winfo_children():
            if isinstance(c, ctk.CTkLabel):
                modal_labels.append(c.cget("text"))
            _scan(c)
    _scan(modal)

    modal_text = "\n".join(modal_labels)
    assert "Channel Total:" in modal_text
    assert "1,420 Videos" in modal_text
    assert "580 Shorts" in modal_text
    assert "2,000 Total Combined Videos" in modal_text

    modal.destroy()
    view.destroy()
