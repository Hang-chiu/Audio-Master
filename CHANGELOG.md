# Audio Master v1.2.2

v1.2.2 大幅升級 Edit Window，往 Logic Pro 的多軌剪輯體驗再靠近一步，同時修正幾個匯出／互動細節問題。

## 更新內容

- 🎛✂️🎚 Edit Window 大幅升級：新增 Flex Time（變速不變調）／Flex Pitch（變調不變速）、Automation 音量自動化節點、合併（Join）多個 Region 混音、Snap to Zero Crossings 修剪自動貼齊零交越點、拖曳修剪即時顯示長度提示、Tab／Shift+Tab 選取上下一個 Region、Option+拖曳直接複製 Region、波形振幅縮放。
- 同軌 Region 互相重疊時，改成拖過去的那一段直接覆蓋掉被疊到的部分（仿真實 DAW 行為），拖曳中即時疊在最上層，放開後底下被蓋到的部分自動修剪/挖洞/移除。
- Target／Gain 數值欄位（主畫面、Edit Window 都有）可以直接按住數值垂直拖曳調整；Edit Window 開著時，主畫面選取哪些音檔，Edit Window 音軌就完全換成那些檔案，點回主畫面也會立刻把 Edit Window 的調整覆寫回去；調整 Gain／Target 時該軌波形會即時放大/縮小反映音量變化。
- Edit Window 工具列重新分成兩列，修正部分按鈕因視窗塞不下而點不到的問題；移除 Fade In／Fade Out 快速按鈕（可直接拖曳 Region 角落把手設定）。
- 修正音檔明明已經匯入、卻打不開 Edit Window 的問題。
- 新增「公版」輸出格式選單（法規(阿波羅)／珍寶(D27)／HRG／iGaming），獨立於一般輸出格式之外。
- 匯出流程簡化為單一確認訊息；原始格式（不轉檔）現在也能正常匯出。
- Undo 現在也能復原刪除檔案／資料夾。
- 修正中間工作區原始／目標 True Peak 欄位擋住點擊選取、擋住滑鼠滾輪捲動的問題；加了直向捲軸。
- 批次 ±Gain 改名為「Gain」，LUFS 數值前加上「Target」標籤；位元率／位元深度選單現在一直都能選。

# Audio Master v1.2.1

v1.2.1 針對 v1.2.0 的剪輯工作流程做了幾項體感優化與修正。

## 更新內容

- ✂️🎬✨ 全新排版與功能：Edit Window 編輯模式登場！剪輯結果即時套用——編輯完成後主畫面的波形、時間顯示與播放內容會立刻更新，不用等匯出才看得到。
- Loop 循環播放改為真正無縫：不管從整段的哪個時間點開始播放，播到底都會立即接回開頭，不再卡頓。
- 中央工作區左右橫向捲動（滑鼠滾輪／觸控板兩指滑動）改成平滑跟手，不再一格一格卡著跳。
- True Peak 數值已更新至中間工作區、音量表即時運算：修正原始／目標 True Peak 欄位彩色數字在捲動時的「貼圖延遲」問題，捲動時會即時跟上表格位置。

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
