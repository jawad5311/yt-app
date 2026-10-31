import customtkinter as ctk
import pytest


@pytest.fixture(scope="session")
def ctk_app():
    app = ctk.CTk()
    app.withdraw()
    yield app
    try:
        app.destroy()
    except Exception:
        pass
