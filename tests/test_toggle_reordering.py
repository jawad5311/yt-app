from unittest.mock import MagicMock
from src.models.channel import Channel, group_videos_by_channel, is_video_task_runnable
from src.models.video_task import VideoTask
from src.automation.watching_worker import WatchingWorker
from src.models.account import Account


def test_unchecking_channel_preserves_video_checked_states():
    # Videos under a channel should maintain their own checked states
    v1 = VideoTask(title="Vid 1", channel="CarHacker", enabled=True)
    v2 = VideoTask(title="Vid 2", channel="CarHacker", enabled=False)
    ch = Channel(name="CarHacker", enabled=False)  # Channel is unchecked / disabled

    # Model level: individual video tasks retain their enabled flag
    assert v1.enabled is True
    assert v2.enabled is False

    # Execution level: Neither video is runnable because channel is disabled
    assert is_video_task_runnable(v1, [ch]) is False
    assert is_video_task_runnable(v2, [ch]) is False


def test_watching_worker_rejects_candidate_from_disabled_channel():
    cfg_mgr = MagicMock()
    # Queue contains a video task for a disabled channel
    v = VideoTask(
        url="https://www.youtube.com/watch?v=11111111111",
        title="Vid 1",
        channel="DisabledChannel",
        enabled=True,
    )
    cfg_mgr.get_video_queue.return_value = [v.to_dict()]

    # Worker only has active channels
    active_ch = Channel(name="ActiveChannel", enabled=True)
    worker = WatchingWorker(
        account=Account(email="test@gmail.com", password="pwd"),
        channels=[active_ch],
        config_mgr=cfg_mgr,
    )

    eligible, task, reason = worker.is_candidate_eligible(
        full_url="https://www.youtube.com/watch?v=11111111111",
        title="Vid 1",
        channel_name="DisabledChannel",
    )
    assert eligible is False
    assert "disabled or not active" in reason


def test_channel_unchecking_moves_channel_and_videos_to_bottom():
    ch1 = Channel(name="Channel A", enabled=True)
    ch2 = Channel(name="Channel B", enabled=True)
    channels = [ch1, ch2]

    v_a1 = VideoTask(title="A1", channel="Channel A")
    v_a2 = VideoTask(title="A2", channel="Channel A")
    v_b1 = VideoTask(title="B1", channel="Channel B")
    video_tasks = [v_a1, v_a2, v_b1]

    # Simulate unchecking Channel A
    ch1.enabled = False
    channels.remove(ch1)
    channels.append(ch1)

    # Move its videos to the end of video_tasks
    ch_name = ch1.name.lower()
    non_ch = [t for t in video_tasks if (t.channel or "").strip().lower() != ch_name]
    ch_vids = [t for t in video_tasks if (t.channel or "").strip().lower() == ch_name]
    video_tasks = non_ch + ch_vids

    # Channel B should now be first, Channel A at the bottom
    assert [c.name for c in channels] == ["Channel B", "Channel A"]
    assert [t.title for t in video_tasks] == ["B1", "A1", "A2"]

    # In grouped view, Channel B group renders first, Channel A group renders last
    groups = group_videos_by_channel(video_tasks, channels)
    assert [g.name for g in groups] == ["Channel B", "Channel A"]


def test_unchecking_video_moves_to_channel_bottom():
    v1 = VideoTask(title="V1", channel="CarHacker", enabled=True)
    v2 = VideoTask(title="V2", channel="CarHacker", enabled=True)
    v3 = VideoTask(title="V3", channel="CarHacker", enabled=True)
    v_other = VideoTask(title="Other", channel="OtherChannel", enabled=True)
    video_tasks = [v1, v2, v3, v_other]

    channels = [Channel(name="CarHacker"), Channel(name="OtherChannel")]
    groups = group_videos_by_channel(video_tasks, channels)
    car_group = groups[0]

    # Simulate unchecking V1
    v1.enabled = False
    curr_real = video_tasks.index(v1)
    last_task = car_group.videos[-1][1]  # v3
    last_real = video_tasks.index(last_task)

    t = video_tasks.pop(curr_real)
    last_real = video_tasks.index(last_task)
    video_tasks.insert(last_real + 1, t)

    # Within CarHacker, V1 is now after V3, but before OtherChannel
    assert [t.title for t in video_tasks] == ["V2", "V3", "V1", "Other"]

    # Re-grouping reflects this order
    new_groups = group_videos_by_channel(video_tasks, channels)
    assert [t.title for _, t in new_groups[0].videos] == ["V2", "V3", "V1"]
