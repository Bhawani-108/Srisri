# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

# Collect Streamlit's static assets and metadata
datas = []
datas += collect_data_files('streamlit')
datas += copy_metadata('streamlit')

# Add your specific application files
datas += [
    ('frontend_dashboard.py', '.'),
    ('watchlist.txt', '.')
]

a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    # Force PyInstaller to include these if it misses them during analysis
    hiddenimports=[
        'pyotp', 
        'SmartApi', 
        'pandas', 
        'plotly'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AlgoTradingApp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True, # Change to False later if you want to hide the terminal
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AlgoTradingApp',
)