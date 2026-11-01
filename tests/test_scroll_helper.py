from unittest.mock import MagicMock

import customtkinter as ctk

from src.utils.scroll_helper import setup_smooth_scrolling


def test_setup_smooth_scrolling_initialization(ctk_app):
    app = ctk_app
    frame = ctk.CTkScrollableFrame(app)

    orig_check = frame.check_if_master_is_canvas
    setup_smooth_scrolling(frame, notch_pixels=120.0, damping=0.35)

    assert frame.check_if_master_is_canvas != orig_check
    assert frame.check_if_master_is_canvas(frame) is False

    frame.destroy()


def test_setup_smooth_scrolling_none_canvas():
    fake_frame = MagicMock(spec=[])
    setup_smooth_scrolling(fake_frame)


def test_layered_frames_wheel_routing(ctk_app):
    app = ctk_app
    app.geometry("500x500")
    container = ctk.CTkFrame(app)
    container.pack(fill="both", expand=True)

    v1 = ctk.CTkFrame(container)
    v1.grid(row=0, column=0, sticky="nsew")
    s1 = ctk.CTkScrollableFrame(v1)
    s1.pack(fill="both", expand=True)
    for i in range(20):
        ctk.CTkLabel(s1, text=f"V1 Item {i}").pack(pady=4)

    v2 = ctk.CTkFrame(container)
    v2.grid(row=0, column=0, sticky="nsew")
    s2 = ctk.CTkScrollableFrame(v2)
    s2.pack(fill="both", expand=True)
    for i in range(20):
        ctk.CTkLabel(s2, text=f"V2 Item {i}").pack(pady=4)

    setup_smooth_scrolling(s1, notch_pixels=120.0)
    setup_smooth_scrolling(s2, notch_pixels=120.0)
    app.update()

    # Raise v2 (Settings)
    v2.tkraise()
    app.update()

    # Simulate scroll event on canvas inside s2
    canvas2 = s2._parent_canvas
    event = MagicMock()
    event.delta = -120
    event.num = 0
    event.widget = canvas2

    # Canvas 2 should scroll because v2 is mapped/active on top
    initial_y = canvas2.yview()[0]
    canvas2.event_generate = MagicMock()
    # Call listener directly
    res = s2._smooth_mousewheel(event)
    assert res == "break"

    container.destroy()
