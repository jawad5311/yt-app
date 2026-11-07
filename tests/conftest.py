import os
import sys
from pathlib import Path
import customtkinter as ctk
import pytest

# Ensure Tcl/Tk paths are configured on Windows
if sys.platform == "win32":
    py_dir = Path(sys.base_prefix)
    tcl_dir = py_dir / "tcl" / "tcl8.6"
    tk_dir = py_dir / "tcl" / "tk8.6"
    if tcl_dir.exists() and "TCL_LIBRARY" not in os.environ:
        os.environ["TCL_LIBRARY"] = str(tcl_dir)
    if tk_dir.exists() and "TK_LIBRARY" not in os.environ:
        os.environ["TK_LIBRARY"] = str(tk_dir)


@pytest.fixture(scope="session")
def ctk_app():
    app = ctk.CTk()
    app.withdraw()
    yield app
    try:
        app.destroy()
    except Exception:
        pass
