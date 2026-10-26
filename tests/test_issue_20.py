"""Network workers return data; only the main thread may access Tk or UI models."""

import queue
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.config.manager import ConfigManager
from src.models.channel import Channel
from src.ui.app import YTBotApp
from src.ui.background_jobs import BackgroundJobs
from src.ui.views import channels_view as module
from src.ui.views.channels_view import ChannelsView
from src.utils.channel_fetcher import ChannelFetchResult


def await_results(jobs, count=1):
    """Wait for actual worker completion without running a UI callback."""
    pending = []
    terminals = 0
    while terminals < count:
        event = jobs._results.get(timeout=3)
        pending.append(event)
        if event[1] != "progress":
            terminals += 1
    for event in pending:
        jobs._results.put(event)
    return pending


@pytest.fixture
def ui(monkeypatch, tmp_path):
    owner = threading.get_ident()
    created = []

    def main_only():
        assert threading.get_ident() == owner, "Tk accessed from a network worker"

    class Widget:
        def __init__(self, parent=None, **options):
            main_only()
            self.parent = parent
            self.options = options
            self.value = options.get("value", "")
            self.alive = True
            self.bindings = {}
            created.append(self)

        def check(self):
            main_only()
            assert self.alive, "Tk accessed after its dialog was destroyed"

        def winfo_exists(self):
            main_only()
            return self.alive

        def configure(self, **options):
            self.check()
            self.options.update(options)

        def get(self):
            self.check()
            variable = self.options.get("variable")
            return variable.get() if variable is not None else self.value

        def set(self, value):
            self.check()
            self.value = value

        def insert(self, index, value):
            self.check()
            self.value = value

        def delete(self, *args):
            self.check()
            self.value = ""

        def bind(self, event, callback):
            self.check()
            self.bindings[event] = callback

        def destroy(self):
            self.check()
            self.alive = False
            for child in created:
                if child.parent is self:
                    child.alive = False

        def after(self, *args):
            raise AssertionError("Network workers must not schedule Tk callbacks")

        def action(self, *args, **kwargs):
            self.check()

        title = geometry = grab_set = pack = grid = start = stop = focus_set = pack_forget = action

    for name in ("CTkToplevel", "CTkLabel", "CTkProgressBar", "CTkFrame", "CTkEntry",
                 "CTkButton", "CTkCheckBox", "CTkOptionMenu", "BooleanVar", "StringVar"):
        monkeypatch.setattr(module.ctk, name, Widget)
    monkeypatch.setattr(module.ctk, "CTkFont", lambda **kwargs: main_only())
    notices = []

    def notice(kind):
        def show(*args, **kwargs):
            main_only()
            notices.append((kind, args))
        return show

    for name in ("showinfo", "showerror", "showwarning"):
        monkeypatch.setattr(module.messagebox, name, notice(name))
    jobs = BackgroundJobs()
    config = ConfigManager(tmp_path / "settings.json")
    queue_view = SimpleNamespace(refresh_list=MagicMock(side_effect=main_only),
                                 reassign_channel=MagicMock(side_effect=lambda *a: main_only()))
    app = SimpleNamespace(background_jobs=jobs, config_mgr=config, queued_video_tasks=[],
                          views={"queue": queue_view}, update_dashboard_stats=MagicMock(side_effect=main_only))
    view = ChannelsView.__new__(ChannelsView)
    view.app_controller = app
    view.channels = [Channel(name="Old", url="https://youtube.com/@old")]
    view.winfo_exists = lambda: (main_only() or True)
    view.refresh_list = MagicMock(side_effect=main_only)
    view.load_channels = MagicMock(side_effect=main_only)
    imports = []

    def import_modal(**kwargs):
        main_only()
        imports.append(kwargs)

    monkeypatch.setattr(module, "ChannelImportModal", import_modal)

    def network(result):
        def fetch(*args, **kwargs):
            assert threading.get_ident() != owner, "Network request blocked the UI thread"
            if isinstance(result, Exception):
                raise result
            return result
        return fetch

    return SimpleNamespace(view=view, app=app, jobs=jobs, created=created, Widget=Widget,
                           notices=notices, imports=imports, network=network, owner=owner)


def test_job_results_and_progress_only_run_on_owner_thread():
    jobs = BackgroundJobs()
    owner = threading.get_ident()
    seen = []

    def work(report):
        assert threading.get_ident() != owner
        report("halfway")
        return "finished"

    def callback(value):
        seen.append((value, threading.get_ident()))

    jobs.submit(work, done=callback, error=callback, progress=callback)
    await_results(jobs)
    assert seen == []
    jobs.drain()
    assert seen == [("halfway", owner), ("finished", owner)]


def test_worker_cannot_drain_ui_callbacks():
    jobs = BackgroundJobs()
    errors = []

    def wrong_thread():
        try:
            jobs.drain()
        except RuntimeError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=wrong_thread)
    thread.start()
    thread.join(timeout=3)
    assert errors == ["Background job callbacks must run on the UI thread"]


