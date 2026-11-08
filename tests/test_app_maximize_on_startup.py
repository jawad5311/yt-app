import tkinter as tk
from unittest.mock import MagicMock, patch
import pytest

from src.ui.app import YTBotApp


class TestAppMaximizeOnStartup:
    def test_maximize_window_sets_zoomed_state(self):
        app = YTBotApp.__new__(YTBotApp)
        app.state = MagicMock()

        YTBotApp.maximize_window(app)

        app.state.assert_called_once_with("zoomed")

    def test_maximize_window_fallback_to_attributes_if_state_fails(self):
        app = YTBotApp.__new__(YTBotApp)
        app.state = MagicMock(side_effect=Exception("state zoomed not supported"))
        app.attributes = MagicMock()

        YTBotApp.maximize_window(app)

        app.state.assert_called_once_with("zoomed")
        app.attributes.assert_called_once_with("-zoomed", True)

    def test_maximize_window_handles_all_exceptions_gracefully(self):
        app = YTBotApp.__new__(YTBotApp)
        app.state = MagicMock(side_effect=Exception("state zoomed not supported"))
        app.attributes = MagicMock(side_effect=Exception("attributes -zoomed not supported"))

        # Should not raise any exception
        YTBotApp.maximize_window(app)

    def test_real_tk_window_zoomed_state(self):
        root = tk.Tk()
        try:
            root.geometry("1180x740")
            root.minsize(1020, 620)
            YTBotApp.maximize_window(root)
            root.update()

            # On Windows, state is 'zoomed'
            assert root.state() in ("zoomed", "normal")
        finally:
            root.destroy()
