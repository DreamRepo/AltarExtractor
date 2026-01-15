# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Collect dash and related packages data files
datas = []

# Add dash and its components data files
datas += collect_data_files('dash')
datas += collect_data_files('dash_core_components')
datas += collect_data_files('dash_html_components')
datas += collect_data_files('dash_table')
datas += collect_data_files('dash_bootstrap_components')
datas += collect_data_files('plotly')

# Try to add pygwalker data if available
try:
    datas += collect_data_files('pygwalker')
except Exception:
    pass

# Add assets folder
datas += [('assets', 'assets')]

# Add altar_extractor package
datas += [('altar_extractor', 'altar_extractor')]

# Collect hidden imports
hiddenimports = [
    'dash',
    'dash.dcc',
    'dash.html',
    'dash.dash_table',
    'dash_bootstrap_components',
    'flask',
    'werkzeug',
    'jinja2',
    'plotly',
    'plotly.graph_objects',
    'plotly.express',
    'pandas',
    'pymongo',
    'dnspython',
    'bson',
    'pygwalker',
    'pygwalker.api.html',
    'altar_extractor',
    'altar_extractor.callbacks',
    'altar_extractor.callbacks.connection',
    'altar_extractor.callbacks.experiments',
    'altar_extractor.callbacks.filters',
    'altar_extractor.callbacks.metrics',
    'altar_extractor.callbacks.pygwalker',
    'altar_extractor.callbacks.ui',
    'altar_extractor.components',
    'altar_extractor.components.layout',
    'altar_extractor.config',
    'altar_extractor.services',
    'altar_extractor.services.data',
    'altar_extractor.services.mongo',
    'altar_extractor.state',
    'altar_extractor.state.cache',
]

# Collect all dash submodules
hiddenimports += collect_submodules('dash')
hiddenimports += collect_submodules('dash_bootstrap_components')
hiddenimports += collect_submodules('plotly')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AltarExtractor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Keep console to see server output and URL
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
