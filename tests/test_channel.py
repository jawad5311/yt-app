"""Tests for the Channel model, URL normalization, and video grouping."""

import pytest

from src.models.channel import (
    NO_CHANNEL_VALUE,
    UNCATEGORIZED_LABEL,
    Channel,
    VideoGroup,
    group_videos_by_channel,
    normalize_channel_url,
)
from src.models.video_task import VideoTask


def test_roundtrip_serialization():
    channel = Channel(name="Cooking", url="https://www.youtube.com/@cooking")
    assert Channel.from_dict(channel.to_dict()) == channel


def test_from_dict_tolerates_junk_input():
    assert Channel.from_dict(None) == Channel()
    assert Channel.from_dict({"name": "  Cooking  "}) == Channel(name="Cooking")


def test_normalize_url_adds_missing_scheme():
    assert normalize_channel_url("youtube.com/@x") == "https://youtube.com/@x"
    assert normalize_channel_url("  http://youtube.com/@x  ") == "http://youtube.com/@x"
    assert normalize_channel_url("") == ""


def test_parse_requires_a_name():
    with pytest.raises(ValueError, match="name is required"):
        Channel.parse("   ", "https://youtube.com/@x")


def test_parse_rejects_duplicate_names_case_insensitively():
    with pytest.raises(ValueError, match="already exists"):
        Channel.parse("cooking", "", existing=[Channel(name="Cooking")])


def test_parse_keeps_unique_names_and_normalizes_url():
    channel = Channel.parse("  Gaming ", "gaming.com", existing=[Channel(name="Cooking")])
    assert channel.name == "Gaming"
    assert channel.url == "https://gaming.com"


def test_no_videos_and_no_channels_yields_no_groups():
    assert group_videos_by_channel([], []) == []


def test_empty_channels_are_still_grouped():
    groups = group_videos_by_channel([], [Channel(name="Cooking")])
    assert [group.name for group in groups] == ["Cooking"]
    assert groups[0].videos == []


def test_groups_follow_channel_order_and_keep_original_indexes():
    channels = [Channel(name="A"), Channel(name="B"), Channel(name="C")]
    videos = [
        VideoTask(title="first", channel="C"),
        VideoTask(title="second", channel="A"),
        VideoTask(title="third", channel="C"),
    ]

    groups = group_videos_by_channel(videos, channels)
    assert [group.name for group in groups] == ["A", "B", "C"]
    assert [task.title for _, task in groups[0].videos] == ["second"]
    assert [index for index, _ in groups[2].videos] == [0, 2]
    assert [task.title for _, task in groups[2].videos] == ["first", "third"]


def test_unknown_and_missing_channels_go_to_uncategorized():
    channels = [Channel(name="A")]
    videos = [
        VideoTask(title="orphan", channel="Deleted Channel"),
        VideoTask(title="loose", channel=""),
        VideoTask(title="kept", channel="A"),
    ]

    groups = group_videos_by_channel(videos, channels)
    assert [group.name for group in groups] == ["A", UNCATEGORIZED_LABEL]
    assert groups[1].is_uncategorized is True
    assert groups[0].is_uncategorized is False
    assert [task.title for _, task in groups[1].videos] == ["orphan", "loose"]


def test_duplicate_channel_names_from_disk_are_collapsed():
    channels = [Channel(name="A"), Channel(name="A"), Channel()]
    groups = group_videos_by_channel([], channels)
    assert [group.name for group in groups] == ["A"]


def test_ui_sentinels_are_distinct():
    assert NO_CHANNEL_VALUE != UNCATEGORIZED_LABEL
    assert UNCATEGORIZED_LABEL != Channel().name


def test_group_count_property():
    groups = group_videos_by_channel([VideoTask(channel="A")], [Channel(name="A")])
    assert isinstance(groups[0], VideoGroup)
    assert groups[0].count == 1
