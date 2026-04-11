# -*- mode: python ; coding: utf-8 -*-
import sys
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files, collect_submodules

hiddenimports = [
    'webrtcvad',
    'pyaudio',
    'pynput',
    'pystray',
    'pystray._base',
    'pynput.keyboard._win32',
    'pynput.mouse._win32',
    'pystray._win32',
    'pynput.keyboard._darwin',
    'pynput.mouse._darwin',
    'pystray._darwin',
    'pynput.keyboard._xorg',
    'pynput.mouse._xorg',
    'pystray._gtk',
]

hiddenimports += collect_submodules('pynput')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=collect_dynamic_libs('webrtcvad'),
    datas=collect_data_files('pystray'),
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

# Single-file on all platforms
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
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

# macOS: wrap single-file exe into .app bundle
if sys.platform == 'darwin':
    app = BUNDLE(
        exe,                  # exe directly, no COLLECT
        name='Xvoice.app',
        icon=None,
        bundle_identifier='com.xvoice.app',
        info_plist={
            'NSMicrophoneUsageDescription': 'Xvoice needs microphone access to transcribe your speech.',
            'LSUIElement': True,
        },
    )