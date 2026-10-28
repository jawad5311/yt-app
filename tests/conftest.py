import pytest
import customtkinter as ctk


@pytest.fixture(scope="session")
def ctk_app():
    app = ctk.CTk()
    app.withdraw()
    yield app
    try:
        app.destroy()
    except Exception:
        pass
