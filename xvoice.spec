# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['main.py'],  # 🔁 change if your entry file is different
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'pynput',
        'pystray',
        'pyaudio',
        'webrtcvad'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,   # keep as False (fine)
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # 🔥 IMPORTANT
    name='xvoice',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # 🔥 VERY IMPORTANT (no packing)
    console=False,           # keep False if you want background
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,               # 🔥 no compression
    name='xvoice',
)