from src.config.manager import ConfigManager


def test_append_video_tasks_canonical_deduplication(tmp_path):
    mgr = ConfigManager(config_file=tmp_path / "settings.json")
    mgr.set_video_queue([{"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "title": "Original"}])

    incoming = [
        {"url": "https://youtu.be/dQw4w9WgXcQ", "title": "Short URL"},
        {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s", "title": "Tracking URL"},
        {"url": "https://www.youtube.com/watch?v=newvid12345", "title": "New Video 1"},
        {"url": "https://www.youtube.com/watch?v=newvid12345", "title": "New Video Duplicate in batch"},
    ]

    res = mgr.append_video_tasks(incoming, deduplicate=True)
    assert len(res) == 2
    assert res[0]["title"] == "Original"
    assert res[1]["title"] == "New Video 1"
