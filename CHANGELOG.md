# Audio Master v1.2.0

v1.2.0 是一次以剪輯工作流程為核心的大型更新，新增多軌非破壞性 Edit Window、Fade Curve，以及 True Peak 監看。

## 重點更新

- 全新多軌 Edit Window，支援 Region 選取、移動、分割、剪下、複製、貼上、刪除及 Undo／Redo。
- Region 單擊後以黃色外框標示；左右上角可調整 Fade In／Fade Out 長度，黃色控制點可改變曲線曲度。
- 黃框 Region 可直接剪下、複製或刪除；時間範圍選取仍保留原本的 Ripple 編輯行為。
- Fade 設定會保存至專案，並實際套用到播放預覽及匯出結果。
- 每條軌道新增 SOLO／MUTE；支援多軌 Solo，Mute 優先。SOLO／MUTE 只影響 Edit Window 監聽，不改變匯出內容。
- 新增原始與目標 True Peak（dBTP）顯示及風險色彩提示。
- 修正 `Cmd+4` 無法開啟視窗的問題；現在可重複按下來切換 Edit Window 的開啟與關閉。

## Edit Window 操作

- `Space`：播放 → 暫停 → 從頭播放。
- `Enter`：停止並將播放頭移到開頭，不會自動播放。
- `←`／`→`：播放頭前後移動 1 秒。
- `Shift+←`／`Shift+→`：播放頭前後移動 5 秒。
- `Cmd+E`：在 Edit Window 於播放頭分割；在主視窗則開啟 Edit Window。
- `Cmd+X`／`Cmd+C`／`Cmd+V`：剪下／複製／貼上。
- `Cmd+Z`／`Cmd+Shift+Z`：Undo／Redo。
- `Cmd+S`：同步 Edit Window 並儲存專案。
- `Delete`／`Backspace`：刪除選取內容。

## 修正與穩定性

- 修正 Tk 9 將 `Cmd+4` 誤判為滑鼠按鍵，以及不同鍵盤鍵值造成的快捷鍵失效。
- 修正主視窗與 Edit Window 快捷鍵重複觸發、反覆開關後殘留 callback 的問題。
- 修正單擊 Region 時誤移動、產生無意義 Undo，以及重疊 Region 邊界判定。
- 改善 Paste、Split、Trim 與 Ripple Delete 的 Region 及 Fade 繼承。
- 在匯出、切換軌道組或關閉程式前同步最新剪輯資料，避免 Region 或 Fade 設定遺失。
- 修正最後一個 Region 搬走或刪除後，重新開啟專案／匯出時來源音訊意外復原的問題。
- 修正專案重開後，中央清單的 Duration 被背景分析改回來源檔長度。
- 改善快速播放／暫停時的播放頭更新。
- `.abproj` 文件圖示與 App 共用同一圖示，避免打包後修改 App 內容造成簽章失效。

## True Peak 說明

- 原始 True Peak 會在匯入分析時計算並寫入專案。
- 目標 True Peak 依照目標 LUFS 與原始 LUFS 的增益差即時推算。
- True Peak 採分塊 4× 多相 FIR 超取樣，能監看 inter-sample peak，且長音檔不需建立整檔 4× 暫存陣列。
- 此量測適合風險監看，但不等同經認證的 BS.1770 True Peak Meter 或 True Peak Limiter。

## 下載與安裝

- 安裝包：`Audio-Master-macOS-AppleSilicon.zip`
- SHA-256 校驗：`SHA256SUMS.txt`
- 支援：macOS Apple Silicon（M 系列）
- 升級前請先完全結束舊版，再以新版 `Audio Master.app` 取代舊版。
- v1.1.19 的 `.abproj` 可直接開啟；重要專案仍建議先保留備份。
- App 尚未經 Apple 公證。第一次開啟請對 App 按右鍵並選擇「打開」。
