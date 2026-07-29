# Audio Master v1.2.0

Audio Master 是以 CustomTkinter、Tk 與 FFmpeg 製作的 macOS 音訊工具，整合 LUFS 響度平衡、True Peak 監看、格式轉換，以及多軌非破壞性剪輯。

## 主要功能

- LUFS 響度量測與目標平衡，支援原始／目標 A/B 試聽及批次增益調整。
- 原始與目標 True Peak（dBTP）顯示，並以顏色提示削波風險。
- 多檔與資料夾匯入、左側資料夾樹、多工作區，以及 `.abproj` 專案存取。
- Edit Window 多軌剪輯：Region 選取、移動、分割、剪下、複製、貼上、刪除、Undo／Redo。
- Region Fade In／Fade Out 長度與曲線控制，並套用到播放預覽及匯出。
- 每軌 SOLO／MUTE 監聽控制。
- WAV、AIF、AIFF、FLAC、OGG、M4A、MP3、WMA、AAC、OPUS 轉換，匯出時可保留來源資料夾層級。

## Edit Window 快捷鍵

| 快捷鍵 | 功能 |
| --- | --- |
| `Cmd+4` | 開啟或關閉 Edit Window |
| `Cmd+E` | 主視窗開啟 Edit Window；Edit Window 於播放頭分割 |
| `Space` | 播放 → 暫停 → 從頭播放 |
| `Enter` | 停止並將播放頭移到開頭，不自動播放 |
| `←` / `→` | 播放頭前後移動 1 秒 |
| `Shift+←` / `Shift+→` | 播放頭前後移動 5 秒 |
| `Cmd+X` / `Cmd+C` / `Cmd+V` | 剪下／複製／貼上 |
| `Cmd+Z` / `Cmd+Shift+Z` | Undo／Redo |
| `Cmd+S` | 同步 Edit Window 並儲存專案 |
| `Delete` / `Backspace` | 刪除選取內容 |

## 開發與執行

```bash
# Python 3.10 / Tk 8.6 開發環境
~/Python_Audio_Balancer/venv/bin/python audio_master.py
```

## macOS Apple Silicon 打包

目前正式安裝包以 Python 3.13、Tk 9 與 PyInstaller 建置，支援 Apple Silicon（M 系列）。

```bash
~/Python_Audio_Balancer/venv/bin/python3.13 -m PyInstaller \
  --clean --noconfirm "Audio Master.spec"

codesign --verify --deep --strict "dist/Audio Master.app"

ditto -c -k --sequesterRsrc --keepParent \
  "dist/Audio Master.app" \
  "dist/Audio-Master-macOS-AppleSilicon.zip"
```

App 目前採 ad-hoc 簽章，尚未經 Apple 公證。第一次開啟時，請在 Finder 對 `Audio Master.app` 按右鍵並選擇「打開」。

## 測試

```bash
~/Python_Audio_Balancer/venv/bin/python3.13 -m unittest discover -s tests -v
```

## 專案檔案

- `audio_master.py` — 主程式
- `Audio Master.spec` — PyInstaller Apple Silicon 打包設定
- `ui_prototype_flet.py` — Flet 外觀原型，非正式程式
- `CHANGELOG.md` — 版本更新記錄
