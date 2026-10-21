"""Tests for the Channel model, URL normalization, and video grouping."""

import pytest

from src.models.channel import (
    NO_CHANNEL_VALUE,
    UNCATEGORIZED_LABEL,
    Channel,
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
    assert groups[0].count == 1


def test_archived_channel_serialization():
    ch = Channel(name="Oldies", archived=True)
    d = ch.to_dict()
    assert d.get("archived") is True
    loaded = Channel.from_dict(d)
    assert loaded.archived is True


def test_archived_channels_excluded_from_active_groups():
    active_ch = Channel(name="Active")
    archived_ch = Channel(name="Archived", archived=True)
    v1 = VideoTask(title="Active Video", channel="Active")
    v2 = VideoTask(title="Archived Video", channel="Archived")
    v3 = VideoTask(title="Orphan Video", channel="Deleted")

    # When include_archived is False (default):
    groups = group_videos_by_channel([v1, v2, v3], [active_ch, archived_ch], include_archived=False)
    # Archived channel and its video must NOT be in active groups, and NOT in uncategorized
    group_names = [g.name for g in groups]
    assert "Active" in group_names
    assert "Archived" not in group_names
    assert UNCATEGORIZED_LABEL in group_names

    # Verify uncategorized contains ONLY the orphan video, not the archived video
    uncat = next(g for g in groups if g.is_uncategorized)
    assert [task.title for _, task in uncat.videos] == ["Orphan Video"]


def test_is_video_task_runnable_for_archived_channel():
    from src.models.channel import is_video_task_runnable

    active_ch = Channel(name="Active", enabled=True, archived=False)
    archived_ch = Channel(name="Archived", enabled=True, archived=True)

    task_active = VideoTask(title="v1", channel="Active", enabled=True)
    task_archived = VideoTask(title="v2", channel="Archived", enabled=True)

    assert is_video_task_runnable(task_active, [active_ch, archived_ch]) is True
    assert is_video_task_runnable(task_archived, [active_ch, archived_ch]) is False
