"""Tests for VideoTask duration parsing and serialization."""

import pytest

from src.models.video_task import DEFAULT_WATCHTIME_DURATION, WATCHTIME_OPTIONS, VideoTask


@pytest.mark.parametrize(
    "label,expected_seconds",
    [
        ("30 Seconds", 30),
        ("1 Minute", 60),
        ("2 Minutes", 120),
        ("5 Minutes", 300),
        ("10 Minutes", 600),
        ("15 Minutes", 900),
        ("30 Minutes", 1800),
        ("45 Minutes", 2700),
        ("1 Hour", 3600),
        ("2 Hours", 7200),
        ("3 Hours", 10800),
        ("6 Hours", 21600),
        ("12 Hours", 43200),
        ("24 Hours", 86400),
    ],
)
def test_parse_duration_seconds(label, expected_seconds):
    assert VideoTask(watchtime_duration=label).parse_duration_seconds() == expected_seconds


def test_full_video_returns_none():
    task = VideoTask(watchtime_duration="Full Video")
    assert task.parse_duration_seconds() is None
    assert task.is_full_video() is True


def test_fixed_duration_is_not_full_video():
    assert VideoTask(watchtime_duration="5 Minutes").is_full_video() is False


def test_unknown_duration_falls_back_to_five_minutes():
    assert VideoTask(watchtime_duration="banana").parse_duration_seconds() == 300


def test_default_duration_matches_constant():
    assert VideoTask().watchtime_duration == DEFAULT_WATCHTIME_DURATION
    assert VideoTask().is_full_video() is True
    assert VideoTask().parse_duration_seconds() is None


def test_options_include_full_video_and_default():
    assert "Full Video" in WATCHTIME_OPTIONS
    assert DEFAULT_WATCHTIME_DURATION in WATCHTIME_OPTIONS


def test_roundtrip_serialization():
    task = VideoTask(
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title="Sample",
        keywords="sample keywords",
        watchtime_duration="1 Hour",
    )
    restored = VideoTask.from_dict(task.to_dict())
    assert restored == task


def test_from_dict_falls_back_to_defaults():
    restored = VideoTask.from_dict({})
    assert restored.watchtime_duration == DEFAULT_WATCHTIME_DURATION
    assert restored.url == ""
    assert restored.keywords == ""


def test_from_dict_legacy_keywords_from_title():
    restored = VideoTask.from_dict({"title": "Legacy Title"})
    assert restored.keywords == "Legacy Title"


def test_channel_defaults_to_empty():
    assert VideoTask().channel == ""
    assert VideoTask.from_dict({}).channel == ""
    assert VideoTask.from_dict({"channel": None}).channel == ""


def test_from_dict_parses_string_short_flag_as_a_boolean():
    assert VideoTask.from_dict({"is_short": "false"}).is_short is False
    assert VideoTask.from_dict({"is_short": "true"}).is_short is True


def test_channel_roundtrips():
    task = VideoTask(url="https://youtu.be/abcdefghijk", channel="Cooking")
    assert task.to_dict()["channel"] == "Cooking"
    assert VideoTask.from_dict(task.to_dict()) == task


def test_video_duration_metadata_storage():
    task = VideoTask(
        url="https://www.youtube.com/watch?v=abcdefghijk",
        title="Sample Video",
        duration_seconds=500,
        duration_text="8:20",
    )
    serialized = task.to_dict()
    assert serialized["duration_seconds"] == 500
    assert serialized["duration_text"] == "8:20"

    restored = VideoTask.from_dict(serialized)
    assert restored.duration_seconds == 500
    assert restored.duration_text == "8:20"
    assert restored == task


@pytest.mark.parametrize("target,length", [(1.0, 1000), (1.04, 1000), (2.5, 600)])
def test_multiplier_target_uses_exact_watch_seconds(target, length):
    task = VideoTask(target_multiplier=target, duration_seconds=length)
    required = target * length
    assert not task.is_target_reached(watch_seconds=required - 0.001)
    assert task.is_target_reached(watch_seconds=required)
    assert task.is_target_reached(watch_seconds=required + 0.001)


def test_rounded_display_does_not_satisfy_one_times_target():
    task = VideoTask(target_multiplier=1.0, duration_seconds=1000)
    assert not task.is_target_reached(watch_seconds=951)
