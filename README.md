# Audio Master — 音量平衡輔助化工具

> LUFS Balancer + Converter — 一套桌面工具，用來量測／統一多個音檔的響度（LUFS），並做格式轉換。支援 macOS 與 Windows。

---

## 📥 下載

點這個連結（永遠是最新版）：

### 👉 https://github.com/Hang-chiu/audio-balancer/releases/latest

往下找到 **Assets**，依你的系統選一個下載：

| 你的電腦 | 下載這個 |
|---|---|
| 🪟 Windows | `Audio-Master-Windows.zip` |
| 🍎 Mac（Apple 晶片 M1/M2/M3/M4）| `Audio-Master-macOS-AppleSilicon.zip` |
| 🍎 Mac（Intel）| `Audio-Master-macOS-Intel.zip` |

> 不確定 Mac 是哪一種？左上角  → **關於這台 Mac** → 看「晶片」寫的是 **Apple** 還是 **Intel**。

---

## 🛠 安裝教學

### 🪟 Windows
1. 對 zip 按右鍵 →「**全部解壓縮**」。
2. 進資料夾，雙擊 **`Audio Master.exe`**。
3. 若跳出「**Windows 已保護您的電腦**」→ 點「**更多資訊**」→「**仍要執行**」。

### 🍎 macOS
1. 解壓縮後，把 **`Audio Master.app`** 拖進「**應用程式**」。
2. 第一次打開：**對著 App 按右鍵 →「打開」→ 再按一次「打開」**（請用右鍵，雙擊會被系統擋住）。
3. 如果出現「**App 已損毀，無法打開**」或一直被擋 → 打開「**終端機**」貼上這行、按 Enter，再開一次：
   ```bash
   xattr -cr "/Applications/Audio Master.app"
   ```

> ⚠️ 因為這個 App 沒有付費做 Apple 公證／簽章，第一次開出現警告是**正常**的，照上面步驟放行即可，之後就能直接開。

---

## ✨ 功能簡介

- **LUFS 響度量測與目標化** —— 把多個音檔批次平衡到一致的響度。
- **多工作區（專案）** —— 整個視窗 = 一個專案，可存成 `.abproj`（Cmd/Ctrl+S）、隨時開啟（Cmd/Ctrl+O）。
- **資料夾瀏覽** —— Import File／Import Folder 把來源累積在左側，可隨時移除。
- **波形預覽** —— 單軌／多軌；多選時可直接在波形上點選要播放的音檔。
- **批次 ±Gain Fader** —— 拖曳即時平移選取檔案的目標 LUFS、0 附近有阻尼好歸零。
- **目標 LUFS 滑桿 + 滾輪微調**、**A/B 原始 ↔ 目標 試聽**。
- **格式／取樣率轉換、靜音移除、輸出裝置選擇**。

---

<sub>以上下載警告皆因 App 未經 Apple／Microsoft 付費簽章所致，屬正常現象。</sub>
