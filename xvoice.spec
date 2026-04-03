# -*- mode: python ; coding: utf-8 -*-
import sys
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

block_cipher = None

# Platform-specific hidden imports
hidden = ['webrtcvad', 'pystray', 'PIL._tkinter_finder']
if sys.platform == 'win32':
    hidden += ['pynput.keyboard._win32', 'pynput.mouse._win32', 'pystray._win32']
elif sys.platform == 'darwin':
    hidden += ['pynput.keyboard._darwin', 'pynput.mouse._darwin', 'pystray._darwin']
else:
    hidden += ['pynput.keyboard._xorg', 'pynput.mouse._xorg', 'pystray._gtk']

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=collect_dynamic_libs('webrtcvad'),
    datas=collect_data_files('pystray'),
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='xvoice',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,   # must be False on non-Mac too
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

# macOS .app bundle
if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='Xvoice.app',
        icon=None,
        bundle_identifier='com.xvoice.app',
        info_plist={
            'NSMicrophoneUsageDescription': 'Xvoice needs microphone access to transcribe your speech.',
            'LSUIElement': True,   # hides from Dock (background app)
        },
    )
