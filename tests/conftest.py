import os
import sys
from pathlib import Path
import customtkinter as ctk
import pytest

# Ensure Tcl/Tk paths are configured on Windows
if sys.platform == "win32":
    candidates = [
        Path(sys.base_prefix),
        Path(sys.prefix),
        Path(getattr(sys, "_base_executable", sys.executable)).parent,
    ]
    cfg = Path(sys.prefix) / "pyvenv.cfg"
    if cfg.exists():
        for line in cfg.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("home ="):
                candidates.append(Path(line.split("=", 1)[1].strip()))
    for py_dir in candidates:
        tcl_dir = py_dir / "tcl" / "tcl8.6"
        tk_dir = py_dir / "tcl" / "tk8.6"
        if tcl_dir.exists() and "TCL_LIBRARY" not in os.environ:
            os.environ["TCL_LIBRARY"] = str(tcl_dir)
        if tk_dir.exists() and "TK_LIBRARY" not in os.environ:
            os.environ["TK_LIBRARY"] = str(tk_dir)
        if "TCL_LIBRARY" in os.environ and "TK_LIBRARY" in os.environ:
            break


@pytest.fixture(scope="session")
def ctk_app():
    app = ctk.CTk()
    app.withdraw()
    yield app
    try:
        app.destroy()
    except Exception:
        pass