def test_cancelled_stale_and_failed_callbacks_do_not_stop_other_jobs(caplog):
    jobs = BackgroundJobs()
    seen = []
    cancelled = jobs.submit(lambda report: "cancelled", done=seen.append, error=seen.append)
    jobs.cancel(cancelled)
    jobs.submit(lambda report: "stale", done=seen.append, error=seen.append,
                current=lambda: False, discard=lambda: seen.append("discarded"))

    def broken(value):
        raise RuntimeError("broken callback")

    jobs.submit(lambda report: "broken", done=broken, error=seen.append)
    jobs.submit(lambda report: "ok", done=seen.append, error=seen.append)
    await_results(jobs, 4)
    jobs.drain(100)
    assert set(seen) == {"discarded", "ok"}
    assert not jobs._callbacks
    assert "broken callback" in caplog.text


@pytest.mark.parametrize("path", ["existing", "new"])
def test_channel_fetches_never_call_tk_from_workers(ui, monkeypatch, path):
    monkeypatch.setattr(module, "fetch_channel_videos", ui.network(ChannelFetchResult()))
    monkeypatch.setattr(module, "fetch_channel_title_online", ui.network("Official"))
    on_saved = MagicMock(side_effect=lambda channel: assert_main(ui))
    if path == "existing":
        ui.view.fetch_videos_for_channel(ui.view.channels[0])
    else:
        ui.view._fetch_and_create_channel("https://youtube.com/@new", on_saved=on_saved)
    events = await_results(ui.jobs)
    assert all(kind != "error" for _, kind, _ in events)
    assert not ui.notices and not ui.imports and not on_saved.called
    ui.jobs.drain(100)
    assert not ui.jobs._callbacks
    assert ui.notices[-1][0] == "showinfo"
    if path == "new":
        assert ui.view.channels[-1].name == "Official"
        on_saved.assert_called_once()


def assert_main(ui):
    assert threading.get_ident() == ui.owner


@pytest.mark.parametrize("path", ["existing", "new"])
def test_network_exceptions_close_loading_and_show_error_on_ui_thread(ui, monkeypatch, path):
    monkeypatch.setattr(module, "fetch_channel_videos", ui.network(RuntimeError("offline")))
    if path == "existing":
        ui.view.fetch_videos_for_channel(ui.view.channels[0])
    else:
        ui.view._fetch_and_create_channel("https://youtube.com/@new")
    loading = ui.created[0]
    await_results(ui.jobs)
    ui.jobs.drain()
    assert not loading.alive
    assert ui.notices[-1][0] == "showerror"
    assert "offline" in str(ui.notices[-1])


def test_closing_new_channel_fetch_does_not_create_a_channel(ui, monkeypatch):
    monkeypatch.setattr(module, "fetch_channel_videos", ui.network(ChannelFetchResult(channel_title="Official")))
    ui.view._fetch_and_create_channel("https://youtube.com/@new")
    await_results(ui.jobs)
    ui.created[0].destroy()
    ui.jobs.drain()
    assert [ch.name for ch in ui.view.channels] == ["Old"]
    assert not ui.notices


@pytest.mark.parametrize("change", ["closed", "removed", "url", "name"])
def test_late_video_fetch_is_discarded_for_closed_or_changed_target(ui, monkeypatch, change):
    monkeypatch.setattr(module, "fetch_channel_videos", ui.network(ChannelFetchResult()))
    channel = ui.view.channels[0]
    ui.view.fetch_videos_for_channel(channel)
    await_results(ui.jobs)
    if change == "closed":
        ui.created[0].destroy()
    elif change == "removed":
        ui.view.channels.clear()
    elif change == "url":
        channel.url = "https://youtube.com/@different"
    else:
        channel.name = "Manual rename"
    ui.jobs.drain()
    assert not ui.notices and not ui.imports
    assert not ui.created[0].alive


def name_dialog(ui):
    modal = ui.Widget()
    url = ui.Widget(modal, value="@old")
    name = ui.Widget(modal, value="")
    button, status = ui.Widget(modal), ui.Widget(modal)
    return modal, url, name, button, status


@pytest.mark.parametrize("change", ["none", "closed", "url", "name"])
def test_name_lookup_preserves_new_input_and_closed_dialogs(ui, monkeypatch, change):
    monkeypatch.setattr(module, "fetch_channel_title_online", ui.network("Official"))
    modal, url, name, button, status = args = name_dialog(ui)
    ui.view._fetch_name_for_dialog(*args)
    await_results(ui.jobs)
    if change == "closed":
        modal.destroy()
    elif change == "url":
        url.set("@different")
    elif change == "name":
        name.set("Manually typed")
    ui.jobs.drain()
    if change == "none":
        assert name.get() == "Official"
    elif change != "closed":
        assert name.get() == ("Manually typed" if change == "name" else "")
    if change != "closed":
        assert button.options["state"] == "normal"


