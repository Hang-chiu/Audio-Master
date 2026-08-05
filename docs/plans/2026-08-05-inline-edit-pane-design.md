# 主畫面內嵌 Edit 區域設計

## 背景與目標

目前多軌剪輯只能透過 Cmd+1 開啟獨立的 `EditWindow`（`ctk.CTkToplevel`）。使用者希望比照 Logic Pro，能在主畫面下方（中央檔案表格區塊下方）直接開一塊內嵌編輯區，用快捷鍵 `X` 開關，並且跟原本的 Cmd+1 獨立視窗可以同時開、即時同步同一份音軌內容（在其中一邊拖曳 Region，另一邊立刻跟著動）。

Cmd+1 獨立視窗維持不變、必須保留。

## 架構：方案 A — 共用 EditSession

考慮過三種做法：

1. **（採用）拆出共用的 `EditSession`，`EditWindow` 開兩份、共用一份資料** — 用 `@property` 讓 `EditWindow` 既有的 `self.tracks`、`self.playhead` 等寫法不變，底層轉發到共用的 `EditSession`。內嵌區與獨立視窗是同一個 `EditWindow` 類別的兩個實例。改動集中、風險最低。
2. 內嵌區另外寫一個精簡版編輯器（`EditPane`）——會導致波形繪製等畫面邏輯重複一份，兩處各自維護容易分岔，且使用者要求功能對等（工具列完整搬過來），重複度會更高。不採用。
3. 真的把獨立視窗的畫面「搬」進主畫面——Tk widget 建立時就綁定所屬視窗，無法事後搬遷，只能銷毀重建，等於繞回方案 2 的重複問題。技術上不成立，排除。

### EditSession（新增，純資料層，不碰 Tk）

持有原本活在 `EditWindow.__init__` 的狀態：`tracks`、`selection`、`active_region`、`selected_regions`、`playhead`、`playhead_track`、`cycle_range`、`cycle_enabled`、`clipboard`、`undo_stack`、`redo_stack`、`is_playing`、`transport_state`、`_play_generation`。

新增職責：

- `views: list[EditWindow]` — 目前綁在這份 session 上、活著的畫面實例（0、1 或 2 個）
- `notify(exclude=None)` — 資料變動後呼叫，讓所有註冊的 view（排除觸發變動的那個）呼叫既有的 `redraw()`
- 播放引擎收斂到這裡：`play()` / `pause()` / `stop()` / `_tick()`，`sd.play(...)` 只在這裡呼叫一次，避免兩邊同時出聲音打架。`_tick()` 以 60fps 更新播放頭並呼叫 `notify()`，兩邊畫面用同一顆播放頭同步跑格。

### EditWindow（既有類別，改動集中在開頭）

`__init__(self, app, session=None, embed_parent=None)`：

- `session` 未給 → 建新的 `EditSession()`（獨立視窗今天的路徑，行為不變）；給了就重用並把自己加進 `session.views`
- `embed_parent` 未給 → 沿用 `ctk.CTkToplevel`；給了 → 改用 `ctk.CTkFrame(embed_parent)`，並跳過僅 Toplevel 適用的 9 處呼叫（`title`/`geometry`/`protocol`/`deiconify`/`lift`/`focus_force`/`destroy` 等，以 `self._is_embedded` 判斷分流）

類別開頭定義一批 `@property`（對應上述共用狀態），getter/setter 轉發到 `self._session.xxx`，中間既有大量直接讀寫 `self.tracks`/`self.playhead` 的程式碼不需改寫。`redraw()` 結尾補一行 `self._session.notify(exclude=self)`。

## 版面配置

每個工作區既有的 `center_panel_inner`（檔案表格容器，本來就逐工作區獨立）內，把檔案表格與新的內嵌編輯區包進一個垂直 `tk.PanedWindow`（跟主畫面三欄式版面同一手法），使用者可拖曳分隔線調整內嵌區高度。內嵌區關閉時整個從 PanedWindow 移除，不是隱藏佔位。首次開啟給一個能同時看到工具列與幾軌波形的預設高度；使用者手動拖過的高度在同一次執行期間記住（不跨重啟持久化，非本次目標）。

內嵌區頂端加一條標題列：顯示檔名 + 一個 × 關閉鈕，供滑鼠操作習慣的使用者不依賴快捷鍵也能關閉。

