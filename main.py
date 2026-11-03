import sys
from pathlib import Path

# Add project root directory to sys.path
root_dir = Path(__file__).resolve().parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from src.automation.driver import close_driver, setup_driver
from src.config.manager import ConfigManager
from src.models.account import Account
from src.ui.app import YTBotApp
from src.utils.logger import init_logging


def run_browser_diagnostics():
    """Diagnostic mode to verify Chrome/ChromeDriver health."""
    print("=== Running Chrome Browser Diagnostics ===")
    init_logging()
    test_acc = Account(email="diagnostics_test@gmail.com", password="dummy")
    driver = None
    try:
        driver = setup_driver(test_acc)
        driver.get("https://accounts.google.com")
        print(f"[SUCCESS] Browser launched successfully! Current URL: {driver.current_url}")
    except Exception as exc:
        print(f"[ERROR] Browser diagnostic failed: {exc}")
    finally:
        if driver:
            close_driver(driver)


def main():
    if "--diagnose-browser" in sys.argv:
        run_browser_diagnostics()
        sys.exit(0)

    try:
        config_mgr = ConfigManager()
        app = YTBotApp(config_mgr=config_mgr)
        app.mainloop()
    except Exception as exc:
        import traceback
        err_msg = f"Application startup error: {exc}\n\n{traceback.format_exc()}"
        try:
            with open("error.log", "a", encoding="utf-8") as f:
                f.write(err_msg + "\n" + "=" * 50 + "\n")
        except Exception:
            pass
        try:
            import tkinter.messagebox
            tkinter.messagebox.showerror("YT Organic Bot Error", err_msg)
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
