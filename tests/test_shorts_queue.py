from unittest.mock import MagicMock

from src.models.channel import Channel, group_videos_by_channel
from src.models.video_task import VideoTask
from src.utils.helpers import clean_youtube_url


def test_video_task_is_short_property():
    long_task = VideoTask(
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title="Never Gonna Give You Up",
    )
    assert long_task.is_short is False

    short_task = VideoTask(
        url="https://www.youtube.com/shorts/3jz_b1YvQwk",
        title="Quick Cat Video",
    )
    assert short_task.is_short is True

    empty_url_task = VideoTask(title="Query Only Video")
    assert empty_url_task.is_short is False


def test_clean_youtube_url_as_short():
    # Long-form video URL with extra parameters cleaned to watch
    raw_watch = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s&feature=share"
    assert clean_youtube_url(raw_watch, as_short=False) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    # Converting watch URL to canonical shorts URL
    assert clean_youtube_url(raw_watch, as_short=True) == "https://www.youtube.com/shorts/dQw4w9WgXcQ"

    # Cleaning a shorts URL with query parameters
    raw_short = "https://www.youtube.com/shorts/3jz_b1YvQwk?feature=share"
    assert clean_youtube_url(raw_short, as_short=True) == "https://www.youtube.com/shorts/3jz_b1YvQwk"
    # When as_short=False, standard cleaner converts to watch?v= format
    assert clean_youtube_url(raw_short, as_short=False) == "https://www.youtube.com/watch?v=3jz_b1YvQwk"


def test_channel_grouping_shorts_and_videos():
    channel1 = Channel(name="Tech Channel", url="https://www.youtube.com/@tech")
    channel2 = Channel(name="Gaming Channel", url="https://www.youtube.com/@gaming")

    tasks = [
        VideoTask(url="https://www.youtube.com/watch?v=vid1", title="Tech Video 1", channel="Tech Channel"),
        VideoTask(url="https://www.youtube.com/shorts/short1", title="Tech Short 1", channel="Tech Channel"),
        VideoTask(url="https://www.youtube.com/shorts/short2", title="Gaming Short 1", channel="Gaming Channel"),
        VideoTask(url="https://www.youtube.com/watch?v=vid2", title="Gaming Video 1", channel="Gaming Channel"),
    ]

    groups = group_videos_by_channel(tasks, [channel1, channel2])

    # Filter for Videos Queue
    videos_by_group = {
        g.name: [(idx, t) for (idx, t) in g.videos if not t.is_short]
        for g in groups
    }
    assert len(videos_by_group["Tech Channel"]) == 1
    assert videos_by_group["Tech Channel"][0][1].title == "Tech Video 1"
    assert len(videos_by_group["Gaming Channel"]) == 1
    assert videos_by_group["Gaming Channel"][0][1].title == "Gaming Video 1"

    # Filter for Shorts Queue
    shorts_by_group = {
        g.name: [(idx, t) for (idx, t) in g.videos if t.is_short]
        for g in groups
    }
    assert len(shorts_by_group["Tech Channel"]) == 1
    assert shorts_by_group["Tech Channel"][0][1].title == "Tech Short 1"
    assert len(shorts_by_group["Gaming Channel"]) == 1
    assert shorts_by_group["Gaming Channel"][0][1].title == "Gaming Short 1"


def test_importing_shorts_updates_shorts_queue_view():
    from src.ui.views.queue_view import QueueView

    mock_app = MagicMock()
    mock_app.config_mgr = MagicMock()
    mock_app.config_mgr.get_video_queue.return_value = []
    mock_app.config_mgr.get_channels.return_value = []
    mock_app.stats_store = MagicMock()
    mock_app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)

    # Instantiate shorts_queue view
    view = QueueView.__new__(QueueView)
    view.shorts_only = True
    view.video_tasks = []
    view.collapsed_groups = set()
    view.app_controller = mock_app
    view.scroll_frame = MagicMock()
    view.scroll_frame.winfo_children.return_value = []
    view._channels = MagicMock(return_value=[])
    view._render_group = MagicMock()

    # Initially empty
    assert len([t for t in view.video_tasks if t.is_short]) == 0

    # Imported shorts are added to app_controller.queued_video_tasks
    imported_short = VideoTask(
        url="https://www.youtube.com/shorts/imported_short1",
        title="Imported Short 1",
        channel="MyChannel",
    )
    mock_app.queued_video_tasks = [imported_short]

    # Calling refresh_list should sync from app_controller.queued_video_tasks
    view.refresh_list()

    matching = [t for t in view.video_tasks if t.is_short]
    assert len(matching) == 1
    assert matching[0].title == "Imported Short 1"
    assert matching[0].url == "https://www.youtube.com/shorts/imported_short1"
    view._render_group.assert_called_once()


