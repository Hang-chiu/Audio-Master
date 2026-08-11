# -*- mode: python ; coding: utf-8 -*-
import os
import imageio_ffmpeg
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = [(imageio_ffmpeg.get_ffmpeg_exe(), '.')]
hiddenimports = ['pydub', 'sounddevice', 'soundfile', 'importlib.resources', 'importlib.metadata']
# Keep the .abproj document icon in the collected data.  PyInstaller's BUNDLE
# stage places DATA entries in Contents/Resources *before* it seals the app,
# which is the location Launch Services resolves for CFBundleTypeIconFile.
# This avoids the old post-build-copy approach that invalidated the bundle's
# code signature.
DOCUMENT_ICON = os.path.join(SPECPATH, 'icons', 'AudioProject.icns')
datas.append((DOCUMENT_ICON, '.'))
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
    # App 本身使用圓角方形 Logo；.abproj 使用專屬的折角文件 Logo（見下方）。
    icon='icons/AudioMaster.icns',
    bundle_identifier='com.audiomaster.app',
    info_plist={
        'CFBundleShortVersionString': '1.3.0',
        # build 編號單調遞增，讓 macOS 一律把新包辨識為較新版（即使公開版本號沒動）。
        # 增加 build 編號，讓 Launch Services 重新讀取更新過的 .abproj 文件圖示關聯。
        'CFBundleVersion': '133',
        'CFBundleDocumentTypes': [
            {
                'CFBundleTypeName': 'Audio Master Project',
                'CFBundleTypeExtensions': ['abproj'],
                # 必須與 Contents/Resources 中由 DOCUMENT_ICON 收集的檔名一致。
                'CFBundleTypeIconFile': 'AudioProject.icns',
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
                'UTTypeIconFile': 'AudioProject.icns',
            }
        ],
    },
)
