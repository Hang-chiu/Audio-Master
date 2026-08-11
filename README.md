# Audio Master v1.2.7

Audio Master 是以 CustomTkinter、Tk 與 FFmpeg 製作的 macOS 音訊工具，整合 LUFS 響度平衡、True Peak 監看、格式轉換，以及多軌非破壞性剪輯。

## 下載

最新版：**[GitHub Releases](https://github.com/Hang-chiu/Audio-Master/releases/latest)**（連結永遠指向最新版本）。

下載 Assets 裡的 `Audio-Master-macOS-AppleSilicon.zip`（僅支援 Apple Silicon，M 系列晶片），並用同一頁的 `SHA256SUMS.txt` 核對檔案完整性。

App 尚未經 Apple 公證，第一次開啟請對 `Audio Master.app` 按右鍵並選擇「打開」，而不是直接雙擊。

## 主要功能

- LUFS 響度量測與目標平衡，支援原始／目標 A/B 試聽及批次增益調整。
- 原始與目標 True Peak（dBTP）顯示，並以顏色提示削波風險。
- 多檔與資料夾匯入、左側資料夾樹、多工作區，以及 `.abproj` 專案存取（每個工作區可各自儲存）。
- 主畫面內嵌 Edit 區與獨立 Edit Window：可共享同一工作區的編輯 Session、選取、Undo／Redo 與播放頭；跨工作區則完整隔離。
- Edit Window 多軌剪輯：Region 選取、移動、分割、合併、剪下、複製、貼上、刪除、拖曳邊緣修剪長度、Undo／Redo。
- Region Fade In／Fade Out 長度與曲線控制，並套用到播放預覽及匯出。
- Snap to Zero Crossings：修剪／分割自動貼齊波形零交越點，避免爆音。
- Flex Time／Flex Pitch：整段 Region 變速（不變調）與變調（不變速），非破壞性。
- Automation：Region 內可畫音量自動化節點，做細部音量調整。
- 每軌 SOLO／MUTE 監聽控制。
- 剪輯引用的外部素材遺失或無法讀取時，預覽、Join 與匯出會顯示明確錯誤，不會悄悄輸出無聲音訊。
- 遺失素材管理：可查看原檔／Region／Join 的影響範圍、單檔或唯一檔名的保守自動 Relink；Collect Project Media 會將實際用到的素材集中複製到 `.abproj` 同層的 `Media/`。
- WAV、AIF、AIFF、FLAC、OGG、M4A、MP3、WMA、AAC、OPUS 轉換，匯出時可保留來源資料夾層級。

## Edit Window 快捷鍵

| 快捷鍵 | 功能 |
| --- | --- |
| `Cmd+1` | 開啟或關閉 Edit Window |
| `Cmd+E` | 主視窗開啟 Edit Window；Edit Window 於播放頭分割 |
| `Space` | 播放 → 暫停 → 從頭播放 |
| `Enter` | 停止並將播放頭移到開頭，不自動播放 |
| `←` / `→` | 播放頭前後移動 1 秒 |
| `Shift+←` / `Shift+→` | 播放頭前後移動 5 秒 |
| `Cmd+X` / `Cmd+C` / `Cmd+V` | 剪下／複製／貼上 |
| `Cmd+Z` / `Cmd+Shift+Z` | Undo／Redo |
| `Cmd+S` | 同步 Edit Window 並儲存專案 |
| `Delete` / `Backspace` | 刪除選取內容 |
| `Cmd+U` | 切換 Cycle Range 循環播放（仿 Logic Pro，以目前的時間範圍選取為循環區間） |
| `Tab` / `Shift+Tab` | 選取目前軌道的下一個／上一個 Region |
| `A` | 切換 Automation（音量自動化節點）顯示與編輯 |
| Option+拖曳 Region | 直接複製出一份並開始搬移 |

## Edit Window 滑鼠操作

- 拖曳 Region 左右邊緣：修剪長度（拖曳時顯示長度／修剪量提示）。
- 將同軌兩個 Region 的邊緣拖到互相重疊：重疊範圍會自動建立 Crossfade；兩段都完整保留，並可用既有黃色曲度控制點調整兩側 Fade Curve。
- 點軌道標頭（非 SOLO/MUTE 按鈕處）：選取該軌所有 Region。
- 框選範圍內有 2 個以上 Region 時，工具列「🔗 合併」會把它們混音成一個新的 Region（另存新檔於 `~/.audio_master_joins/`，不影響原始素材）。
- 工具列「0️⃣ Snap Zero」：開啟後修剪／分割自動貼齊零交越點。
- 工具列「🎛 Flex」：對目前選取的 Region 開啟 Flex Time／Pitch 設定視窗（變速％、音高半音）。
- 按 `A` 顯示 Automation 後：點曲線新增節點、拖曳節點調整音量/時間、雙擊節點刪除。

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