def test_queue_views_preserve_tasks_on_startup_and_refresh():
    from src.ui.views.queue_view import QueueView

    mock_app = MagicMock()
    mock_app.config_mgr = MagicMock()
    saved_tasks = [
        VideoTask(url="https://www.youtube.com/watch?v=long1", title="Long Video 1", channel="Chan1"),
        VideoTask(url="https://www.youtube.com/shorts/short1", title="Short Video 1", channel="Chan1"),
    ]
    mock_app.config_mgr.get_video_queue.return_value = [t.to_dict() for t in saved_tasks]
    mock_app.config_mgr.get_channels.return_value = []
    mock_app.queued_video_tasks = list(saved_tasks)
    mock_app.stats_store = MagicMock()
    mock_app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)

    # Initialize video queue view
    video_view = QueueView.__new__(QueueView)
    video_view.shorts_only = False
    video_view.video_tasks = []
    video_view.collapsed_groups = set()
    video_view.app_controller = mock_app
    video_view.scroll_frame = MagicMock()
    video_view.scroll_frame.winfo_children.return_value = []
    video_view._channels = MagicMock(return_value=[])
    video_view._render_group = MagicMock()

    video_view.load_initial_queue()
    assert len(video_view.video_tasks) == 2
    video_matching = [t for t in video_view.video_tasks if not t.is_short]
    assert len(video_matching) == 1
    assert video_matching[0].title == "Long Video 1"

    # Initialize shorts queue view
    shorts_view = QueueView.__new__(QueueView)
    shorts_view.shorts_only = True
    shorts_view.video_tasks = []
    shorts_view.collapsed_groups = set()
    shorts_view.app_controller = mock_app
    shorts_view.scroll_frame = MagicMock()
    shorts_view.scroll_frame.winfo_children.return_value = []
    shorts_view._channels = MagicMock(return_value=[])
    shorts_view._render_group = MagicMock()

    shorts_view.load_initial_queue()
    assert len(shorts_view.video_tasks) == 2
    shorts_matching = [t for t in shorts_view.video_tasks if t.is_short]
    assert len(shorts_matching) == 1
    assert shorts_matching[0].title == "Short Video 1"

    # Refreshing either view preserves their tasks
    video_view.refresh_list()
    assert len(video_view.video_tasks) == 2
    shorts_view.refresh_list()
    assert len(shorts_view.video_tasks) == 2


def test_batched_card_rendering_progresses_smoothly():
    from src.ui.views.queue_view import QueueView
    from src.models.video_task import VideoTask
    from src.models.channel import Channel, VideoGroup

    view = QueueView.__new__(QueueView)
    view._pending_card_queue = []
    view._render_job = None
    view._build_task_card = MagicMock(return_value=MagicMock())
    view.after = MagicMock(return_value="job_123")

    mock_container = MagicMock()
    mock_container.winfo_exists.return_value = True
    mock_group = VideoGroup(channel=Channel(name="Chan1", enabled=True), videos=[])

    # Enqueue 30 card items
    for i in range(30):
        task = VideoTask(url=f"https://www.youtube.com/shorts/s{i}", title=f"Short {i}")
        view._pending_card_queue.append((
            mock_container,
            i,
            task,
            i,
            mock_group,
            i > 0,
            i < 29,
            MagicMock(),
            MagicMock(),
        ))

    assert len(view._pending_card_queue) == 30

    # Process first batch of 15
    view._process_card_render_batch(batch_size=15)

    assert view._build_task_card.call_count == 15
    assert len(view._pending_card_queue) == 15
    view.after.assert_called_once()
    assert view._render_job == "job_123"


def test_cancel_pending_render_cleans_up():
    from src.ui.views.queue_view import QueueView

    view = QueueView.__new__(QueueView)
    view._pending_card_queue = ["item1", "item2"]
    view._render_job = "job_456"
    view.after_cancel = MagicMock()

    view._cancel_pending_render()

    view.after_cancel.assert_called_once_with("job_456")
    assert view._render_job is None
    assert len(view._pending_card_queue) == 0


