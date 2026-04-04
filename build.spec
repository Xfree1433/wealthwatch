# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for WealthWatch Desktop
# Build: python -m PyInstaller build.spec --clean --noconfirm
# Output: dist/WealthWatch/WealthWatch.exe (folder-based, AV-friendly)

import os

block_cipher = None
root = os.path.dirname(os.path.abspath(SPEC))

a = Analysis(
    [os.path.join(root, 'launcher.py')],
    pathex=[root],
    binaries=[],
    datas=[
        (os.path.join(root, 'app', 'templates'), os.path.join('app', 'templates')),
        (os.path.join(root, 'app', 'static'), os.path.join('app', 'static')),
        (os.path.join(root, 'README.txt'), '.'),
    ],
    hiddenimports=[
        'flask', 'markupsafe', 'jinja2', 'flask.json',
        'app', 'app.extensions', 'app.services', 'app.services.database', 'app.services.licensing',
        'app.blueprints', 'app.blueprints.auth', 'app.blueprints.dashboard',
        'app.blueprints.accounts', 'app.blueprints.transactions', 'app.blueprints.reports',
        'config',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Exclude internal tools from the build
EXCLUDE_FILES = {'keygen.py', 'memory.md', 'keygen.pyc'}
a.datas = [d for d in a.datas if os.path.basename(d[0]) not in EXCLUDE_FILES]
a.scripts = [s for s in a.scripts if os.path.basename(s[0]) not in EXCLUDE_FILES]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='WealthWatch',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(root, 'app', 'static', 'icon-192.png'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='WealthWatch',
)