## X 快捷鍵行為

綁在主視窗層級，沿用 `_is_frontmost()` + 文字輸入框排除判斷（跟現有 Cmd+Z 同一套防呆）。純切換：

- 未開啟 → 依目前選取解析要編輯的檔案（沿用 `_open_edit_window` 現成的「選取的檔案 → 目前主檔 → 整個工作區」邏輯），開啟內嵌區
- 已開啟 → 關閉並寫回

## 與 Cmd+1 的共存規則

因為內嵌區的 Tk 元件掛在各工作區自己的容器下，切換工作區分頁時會自動跟著隱藏/顯示（沿用既有 `grid_remove()`/`grid()` 機制），不需額外程式碼。

兩個快捷鍵各自維護「目前開的是哪個 view」。任一邊被觸發時，若發現另一邊當下開著的音軌路徑跟這次要開的完全一致，兩邊接上同一份 `EditSession`（畫面同步）；路徑對不上則各自獨立開自己的一份，不強行同步不相關的內容。

## 關閉、播放、焦點同步細節

**關閉行為**：任一邊關閉只把自己從 `EditSession.views` 移除、銷毀自己的 Tk 元件，`EditSession` 本身不受影響，另一邊繼續正常運作、undo 歷史完整保留。只有關閉**最後一個** view 時才觸發既有的 `sync_entries()` 寫回 `app.audio_files`，然後才捨棄整個 `EditSession`。今天「關閉唯一視窗＝寫回＋結束」的行為完全不變，只是泛化成「關閉最後一個 view」。

**播放單一入口**：`sd.play()` 只在 `EditSession.play()` 呼叫一次，不論從哪一邊觸發播放都走同一入口，不會兩份聲音疊在一起。

**`_is_frontmost()` 是本次最大技術風險點**：現有判斷法（比對目前鍵盤焦點的 Toplevel 是否等於 `self.win`）是建立在「全世界只會存在一個 `EditWindow` 實例」的前提上。方案 A 打破這個前提後，內嵌 view 沒有自己的 Toplevel 可比對，必須改成「焦點是否落在自己的元件範圍內」；獨立視窗那邊的判斷邏輯維持不變。此處沒處理好，空白鍵、⌘Z 等全域快捷鍵會出現跟先前滾輪 bug 同類型的誤觸／收不到問題，因此列為實作前就要鎖住的一級注意事項。

## 測試規劃

**單元測試（純資料層，fake-object 風格，不開真視窗）**

- `EditSession` property 轉發正確性：兩個 `EditWindow` 實例共用一個 session，一邊寫入另一邊讀到同一份資料
- `notify()`：一邊觸發變動，另一邊（且只有另一邊）的 `redraw()` 被呼叫
- view 進出計數：關閉非最後一個 view 時 session 存活、`sync_entries()` 不觸發；關閉最後一個才觸發
- 內嵌 view 版本的 `_is_frontmost()`：焦點在自己容器內回 True、在另一個 view 容器內回 False

**既有回歸測試（必須原樣通過）**

`test_multiselect.py`、`test_batch_target_gain.py`、`test_tp_overlay_pool.py`、`test_touchpad_scroll.py` —— 測的邏輯跟資料來源是不是同一個 session 無關，方案 A 不改內部程式碼寫法，理論上不受影響，仍須實跑確認。

**真機手動驗證清單**

1. X 開關內嵌區、Cmd+1 開關獨立視窗，各自能正常開關
2. 兩邊同時開著時，一邊拖曳 Region，另一邊即時跟著動
3. 播放時只有一份聲音，兩邊播放頭同步跑
4. 關掉一邊，音軌沒有從另一邊消失、undo 歷史沒斷
5. 兩邊都關掉後，剪輯/音量調整正確寫回主表格
6. 切換工作區分頁時，內嵌區正確跟著隱藏/顯示，不會誤跳出別工作區資料
7. 空白鍵、⌘Z 等快捷鍵在兩邊各自聚焦時只觸發該邊，不互相誤觸

## 明確排除範圍（非本次目標）

- 內嵌區高度跨應用程式重啟持久化
- 獨立視窗在切換工作區分頁時自動關閉/刷新（既有行為維持現狀，不在本次修改）
- 內嵌區精簡化工具列（已決議功能對等，完整搬過來）
