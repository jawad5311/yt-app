"""
Unit tests for ToolTip component in src.ui.tooltip.
"""
import tkinter as tk
import unittest
from unittest.mock import MagicMock, patch

from src.ui.tooltip import ToolTip, add_tooltip


class TestToolTip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
        except Exception:
            cls.root = None

    @classmethod
    def tearDownClass(cls):
        if cls.root is not None:
            try:
                cls.root.destroy()
            except Exception:
                pass

    def setUp(self):
        if self.root is None:
            self.skipTest("Tkinter display not available")
        self.btn = tk.Button(self.root, text="Test Button")
        self.btn.pack()
        self.root.update_idletasks()

    def tearDown(self):
        if hasattr(self, "btn") and self.btn.winfo_exists():
            self.btn.destroy()

    def test_tooltip_init_and_helper(self):
        tip = add_tooltip(self.btn, "This is a helpful tip.\nSecond line here.")
        self.assertIsInstance(tip, ToolTip)
        self.assertEqual(tip.text, "This is a helpful tip.\nSecond line here.")
        self.assertIsNone(tip._tip_window)
        self.assertIsNone(tip._after_id)

    def test_show_and_hide(self):
        tip = ToolTip(self.btn, "Hover description", delay_ms=10)
        tip.show()
        self.assertIsNotNone(tip._tip_window)
        self.assertTrue(tip._tip_window.winfo_exists())

        tip.hide()
        self.assertIsNone(tip._tip_window)

    def test_set_text(self):
        tip = ToolTip(self.btn, "Old text")
        tip.show()
        self.assertIsNotNone(tip._tip_window)

        tip.set_text("New updated text")
        self.assertEqual(tip.text, "New updated text")
        # Changing text hides current window
        self.assertIsNone(tip._tip_window)

    def test_on_click_hides_tooltip(self):
        tip = ToolTip(self.btn, "Tip to hide on click")
        tip.show()
        self.assertIsNotNone(tip._tip_window)

        tip._on_click()
        self.assertIsNone(tip._tip_window)

    def test_on_destroy_cleans_up(self):
        tip = ToolTip(self.btn, "Destroy cleanup tip")
        tip.show()
        self.assertIsNotNone(tip._tip_window)

        tip._on_destroy()
        self.assertIsNone(tip._tip_window)

    def test_empty_text_does_not_show(self):
        tip = ToolTip(self.btn, "")
        tip.show()
        self.assertIsNone(tip._tip_window)

    def test_schedule_show_on_enter(self):
        tip = ToolTip(self.btn, "Delayed tip", delay_ms=50)
        tip._on_enter()
        self.assertIsNotNone(tip._after_id)

        # Calling leave cancels after_id
        tip._on_leave()
        self.root.update()
        tip.hide()


if __name__ == "__main__":
    unittest.main()
