# -*- mode: python ; coding: utf-8 -*-
# Xvoice PyInstaller spec — produces a single-file executable on
# Windows/Linux and an .app bundle on macOS.

import sys
import os

block_cipher = None

# ── Data files to bundle ────────────────────────────────────────────────
# On Windows we ship ffmpeg.exe next to the binary so normalize_audio()
# can find it at  os.path.dirname(sys.executable).
datas = []
if sys.platform == 'win32' and os.path.isfile('ffmpeg.exe'):
    datas.append(('ffmpeg.exe', '.'))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'pynput',
        'pynput.keyboard',
        'pynput.keyboard._win32',   # Windows-specific backend
        'pynput.keyboard._xorg',    # Linux X11 backend
        'pynput.keyboard._darwin',  # macOS backend
        'pystray',
        'pystray._win32',
        'pystray._xorg',
        'pystray._darwin',
        'pyaudio',
        'webrtcvad',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'requests',
        'json',
        'tempfile',
        'logging',
        'logging.handlers',         # RotatingFileHandler
        'socket',
        'wave',
        'webbrowser',
        'http.server',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── macOS: produce an .app bundle ───────────────────────────────────────
if sys.platform == 'darwin':
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='xvoice',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        name='xvoice',
    )
    app = BUNDLE(
        coll,
        name='Xvoice.app',
        icon=None,              # add an .icns path here if you have one
        bundle_identifier='app.lovable.xvoice',
        info_plist={
            'CFBundleShortVersionString': '1.1.0',
            'CFBundleName': 'Xvoice',
            'LSUIElement': True,        # hide from Dock (tray-only app)
            'NSMicrophoneUsageDescription':
                'Xvoice needs microphone access to transcribe your speech.',
        },
    )

# ── Windows / Linux: single-file executable ─────────────────────────────
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        name='xvoice',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,          # no packing — avoids AV false positives
        console=False,      # no terminal window
    )