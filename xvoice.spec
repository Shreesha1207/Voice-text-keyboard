# -*- mode: python ; coding: utf-8 -*-
# Xvoice PyInstaller spec — produces a single-file executable on
# Windows/Linux and an .app bundle on macOS.

import sys
import os
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# ── Data files to bundle ────────────────────────────────────────────────
# On Windows we ship ffmpeg.exe next to the binary so normalize_audio()
# can find it at  os.path.dirname(sys.executable).
datas = []
if sys.platform == 'win32' and os.path.isfile('ffmpeg.exe'):
    datas.append(('ffmpeg.exe', '.'))

# setuptools' vendored jaraco.text ships a data file ("Lorem ipsum.txt") that the
# pkg_resources runtime hook imports at startup. PySide6 pulls setuptools into the
# bundle, and without this data file the frozen .exe crashes on launch with
#   FileNotFoundError: ...setuptools\_vendor\jaraco\text\Lorem ipsum.txt
# collect_data_files() does not reliably capture _vendor data across setuptools
# versions, so bundle those .txt files directly to the exact path the hook expects.
datas += collect_data_files('setuptools')
try:
    import setuptools as _st
    _jaraco_text = os.path.join(os.path.dirname(_st.__file__), '_vendor', 'jaraco', 'text')
    if os.path.isdir(_jaraco_text):
        for _f in os.listdir(_jaraco_text):
            if _f.endswith('.txt'):
                datas.append((os.path.join(_jaraco_text, _f),
                              os.path.join('setuptools', '_vendor', 'jaraco', 'text')))
except Exception:
    pass

# certifi's CA bundle (cacert.pem) must be bundled or every HTTPS call / token
# refresh fails with "Could not find a suitable TLS CA certificate bundle".
try:
    import certifi
    datas.append((certifi.where(), 'certifi'))
except Exception:
    pass

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'pynput',
        'pynput.keyboard',
        'pynput.keyboard._win32',
        'pynput.keyboard._xorg',
        'pynput.keyboard._darwin',
        'pynput.mouse',
        'pynput.mouse._win32',
        'pynput.mouse._xorg',
        'pynput.mouse._darwin',
        'pystray',
        'pystray._win32',
        'pystray._xorg',
        'pystray._darwin',
        'pyaudio',
        'webrtcvad',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL._imaging',
        'PIL._tkinter_finder',
        'requests',
        'json',
        'tempfile',
        'logging',
        'logging.handlers',
        'socket',
        'wave',
        'webbrowser',
        'http.server',
        'pyperclip',
        # ── Writing Engine (conditionally started based on plan_product) ──
        # UI is built on PySide6/Qt (migrated from tkinter).
        'PySide6',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'shiboken6',
        'writing.engine',
        'writing.overlay',
        'writing.selection',
        'writing.clipboard',
        'writing.backend_client',
        'writing.actions.perform',
        'writing.ui.qt_host',
        'writing.ui.qt_helpers',
        'writing.ui.listening_overlay',
        'writing.ui.action_menu',
        'writing.ui.language_menu',
        'writing.ui.preview_widget',
    ],
    hookspath=['.'],
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