def test_show_view_avoids_redundant_refresh_when_unchanged():
    from src.ui.app import YTBotApp
    from src.models.video_task import VideoTask

    app = YTBotApp.__new__(YTBotApp)
    app.current_view_name = "dashboard"
    app._shorts_queue_needs_refresh = False

    shared_tasks = [
        VideoTask(url="https://www.youtube.com/shorts/test1", title="Short 1"),
    ]
    app.queued_video_tasks = shared_tasks

    mock_view = MagicMock()
    mock_view.video_tasks = shared_tasks
    mock_child = MagicMock()
    mock_view.scroll_frame.winfo_children.return_value = [mock_child]

    mock_btn = MagicMock()
    app.views = {"shorts_queue": mock_view}
    app.nav_buttons = {"shorts_queue": mock_btn}

    app.show_view("shorts_queue")

    # View was raised
    mock_view.tkraise.assert_called_once()
    # But refresh_list was NOT called because tasks are identical and children exist
    mock_view.refresh_list.assert_not_called()


def test_scroll_down_enabled_during_progressive_loading(ctk_app):
    import customtkinter as ctk
    from src.utils.scroll_helper import setup_smooth_scrolling

    sf = ctk.CTkScrollableFrame(ctk_app)
    sf.pack(fill="both", expand=True)
    setup_smooth_scrolling(sf)

    # Initial 15 items
    for i in range(15):
        ctk.CTkLabel(sf, text=f"Item {i}", height=40).pack()

    sf.update_idletasks()
    canvas = sf._parent_canvas
    y0 = canvas.yview()

    class FakeEvent:
        def __init__(self, delta, widget):
            self.delta = delta
            self.widget = widget
            self.x_root = 100
            self.y_root = 100

    # Fire downward mousewheel scroll
    sf._smooth_mousewheel(FakeEvent(-120, canvas))
    ctk_app.update()

    y1 = canvas.yview()
    assert y1[0] > y0[0]

    # Add next batch and update idle tasks
    for i in range(15, 30):
        ctk.CTkLabel(sf, text=f"Item {i}", height=40).pack()
    sf.update_idletasks()

    # Continue downward mousewheel scroll
    sf._smooth_mousewheel(FakeEvent(-120, canvas))
    ctk_app.update()

    y2 = canvas.yview()
    assert y2[0] > y1[0]

    sf.destroy()


def test_video_queue_shares_progressive_batch_rendering_and_smooth_scrolling():
    from src.ui.views.queue_view import QueueView
    from src.models.video_task import VideoTask
    from src.models.channel import Channel

    mock_app = MagicMock()
    mock_app.config_mgr = MagicMock()
    # 20 long videos
    long_tasks = [
        VideoTask(url=f"https://www.youtube.com/watch?v=v{i}", title=f"Video {i}", channel="TestChan")
        for i in range(20)
    ]
    mock_app.config_mgr.get_video_queue.return_value = [t.to_dict() for t in long_tasks]
    mock_app.config_mgr.get_channels.return_value = [Channel(name="TestChan", enabled=True).to_dict()]
    mock_app.queued_video_tasks = list(long_tasks)
    mock_app.stats_store = MagicMock()
    mock_app.stats_store.channel_totals.return_value = MagicMock(runs=0, watch_seconds=0.0)

    # Video Queue View has shorts_only=False
    video_view = QueueView.__new__(QueueView)
    video_view.shorts_only = False
    video_view.video_tasks = list(long_tasks)
    video_view.collapsed_groups = set()
    video_view._pending_card_queue = []
    video_view._render_job = None
    video_view._loading_indicator = None
    video_view.app_controller = mock_app
    video_view.scroll_frame = MagicMock()
    video_view.scroll_frame.winfo_children.return_value = []
    video_view._channels = MagicMock(return_value=[Channel(name="TestChan", enabled=True)])
    video_view._render_group = MagicMock()
    video_view._build_task_card = MagicMock(return_value=MagicMock())
    video_view.after = MagicMock(return_value="job_long_1")

    # Refreshing Video Queue triggers group rendering
    video_view.refresh_list()
    video_view._render_group.assert_called_once()
    filtered = video_view._render_group.call_args[0][0]
    assert len(filtered.videos) == 20

    # Enqueue the 20 long videos into _pending_card_queue and verify batching
    mock_container = MagicMock()
    mock_container.winfo_exists.return_value = True
    for i, t in enumerate(long_tasks):
        video_view._pending_card_queue.append((
            mock_container, i, t, i, filtered, i > 0, i < 19, MagicMock(), MagicMock()
        ))
    video_view._process_card_render_batch(batch_size=15)

    # 15 rendered immediately, 5 queued for subsequent batch
    assert video_view._build_task_card.call_count == 15
    assert len(video_view._pending_card_queue) == 5
    video_view.after.assert_called_once()
