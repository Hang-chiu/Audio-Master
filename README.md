# Audio Master — 音量平衡輔助化工具

單檔 Python 桌面 App(CustomTkinter + ttk),整合 **LUFS 響度平衡** 與 **FFmpeg 格式轉換**,支援多工作區(workspace tabs)。

## 功能
- LUFS 響度量測與目標平衡(原始/目標 A/B 試聽、批次 ±Gain)
- 多檔匯入(可複選資料夾)、左側資料夾樹(含檔案數量、橫向捲軸)
- 多選音軌時,波形整組移到左側獨立欄、各軌依時長量化(一眼看出長短)
- 格式轉換(wav / mp3 / m4a / aac / wma / flac…),保留匯入資料夾層級
- 靜音移除、多工作區、專案存讀(`.abproj`)

## 開發 / 執行
```bash
# 開發(venv: Python 3.10 / Tk 8.6)
~/Python_Audio_Balancer/venv/bin/python audio_master.py
```

## 打包成 .app(macOS)
```bash
# 用 Python 3.13 / Tk 9.0 打包
~/Python_Audio_Balancer/venv/bin/python3.13 -m PyInstaller "Audio Master.spec" --noconfirm
# 產出 dist/Audio Master.app
```

## 檔案
- `audio_master.py` — 主程式(單檔)
- `Audio Master.spec` — PyInstaller 打包設定
- `ui_prototype_flet.py` — Flet 外觀原型(非正式程式)