def test_new_name_request_cancels_the_older_result(ui, monkeypatch):
    def fetch(url):
        assert threading.get_ident() != ui.owner
        return url + " official"

    monkeypatch.setattr(module, "fetch_channel_title_online", fetch)
    modal, url, name, button, status = args = name_dialog(ui)
    ui.view._fetch_name_for_dialog(*args)
    await_results(ui.jobs)
    url.set("@new")
    ui.view._fetch_name_for_dialog(*args)
    await_results(ui.jobs, 2)
    ui.jobs.drain()
    assert name.get() == "@new official"
    assert button.options["state"] == "normal"


def test_name_lookup_failure_restores_dialog_controls(ui, monkeypatch):
    monkeypatch.setattr(module, "fetch_channel_title_online", ui.network(RuntimeError("offline")))
    modal, url, name, button, status = args = name_dialog(ui)
    ui.view._fetch_name_for_dialog(*args)
    await_results(ui.jobs)
    ui.jobs.drain()
    assert button.options["state"] == "normal"
    assert "offline" in status.options["text"]
    assert name.get() == ""


def test_blank_name_submit_resolves_off_thread_before_saving(ui, monkeypatch):
    monkeypatch.setattr(module, "fetch_channel_title_online", ui.network("Official"))
    ui.view.open_channel_modal()
    url = next(w for w in ui.created if w.options.get("placeholder_text", "").startswith("https://"))
    url.set("https://youtube.com/@new")
    fetch_check = next(w for w in ui.created if w.options.get("text") == "Fetch videos automatically")
    fetch_check.options["variable"].set(False)
    submit = next(w for w in ui.created if w.options.get("text") == "Add Channel")
    submit.options["command"]()
    await_results(ui.jobs)
    assert len(ui.view.channels) == 1
    ui.jobs.drain()
    assert ui.app.config_mgr.get_channels()[-1]["name"] == "Official"


@pytest.mark.parametrize("change", ["removed", "url", "name", "reordered"])
def test_edit_dialog_uses_channel_identity_instead_of_a_stale_index(ui, change):
    target = ui.view.channels[0]
    ui.view.open_channel_modal(edit_index=0)
    other = Channel(name="Other", url="https://youtube.com/@other")
    ui.view.channels.append(other)
    if change == "removed":
        ui.view.channels.remove(target)
    elif change == "url":
        target.url = "https://youtube.com/@changed"
    elif change == "name":
        target.name = "Changed"
    else:
        ui.view.channels.reverse()
    submit = next(w for w in ui.created if w.options.get("text") == "Save Changes")
    submit.options["command"]()
    if change == "reordered":
        assert ui.view.channels[0] is other
        assert ui.view.channels[1].name == "Old"
        assert not ui.notices
    else:
        assert ui.notices[-1][0] == "showerror"
        assert other.name == "Other"


@pytest.mark.parametrize("change", ["none", "closed", "removed", "url", "name"])
def test_bulk_sync_applies_current_results_only_on_main_thread(ui, monkeypatch, change):
    monkeypatch.setattr(module, "fetch_channel_title_online", ui.network("Official"))
    original = ui.view.channels[0]
    ui.view.sync_all_channel_names()
    await_results(ui.jobs)
    assert original.name == "Old"
    assert not ui.app.views["queue"].reassign_channel.called
    if change == "closed":
        ui.created[0].destroy()
    elif change == "removed":
        ui.view.channels.clear()
    elif change == "url":
        original.url = "https://youtube.com/@different"
    elif change == "name":
        original.name = "Manual"
    ui.jobs.drain(100)
    if change == "none":
        assert original.name == "Official"
        ui.app.views["queue"].reassign_channel.assert_called_once_with("Old", "Official")
    else:
        assert original.name != "Official"
        assert not ui.app.views["queue"].reassign_channel.called


def test_bulk_sync_does_not_merge_duplicate_channel_names(ui, monkeypatch):
    ui.view.channels.append(Channel(name="Other", url="https://youtube.com/@other"))
    monkeypatch.setattr(module, "fetch_channel_title_online", ui.network("Official"))
    ui.view.sync_all_channel_names()
    await_results(ui.jobs)
    ui.jobs.drain(100)
    assert [ch.name for ch in ui.view.channels] == ["Official", "Other"]
    assert len(ui.app.config_mgr.get_channels()) == 2


def test_poll_continues_after_callback_and_worker_event_failures(caplog):
    app = YTBotApp.__new__(YTBotApp)
    app.background_jobs = BackgroundJobs()
    app.views = {}
    app.log_queue = queue.Queue()
    app.event_queue = queue.Queue()
    app.event_queue.put(("broken",))
    app.event_queue.put(("ok",))
    app._handle_worker_event = MagicMock(side_effect=[RuntimeError("broken event"), None])
    app.after = MagicMock()

    def broken(value):
        raise RuntimeError("broken UI callback")

    app.background_jobs.submit(lambda report: "ready", done=broken, error=broken)
    await_results(app.background_jobs)
    app._poll_log_queue()
    assert app._handle_worker_event.call_count == 2
    app.after.assert_called_once_with(50, app._poll_log_queue)
    assert "broken UI callback" in caplog.text and "broken event" in caplog.text
