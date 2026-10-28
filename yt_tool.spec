# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect all data files and submodules from customtkinter
datas = collect_data_files("customtkinter")

# Exclude sensitive local configuration, credentials, statistics, and temp/backup files
SENSITIVE_NAMES = {
    "credentials.json",
    "accounts.txt",
    "settings.json",
    "stats.json",
}

filtered_datas = []
for src, dst in datas:
    src_base = os.path.basename(src).lower()
    if src_base in SENSITIVE_NAMES or src_base.endswith(".bak") or src_base.endswith(".tmp") or ".backup_" in src_base:
        continue
    filtered_datas.append((src, dst))
datas = filtered_datas

hiddenimports = (
    collect_submodules("customtkinter")
    + collect_submodules("selenium")
    + ["undetected_chromedriver"]
)

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tests", "scratch"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="YTAutomationTool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="YTAutomationTool",
)
