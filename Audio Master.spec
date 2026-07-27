# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = [('/Users/patrickchiu/Python_Audio_Balancer/venv/lib/python3.13/site-packages/imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v7.1', '.')]
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
    name='Audio Master',
)
app = BUNDLE(
    coll,
    name='Audio Master.app',
    # App 本身用標準 macOS 圓角方形（squircle）Logo，跟其他 App 圖示外觀一致；
    # .abproj 文件圖示維持另一份「折角紙」造型（CFBundleTypeIconFile 另外指定，見下方）。
    icon='icons/AudioMaster.icns',
    bundle_identifier='com.audiomaster.app',
    info_plist={
        'CFBundleDocumentTypes': [
            {
                'CFBundleTypeName': 'Audio Master Project',
                'CFBundleTypeExtensions': ['abproj'],
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

# PyInstaller 的 datas 只會落到 Contents/MacOS，但 CFBundleTypeIconFile 要從
# Contents/Resources 才找得到 → 打包完成後手動把文件圖示補進去，讓「一次指令」仍可重現整包。
import shutil as _shutil
import subprocess as _subprocess
_app_path = os.path.join(DISTPATH, 'Audio Master.app')
_resources_dir = os.path.join(_app_path, 'Contents', 'Resources')
try:
    _shutil.copy(os.path.join(SPECPATH, 'icons', 'AudioProject.icns'), _resources_dir)
except Exception as _e:
    print('WARN: 無法複製文件圖示 AudioProject.icns：', _e)

# 上面複製檔案發生在 BUNDLE 已經完成簽章之後，會讓簽章的檔案清單跟實際內容對不上
# （Gatekeeper 檢查會判定「已損毀」拒開）。所以這裡動完檔案要重新簽一次整包，
# 確保交出去的 App 在別人電腦上通得過 spctl 檢查。
try:
    _subprocess.run(
        ['codesign', '--force', '--deep', '--sign', '-', _app_path],
        check=True,
    )
    print('已重新簽章：', _app_path)
except Exception as _e:
    print('WARN: 重新簽章失敗：', _e)
