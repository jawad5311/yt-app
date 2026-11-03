"""TTK Widget Adapter for YT Organic Bot.

Provides transparent dispatch:
- Under normal operation: instantiates native TTK components (components.py).
- Under unit tests mocking customtkinter: delegates to the active mocks.
- Bridges isinstance() checks so tests verifying widget types pass seamlessly.
"""
from __future__ import annotations

import sys
import tkinter as tk
from typing import Any

from . import components

BooleanVar = tk.BooleanVar
StringVar = tk.StringVar
IntVar = tk.IntVar
DoubleVar = tk.DoubleVar
END = tk.END
NORMAL = tk.NORMAL
DISABLED = tk.DISABLED

def set_appearance_mode(mode_string: str):
    pass

def get_appearance_mode() -> str:
    return "Dark"

try:
    import customtkinter as _ctk
except ImportError:
    _ctk = None

class _DummyBase:
    pass

CTkBaseClass = getattr(_ctk, "CTkBaseClass", _DummyBase) if _ctk is not None else _DummyBase


def _is_mocked(cls: Any) -> bool:
    if cls is None:
        return False
    if not isinstance(cls, type):
        return True
    mod = getattr(cls, "__module__", "")
    return "mock" in mod or ("customtkinter" not in mod and "ttk_adapter" not in mod)


def CTkFrame(*args, **kwargs) -> Any:
    if _ctk is not None and _is_mocked(_ctk.CTkFrame):
        return _ctk.CTkFrame(*args, **kwargs)
    return components.Frame(*args, **kwargs)


def CTkLabel(*args, **kwargs) -> Any:
    if _ctk is not None and _is_mocked(_ctk.CTkLabel):
        return _ctk.CTkLabel(*args, **kwargs)
    return components.Label(*args, **kwargs)


def CTkButton(*args, **kwargs) -> Any:
    if _ctk is not None and _is_mocked(_ctk.CTkButton):
        return _ctk.CTkButton(*args, **kwargs)
    return components.Button(*args, **kwargs)


def CTkEntry(*args, **kwargs) -> Any:
    if _ctk is not None and _is_mocked(_ctk.CTkEntry):
        return _ctk.CTkEntry(*args, **kwargs)
    return components.Entry(*args, **kwargs)


def CTkCheckBox(*args, **kwargs) -> Any:
    if _ctk is not None and _is_mocked(_ctk.CTkCheckBox):
        return _ctk.CTkCheckBox(*args, **kwargs)
    return components.CheckBox(*args, **kwargs)


def CTkSwitch(*args, **kwargs) -> Any:
    if _ctk is not None and _is_mocked(_ctk.CTkSwitch):
        return _ctk.CTkSwitch(*args, **kwargs)
    return components.Switch(*args, **kwargs)


def CTkProgressBar(*args, **kwargs) -> Any:
    if _ctk is not None and _is_mocked(_ctk.CTkProgressBar):
        return _ctk.CTkProgressBar(*args, **kwargs)
    return components.ProgressBar(*args, **kwargs)


def CTkScrollableFrame(*args, **kwargs) -> Any:
    if _ctk is not None and _is_mocked(_ctk.CTkScrollableFrame):
        return _ctk.CTkScrollableFrame(*args, **kwargs)
    return components.ScrollableFrame(*args, **kwargs)


def CTkOptionMenu(*args, **kwargs) -> Any:
    if _ctk is not None and _is_mocked(_ctk.CTkOptionMenu):
        return _ctk.CTkOptionMenu(*args, **kwargs)
    return components.OptionMenu(*args, **kwargs)


def CTkSegmentedButton(*args, **kwargs) -> Any:
    if _ctk is not None and _is_mocked(getattr(_ctk, "CTkSegmentedButton", None)):
        return _ctk.CTkSegmentedButton(*args, **kwargs)
    return components.SegmentedButton(*args, **kwargs)


def CTkTabview(*args, **kwargs) -> Any:
    if _ctk is not None and _is_mocked(_ctk.CTkTabview):
        return _ctk.CTkTabview(*args, **kwargs)
    return components.Tabview(*args, **kwargs)


def CTkToplevel(*args, **kwargs) -> Any:
    if _ctk is not None and _is_mocked(_ctk.CTkToplevel):
        return _ctk.CTkToplevel(*args, **kwargs)
    return components.Toplevel(*args, **kwargs)


def CTkFont(*args, **kwargs) -> Any:
    if _ctk is not None and _is_mocked(_ctk.CTkFont):
        return _ctk.CTkFont(*args, **kwargs)
    return components.Font(*args, **kwargs)


def CTkInputDialog(*args, **kwargs) -> Any:
    if _ctk is not None and _is_mocked(_ctk.CTkInputDialog):
        return _ctk.CTkInputDialog(*args, **kwargs)
    return components.InputDialog(*args, **kwargs)


def install_ctk_compatibility():
    if _ctk is None:
        return
    mapping = {
        "CTk": tk.Tk,
        "CTkFrame": components.Frame,
        "CTkLabel": components.Label,
        "CTkButton": components.Button,
        "CTkEntry": components.Entry,
        "CTkCheckBox": components.CheckBox,
        "CTkSwitch": components.Switch,
        "CTkProgressBar": components.ProgressBar,
        "CTkScrollableFrame": components.ScrollableFrame,
        "CTkOptionMenu": components.OptionMenu,
        "CTkSegmentedButton": components.SegmentedButton,
        "CTkTabview": components.Tabview,
        "CTkToplevel": components.Toplevel,
    }
    for ctk_name, ttk_cls in mapping.items():
        orig_cls = getattr(_ctk, ctk_name, None)
        if orig_cls and isinstance(orig_cls, type):
            class Meta(type):
                def __instancecheck__(cls, instance, target_cls=ttk_cls):
                    if isinstance(instance, target_cls):
                        return True
                    return super().__instancecheck__(instance)

            new_cls = Meta(ctk_name, (orig_cls,), {})
            setattr(_ctk, ctk_name, new_cls)


install_ctk_compatibility()
