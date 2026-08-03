# -*- mode: python ; coding: utf-8 -*-
import os
import imageio_ffmpeg
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = [(imageio_ffmpeg.get_ffmpeg_exe(), '.')]
hiddenimports = ['pydub', 'sounddevice', 'soundfile', 'importlib.resources', 'importlib.metadata']
tmp_ret = collect_all('customtkinter')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pyloudnorm')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('scipy')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('tkinterdnd2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('PIL')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['audio_master.py'],
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
    name='Audio Master',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,  # 讓雙擊 .abproj 開啟時，macOS 的開檔事件轉成 sys.argv
    target_arch='arm64',
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
    name='Audio Master',
)
app = BUNDLE(
    coll,
    name='Audio Master.app',
    # App 本身用標準 macOS 圓角方形（squircle）Logo；.abproj 文件圖示也用同一顆
    # （CFBundleTypeIconFile 另外指定，見下方），兩者視覺上完全一致。
    icon='icons/AudioMaster.icns',
    bundle_identifier='com.audiomaster.app',
    info_plist={
        'CFBundleShortVersionString': '1.2.2',
        'CFBundleVersion': '122',
        'CFBundleDocumentTypes': [
            {
                'CFBundleTypeName': 'Audio Master Project',
                'CFBundleTypeExtensions': ['abproj'],
                # .abproj 文件圖示改跟 App 本身用同一顆（AudioMaster.icns），視覺上完全一致。
                # 這顆圖示本來就會被 BUNDLE() 的 icon= 參數放進 Contents/Resources，不必再手動複製，
                # 也就不會有「BUNDLE 簽完名之後再動檔案」讓簽章失效的風險（曾經因此讓使用者端 App 打不開）。
                'CFBundleTypeIconFile': 'AudioMaster.icns',
                'CFBundleTypeRole': 'Editor',
                'LSHandlerRank': 'Owner',
                'LSItemContentTypes': ['com.audiomaster.app.abproj'],
            }
        ],
        'UTExportedTypeDeclarations': [
            {
                'UTTypeIdentifier': 'com.audiomaster.app.abproj',
                'UTTypeDescription': 'Audio Master Project',
                'UTTypeConformsTo': ['public.data', 'public.content'],
                'UTTypeTagSpecification': {'public.filename-extension': ['abproj']},
                'UTTypeIconFile': 'AudioMaster.icns',
            }
        ],
    },
)
