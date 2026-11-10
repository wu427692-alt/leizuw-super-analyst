# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_all

datas = [('static', 'static'), ('strategies', 'strategies')]
binaries = []
hiddenimports = ['multipart', 'multipart.multipart', 'json_repair', 'tiktoken', 'tiktoken_ext', 'tiktoken_ext.openai_public', 'api', 'api.app', 'api.deps', 'api.v1', 'api.v1.router', 'api.v1.endpoints', 'api.v1.endpoints.analysis', 'api.v1.endpoints.history', 'api.v1.endpoints.stocks', 'api.v1.endpoints.health', 'api.v1.endpoints.alphasift', 'api.v1.schemas', 'api.v1.schemas.analysis', 'api.v1.schemas.history', 'api.v1.schemas.stocks', 'api.v1.schemas.common', 'api.middlewares', 'api.middlewares.error_handler', 'src.services', 'src.services.task_queue', 'src.services.analysis_service', 'src.services.history_service', 'src.services.alphasift_service', 'alphasift', 'alphasift.dsa_adapter', 'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto', 'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan', 'uvicorn.lifespan.on']
datas += collect_data_files('litellm')
datas += collect_data_files('tiktoken')
tmp_ret = collect_all('alphasift')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
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
    [],
    exclude_binaries=True,
    name='stock_analysis',
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
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='stock_analysis',
)
app = BUNDLE(
    coll,
    name='stock_analysis.app',
    icon=None,
    bundle_identifier=None,
)
