import os
import sys
import json
import shutil
import subprocess
import tempfile
import threading
import numpy as np
import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, simpledialog, messagebox
from tkinter import ttk
from tkinter import font as tkfont
from pathlib import Path
from PIL import Image, ImageDraw, ImageTk
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DND_AVAILABLE = True
except Exception:
    _DND_AVAILABLE = False
from pydub import AudioSegment
import pyloudnorm as pyln
import sounddevice as sd
from scipy.signal import resample_poly
import queue
import time
from datetime import datetime
import concurrent.futures
import math
import uuid
import traceback
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

APP_VERSION = "1.2.2"
_TRUE_PEAK_CHUNK_FRAMES = 262_144
_TRUE_PEAK_OVERLAP_FRAMES = 64

# 每個版本開啟 App 時要顯示的「新功能」提要；沒有列在這裡的版本開啟時不會彈窗。
WHATS_NEW_NOTES = {
    "1.2.0": [
        "全新多軌 Edit Window：Region 選取、移動、分割、剪下／複製／貼上、Undo／Redo（Cmd+4 開關視窗）。",
        "Region 可調整 Fade In／Fade Out 長度與曲線曲度，設定會保存並套用到播放預覽及匯出結果。",
        "每條軌道新增 SOLO／MUTE（只影響 Edit Window 監聽，不影響匯出內容）。",
        "新增原始與目標 True Peak（dBTP）顯示與風險色彩提示。",
        "修正 Cmd+4 無法開啟視窗的問題，現在可重複按來切換 Edit Window 的開啟與關閉。",
    ],
    "1.2.1": [
        "✂️🎬✨ 全新排版與功能：Edit Window 編輯模式登場！剪輯結果即時套用——編輯完成後主畫面的波形、時間顯示與播放內容會立刻更新，不用等匯出才看得到。",
        "Loop 循環播放改為真正無縫：不管從整段的哪個時間點開始播放，播到底都會立即接回開頭，不再卡頓。",
        "中央工作區左右橫向捲動（滑鼠滾輪／觸控板兩指滑動）改成平滑跟手，不再一格一格卡著跳。",
        "True Peak 數值已更新至中間工作區、音量表即時運算：修正原始／目標 True Peak 欄位彩色數字在捲動時的『貼圖延遲』問題，捲動時會即時跟上表格位置。",
    ],
    "1.2.2": [
        "🎛✂️🎚 Edit Window 大幅升級，仿 Logic Pro Audio Track Editor：新增 Flex Time（變速不變調）／Flex Pitch（變調不變速）、Automation 音量自動化節點、合併（Join）多個 Region 混音、Snap to Zero Crossings 修剪自動貼齊零交越點、拖曳修剪即時顯示長度提示、Tab／Shift+Tab 選取上下一個 Region、Option+拖曳直接複製 Region、波形振幅縮放。",
        "同軌 Region 互相重疊時，改成拖過去的那一段直接覆蓋掉被疊到的部分（仿真實 DAW 行為）：拖曳中就會即時疊在最上層，放開後底下被蓋到的 Region 自動修剪/挖洞/移除，不會再變成兩段音訊疊在一起變吵。",
        "Target／Gain 數值欄位（主畫面右側面板、Edit Window 都有）現在可以直接按住數值垂直拖曳調整：往上拖＝加、往下拖＝減，不用再找滑桿；Edit Window 的調整會即時覆寫回主畫面的檔案與表格。",
        "Edit Window 開著時，主畫面中間表格選取哪些音檔，Edit Window 裡的音軌就完全換成那些檔案（仿 Logic Pro 點列表換上方 Editor 內容），不再只是標亮；切換前會先把原本音軌的編輯內容存回去，不會遺失。反過來，點回主畫面時（不管有沒有換選取）也會立刻把 Edit Window 裡任何 Gain／Target／剪輯調整覆寫回主畫面。另外在 Edit Window 裡調整任何音檔的 Gain 或 Target，該軌波形也會即時放大/縮小反映音量變化。",
        "Edit Window 工具列重新分成兩列，修正部分按鈕（Undo／Redo 之後、Snap Zero／Flex／Automation）因為視窗塞不下而點不到的問題；同時移除 Fade In／Fade Out 快速按鈕（淡入淡出還是可以直接拖曳 Region 角落的把手設定）。",
        "修正音檔明明已經匯入、卻打不開 Edit Window（提示『請先匯入至少一個已分析完成的音檔』）的問題。",
        "新增「公版」輸出格式選單（法規(阿波羅)／珍寶(D27)／HRG／iGaming），獨立於一般輸出格式之外，選一下就自動套好格式、取樣率、位元率。",
        "匯出流程簡化：有多個工作區時只匯出目前工作區已勾選的檔案，用單一確認訊息取代原本的多步驟對話框；原始格式（不轉檔）現在也能正常匯出。",
        "Undo 現在也能復原刪除檔案／資料夾，不再只能復原參數調整。",
        "修正中間工作區原始／目標 True Peak 欄位擋住點擊選取、擋住滑鼠滾輪捲動（上下與 Shift+左右）的問題。",
        "中間工作區加了直向捲軸；「全選」勾選圖示統一成跟其他勾選框一樣的樣式。",
        "批次 ±Gain 改名為「Gain」，LUFS 數值前加上「Target」標籤；位元率／位元深度選單現在一直都能選，不受格式影響。",
    ],
}

# ── FFmpeg 整合（來自 音檔批次轉換工具）────────────────────────
LOSSLESS_FORMATS = {"wav", "aif", "aiff", "flac"}
LOSSY_FORMATS    = {"ogg", "m4a", "mp3", "wma", "aac", "opus"}
SAMPLE_RATES     = ["Original", "8000", "11025", "22050", "24000", "32000", "44100", "48000", "96000"]
BITRATES         = ["Original", "32", "48", "64", "80", "96", "105", "112", "128", "160", "192", "224", "256", "320"]
BIT_DEPTHS       = ["Original", "16", "24", "32"]  # 無損格式(wav/aif/aiff/flac)用的位元深度選項

# 公版輸出規格：獨立於「輸出格式」選單之外的另一個選單，選取後直接把格式／取樣率／
# 位元率(深度)一次套成公版內容，見 _on_preset_changed。PRESET_PLACEHOLDER 是公版選單
# 沒有套用中任何公版時顯示的字，選完公版後選單也會立刻跳回這個字（公版只是「套用一次」
# 的動作，不是持續套用的模式，避免使用者事後又手動改了取樣率/位元率，選單卻還顯示著
# 某個公版名稱、誤以為目前仍完全符合該規格）。
PRESET_PROFILES = {
    "法規(阿波羅)": {"format": "WAV", "sr": "44100", "bit": "16"},
    "珍寶(D27)":    {"format": "WAV", "sr": "44100", "bit": "16"},
    "HRG":          {"format": "MP3", "sr": "44100", "bit": "96"},
    "iGaming":      {"format": "MP3", "sr": "48000", "bit": "105"},
}
PRESET_PLACEHOLDER = "公版格式"
PRESET_OPTIONS  = [PRESET_PLACEHOLDER] + list(PRESET_PROFILES.keys())
OUTPUT_FORMATS   = ["Original", "WAV", "AIF", "AIFF", "FLAC", "OGG", "M4A", "MP3", "WMA", "AAC", "OPUS"]

CODEC_MAP = {
    "wav": "pcm_s16le", "aif": "pcm_s16le", "aiff": "pcm_s16le",
    "flac": "flac", "ogg": "libvorbis", "m4a": "aac",
    "mp3": "libmp3lame", "wma": "wmav2", "aac": "aac", "opus": "libopus",
}
CONTAINER_MAP = {
    "aif": "aiff", "aiff": "aiff",
    "m4a": "ipod",   # .m4a 容器 → FFmpeg 的 ipod muxer（沒有名為 m4a 的 muxer）
    "aac": "adts",   # 原始 AAC → adts muxer
    "wma": "asf",    # .wma → asf muxer
}

# 可匯入的音檔副檔名：由格式集合推導，所有匯入點（資料夾／檔案／拖放／樹拖曳）共用同一份，
# 避免各處硬編一份且彼此不一致（先前 .opus/.wma/.aac 能輸出卻匯不進、拖放連 .ogg/.m4a 都被擋）。
IMPORTABLE_EXTS = tuple("." + e for e in sorted(LOSSLESS_FORMATS | LOSSY_FORMATS))

def _bundled_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent))

def find_ffmpeg():
    bundled = _bundled_dir()
    candidates = [
        str(bundled / "ffmpeg"),
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/usr/bin/ffmpeg",
    ]
    try:
        candidates[1:1] = [str(p) for p in sorted(bundled.glob("ffmpeg*")) if p.is_file()]
    except Exception:
        pass
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    found = shutil.which("ffmpeg")
    return found if found else None

FFMPEG_BIN = find_ffmpeg()
# 重要：find_ffmpeg() 找到的路徑（含 App 包裡自帶的 ffmpeg）本身不會自動生效——pydub
# 內部解碼/匯出（AudioSegment.from_file／.export）預設只會呼叫裸指令字串 "ffmpeg"，
# 交給 subprocess 當下的 PATH 去找。用 Finder 雙擊打開 App（而不是從 Terminal 用 open
# 開）時，PATH 往往不含 Homebrew 的 /opt/homebrew/bin，pydub 就會完全找不到 ffmpeg、
# AudioSegment.from_file 靜默失敗——這正是「音檔明明在清單裡卻解碼不出來」的真正原因，
# 且只有在雙擊啟動時才會發生，從 Terminal 開發環境測試永遠不會踩到。直接把已經解析好的
# 絕對路徑指定給 AudioSegment.converter，pydub 就不再需要依賴 PATH 環境變數。
if FFMPEG_BIN:
    AudioSegment.converter = FFMPEG_BIN

def find_ffprobe():
    bundled = _bundled_dir()
    candidates = []
    if FFMPEG_BIN:
        candidates.append(str(Path(FFMPEG_BIN).with_name("ffprobe")))
    candidates += [
        str(bundled / "ffprobe"),
        "/opt/homebrew/bin/ffprobe",
        "/usr/local/bin/ffprobe",
        "/usr/bin/ffprobe",
    ]
    try:
        candidates[1:1] = [str(p) for p in sorted(bundled.glob("ffprobe*")) if p.is_file()]
    except Exception:
        pass
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    found = shutil.which("ffprobe")
    return found if found else None

FFPROBE_BIN = find_ffprobe()

def _available_encoders(ffmpeg_bin):
    """回傳此 ffmpeg build 實際可用的 audio encoder 名稱集合（用來偵測缺漏的編碼器）。"""
    if not ffmpeg_bin:
        return set()
    try:
        res = subprocess.run([ffmpeg_bin, "-hide_banner", "-encoders"],
                             capture_output=True, text=True, timeout=10)
        out = res.stdout or ""
    except Exception:
        return set()
    names = set()
    started = False
    for line in out.splitlines():
        if not started:
            if set(line.strip()) == {"-"}:   # encoders 清單前的 ------ 分隔線
                started = True
            continue
        parts = line.split()
        # 每列格式： <6 個旗標字元> <encoder 名稱> <描述...>，旗標首字元 A/V/S 代表類型
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "AVS":
            names.add(parts[1])
    return names

AVAILABLE_ENCODERS = _available_encoders(FFMPEG_BIN)
# OGG 預設用 libvorbis，但部分 ffmpeg build（如未 --enable-libvorbis 的 Homebrew）沒有它，
# 只剩原生 'vorbis' encoder → 沒 fallback 會直接匯出失敗且零檔。這裡自動退回原生 vorbis。
if AVAILABLE_ENCODERS and "libvorbis" not in AVAILABLE_ENCODERS and "vorbis" in AVAILABLE_ENCODERS:
    CODEC_MAP["ogg"] = "vorbis"

def _probe_audio_bit_depth(path):
    """用 ffprobe 讀來源音訊位深；24-bit WAV 在 pydub 內會變 32-bit，需靠來源檔判斷。"""
    if not path or not os.path.isfile(path):
        return None
    if FFPROBE_BIN:
        try:
            res = subprocess.run(
                [FFPROBE_BIN, "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=bits_per_raw_sample,bits_per_sample,sample_fmt,codec_name",
                 "-of", "json", path],
                capture_output=True, text=True, timeout=10
            )
            if res.returncode == 0:
                streams = json.loads(res.stdout or "{}").get("streams") or []
                if streams:
                    stream = streams[0]
                    for key in ("bits_per_raw_sample", "bits_per_sample"):
                        val = stream.get(key)
                        try:
                            bits = int(val)
                        except (TypeError, ValueError):
                            continue
                        if bits in (8, 16, 24, 32):
                            return bits
        except Exception:
            pass
    if FFMPEG_BIN:
        try:
            res = subprocess.run([FFMPEG_BIN, "-hide_banner", "-i", path],
                                 capture_output=True, text=True, timeout=10)
            info = (res.stderr or "") + "\n" + (res.stdout or "")
            for bits in (32, 24, 16, 8):
                if f"({bits} bit)" in info:
                    return bits
            for token, bits in (("pcm_s24", 24), ("pcm_u24", 24),
                                ("pcm_s16", 16), ("pcm_u8", 8),
                                ("pcm_s32", 32), ("pcm_f32", 32)):
                if token in info:
                    return bits
        except Exception:
            pass
    return None

def _audio_bit_depth(audio):
    try:
        bits = int(getattr(audio, "sample_width", 0)) * 8
    except Exception:
        return None
    return bits if bits in (8, 16, 24, 32) else None

def _pcm_codec_for(fmt_key, sample_width, source_bit_depth=None):
    bits = source_bit_depth if source_bit_depth in (8, 16, 24, 32) else None
    if bits is None:
        bits = (int(sample_width) * 8) if sample_width else 16
    if fmt_key == "wav":
        return {8: "pcm_u8", 16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_s32le"}.get(bits, "pcm_s16le")
    if fmt_key in ("aif", "aiff"):
        return {8: "pcm_s8", 16: "pcm_s16be", 24: "pcm_s24be", 32: "pcm_s32be"}.get(bits, "pcm_s16be")
    return CODEC_MAP.get(fmt_key, fmt_key)

def _output_path_key(path):
    """本次匯出用的路徑去重 key；casefold 可避開 macOS 常見大小寫不分磁碟互蓋。"""
    return os.path.normpath(os.path.abspath(path)).casefold()

def _make_temp_output_path(save_path):
    out_dir = os.path.dirname(save_path) or "."
    stem, ext = os.path.splitext(os.path.basename(save_path))
    fd, tmp_path = tempfile.mkstemp(prefix=f".{stem}.", suffix=ext or ".tmp", dir=out_dir)
    os.close(fd)
    return tmp_path

SEMANTIC_TARGETS = {
    "bgm": -21.0, "freebgm": -14.0, "basebgm": -21.0,
    "scoring": -21.0, "freescoring": -16.5,
    "result": -13.0, "freeresult": -13.0,
    "featurewin": -8.0, "bigwin": -14.0, "megawin": -12.0, "jumbowin": -10.0,
    "bonusretrigger": -8.0,
    "transition": -22.0, "transitionbgm": -22.0,
    "spinstop": -30.0, "scatter": -12.0,
    "start": -12.0, "freestart": -12.0,
    "lock": -19.0, "featurelock": -19.0,
}

ctk.set_appearance_mode("Dark")

COLOR_BG = "#1A1A1D"
COLOR_PANEL = "#2C2C2E"
COLOR_CYAN = "#00E5FF"
COLOR_RED = "#FF3B30"
COLOR_TEXT_DIM = "#8E8E93"
COLOR_SELECTED = "#103A40"

# 中央工作區勾選框底層狀態 tag（取代直接用文字 emoji 判斷，見 _get_check/_set_check）
_CHECK_TAG_ON = "chk_on"
_CHECK_TAG_OFF = "chk_off"
_CHECK_TAGS = (_CHECK_TAG_ON, _CHECK_TAG_OFF)

# Edit Window 音軌底色（依序輪流指派），暗色系但彼此可辨識，跟波形藍/播放桿青對比足夠。
EDIT_TRACK_COLORS = [
    "#2A4D6E", "#2E6E4D", "#6E5A2A", "#5A2A6E",
    "#6E2A3D", "#2A6E6E", "#5A6E2A", "#6E3D2A",
]


def _bind_drag_scrub(entry, on_drag_delta, sensitivity=0.05, threshold_px=3):
    """讓數值輸入框可以直接按住滑鼠垂直拖曳調整數值：往上拖＝加、往下拖＝減，
    仿主流專業音訊軟體（Logic／Pro Tools／各種外掛）數值欄位的手感，不用先點滑桿。
    小幅移動（沒超過門檻）仍當成一般點擊，可以正常把游標點進去打字選字，不會被
    誤判成拖曳；一旦超過門檻才開始送出增減量，並回傳 "break" 蓋掉輸入框內建的
    拖曳選字行為（否則會一邊拖曳調整數值、一邊選取文字，畫面會很奇怪）。"""
    state = {"active": False, "moved": False, "last_y": 0.0}

    def on_press(event):
        state["active"] = True
        state["moved"] = False
        state["last_y"] = event.y_root

    def on_motion(event):
        if not state["active"]:
            return None
        dy_total = state["last_y"] - event.y_root
        if not state["moved"]:
            if abs(dy_total) < threshold_px:
                return None
            state["moved"] = True
        dy = state["last_y"] - event.y_root
        state["last_y"] = event.y_root
        if dy:
            on_drag_delta(dy * sensitivity)
        return "break"

    def on_release(event):
        state["active"] = False

    entry.bind("<ButtonPress-1>", on_press, add="+")
    entry.bind("<B1-Motion>", on_motion, add="+")
    entry.bind("<ButtonRelease-1>", on_release, add="+")


def _clamp_fade_curve(value):
    """Fade 曲度的共用資料範圍；舊專案或異常值一律安全退回線性。"""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value):
        return 0.0
    return max(-1.0, min(1.0, value))


def _fade_curve_gain(progress, curve):
    """把 0..1 的 fade 進度轉成實際增益。

    curve=0 完全等同既有線性 fade；正值往 full-gain 上拱，負值往 silence 下凹。
    畫面、播放預覽與匯出共同使用這個函式，確保看見的曲線就是聽見的曲線。
    """
    curve = _clamp_fade_curve(curve)
    progress_arr = np.clip(np.asarray(progress, dtype=np.float64), 0.0, 1.0)
    if abs(curve) < 1e-7:
        result = progress_arr
    else:
        k = -4.0 * curve
        result = np.expm1(k * progress_arr) / math.expm1(k)
    if np.ndim(progress_arr) == 0:
        return float(result)
    return result


def _envelope_gain_curve(gain_nodes, n_samples, sr):
    """把 Region 的 Automation 節點（[time_sec, gain_db], ...）換算成逐取樣點的線性增益陣列，
    節點之間線性內插（在 dB 域內插，比較符合人耳對音量變化的感受），第一個節點之前／
    最後一個節點之後維持該節點的值不變（hold）。沒有節點就回傳 None（表示不用套用）。"""
    if not gain_nodes:
        return None
    nodes = sorted(gain_nodes, key=lambda p: p[0])
    times = np.array([p[0] for p in nodes], dtype=np.float64)
    dbs = np.array([p[1] for p in nodes], dtype=np.float64)
    sample_times = np.arange(n_samples, dtype=np.float64) / sr
    interp_db = np.interp(sample_times, times, dbs, left=dbs[0], right=dbs[-1])
    return (10.0 ** (interp_db / 20.0)).astype(np.float64)


def _stft_phase_vocoder_stretch_mono(samples, rate, n_fft=2048, hop_div=4):
    """STFT phase vocoder：把單聲道 float 樣本依 rate 變速不變調（Flex Time 核心）。
    rate>1＝拉長變慢，rate<1＝縮短變快。純 numpy 實作，不額外依賴 librosa/rubberband
    這類需要另外裝系統套件的函式庫，PyInstaller 打包才不會多一層風險。"""
    samples = np.asarray(samples, dtype=np.float64)
    if samples.size == 0 or abs(rate - 1.0) < 1e-6:
        return samples.astype(np.float32, copy=True)

    hop = n_fft // hop_div
    window = np.hanning(n_fft)
    pad = n_fft
    padded = np.concatenate([np.zeros(pad), samples, np.zeros(pad + n_fft)])
    n_frames = 1 + (len(padded) - n_fft) // hop
    target_len = max(1, int(round(len(samples) * rate)))
    if n_frames < 2:
        idx = np.linspace(0, len(samples) - 1, target_len)
        return np.interp(idx, np.arange(len(samples)), samples).astype(np.float32)

    mag = np.empty((n_frames, n_fft // 2 + 1))
    phase = np.empty((n_frames, n_fft // 2 + 1))
    for i in range(n_frames):
        seg = padded[i * hop: i * hop + n_fft] * window
        spec = np.fft.rfft(seg)
        mag[i] = np.abs(spec)
        phase[i] = np.angle(spec)

    out_n_frames = max(1, int(round((n_frames - 1) / rate)) + 1)
    time_steps = np.linspace(0, n_frames - 1, out_n_frames)
    omega = 2 * np.pi * hop * np.arange(n_fft // 2 + 1) / n_fft

    out_len = (out_n_frames - 1) * hop + n_fft
    out = np.zeros(out_len)
    win_sum = np.zeros(out_len)

    phase_acc = phase[0].copy()
    for i, t in enumerate(time_steps):
        i0 = int(np.floor(t))
        i1 = min(i0 + 1, n_frames - 1)
        frac = t - i0
        mag_i = mag[i0] * (1 - frac) + mag[i1] * frac
        if i > 0:
            dphase = (phase[i1] - phase[i0]) - omega
            dphase = dphase - 2 * np.pi * np.round(dphase / (2 * np.pi))
            phase_acc = phase_acc + omega + dphase
        spec = mag_i * np.exp(1j * phase_acc)
        seg = np.fft.irfft(spec, n=n_fft) * window
        start = i * hop
        out[start:start + n_fft] += seg
        win_sum[start:start + n_fft] += window ** 2

    win_sum[win_sum < 1e-8] = 1e-8
    out = out / win_sum

    lead = pad
    out = out[lead: lead + target_len]
    if len(out) < target_len:
        out = np.pad(out, (0, target_len - len(out)))
    return out.astype(np.float32)


def _flex_time_stretch(seg, rate):
    """幫 mono 或 (n, ch) 多聲道陣列套用 phase vocoder 變速（逐聲道處理）。"""
    if seg.size == 0 or abs(rate - 1.0) < 1e-6:
        return seg.astype(np.float64, copy=True)
    if seg.ndim == 1:
        return _stft_phase_vocoder_stretch_mono(seg, rate).astype(np.float64)
    channels = [
        _stft_phase_vocoder_stretch_mono(seg[:, c], rate)
        for c in range(seg.shape[1])
    ]
    n = min(len(c) for c in channels)
    return np.stack([c[:n] for c in channels], axis=1).astype(np.float64)


def _flex_pitch_shift(seg, semitones):
    """Pitch shift：先用同一個 phase vocoder 依 2**(semitones/12) 變速，再內插回原長度
    （時間拉伸＋重取樣是不需要額外函式庫的經典 pitch shift 做法）。"""
    if seg.size == 0 or abs(semitones) < 1e-6:
        return seg.astype(np.float64, copy=True)
    ratio = 2.0 ** (semitones / 12.0)
    stretched = _flex_time_stretch(seg, ratio)
    orig_len = len(seg)
    if len(stretched) < 2:
        return seg.astype(np.float64, copy=True)
    idx = np.linspace(0, len(stretched) - 1, orig_len)
    if stretched.ndim == 1:
        return np.interp(idx, np.arange(len(stretched)), stretched)
    return np.stack(
        [np.interp(idx, np.arange(len(stretched)), stretched[:, c]) for c in range(stretched.shape[1])],
        axis=1,
    )


class EditRegion:
    """Edit Window 裡的一段非破壞性音訊片段：指到某個來源檔的 [src_start, src_end)，
    放在自己軌道時間軸上的 track_offset 位置，可各自套用淡入/淡出。
    source_path 不一定等於這軌本身的檔案——貼上其他軌複製的音訊時會指向別的來源檔。"""

    __slots__ = (
        "source_path", "src_start", "src_end", "track_offset",
        "fade_in", "fade_out", "fade_in_curve", "fade_out_curve",
        "time_stretch_ratio", "pitch_semitones", "gain_nodes",
    )

    def __init__(self, source_path, src_start, src_end, track_offset,
                 fade_in=0.0, fade_out=0.0, fade_in_curve=0.0, fade_out_curve=0.0,
                 time_stretch_ratio=1.0, pitch_semitones=0.0, gain_nodes=None):
        self.source_path = source_path
        self.src_start = src_start
        self.src_end = src_end
        self.track_offset = track_offset
        self.time_stretch_ratio = max(0.25, min(4.0, float(time_stretch_ratio)))
        self.pitch_semitones = max(-24.0, min(24.0, float(pitch_semitones)))
        self.fade_in = max(0.0, min(self.playback_length, float(fade_in)))
        self.fade_out = max(0.0, min(self.playback_length, float(fade_out)))
        self.fade_in_curve = _clamp_fade_curve(fade_in_curve)
        self.fade_out_curve = _clamp_fade_curve(fade_out_curve)
        # Automation：[[time_sec, gain_db], ...]，time 是「播放時間」(0~playback_length)，
        # 依 time 排序；空list＝沒有自動化，維持既有行為（見 _envelope_gain_curve）。
        self.gain_nodes = [[float(t), float(db)] for t, db in gain_nodes] if gain_nodes else []

    @property
    def length(self):
        """來源音訊被這個 Region 用掉的長度（秒）——Flex Time 拉伸前的原始長度。"""
        return max(0.0, self.src_end - self.src_start)

    @property
    def playback_length(self):
        """這個 Region 實際在時間軸上占用的長度（秒）：套用 Flex Time 拉伸倍率之後的長度，
        Region 的定位／熱區／畫面寬度都用這個，不要用 length（那是來源音訊本身的長度）。"""
        return self.length * self.time_stretch_ratio

    def to_dict(self):
        return {
            "source_path": self.source_path, "src_start": self.src_start, "src_end": self.src_end,
            "track_offset": self.track_offset, "fade_in": self.fade_in, "fade_out": self.fade_out,
            "fade_in_curve": self.fade_in_curve, "fade_out_curve": self.fade_out_curve,
            "time_stretch_ratio": self.time_stretch_ratio, "pitch_semitones": self.pitch_semitones,
            "gain_nodes": [[t, db] for t, db in self.gain_nodes],
        }

    @classmethod
    def from_dict(cls, d):
        return cls(d["source_path"], d["src_start"], d["src_end"], d["track_offset"],
                   d.get("fade_in", 0.0), d.get("fade_out", 0.0),
                   d.get("fade_in_curve", 0.0), d.get("fade_out_curve", 0.0),
                   d.get("time_stretch_ratio", 1.0), d.get("pitch_semitones", 0.0),
                   d.get("gain_nodes"))

    def clone(self):
        return EditRegion(self.source_path, self.src_start, self.src_end, self.track_offset,
                          self.fade_in, self.fade_out, self.fade_in_curve, self.fade_out_curve,
                          self.time_stretch_ratio, self.pitch_semitones, self.gain_nodes)


@dataclass
class Workspace:
    name: str
    audio_files: List[Dict[str, Any]] = field(default_factory=list)
    current_folder: str = ""
    current_file_path: Optional[str] = None
    tree_item_paths: Dict[str, str] = field(default_factory=dict)
    dir_tree: Any = None
    file_table: Any = None
    left_panel_inner: Any = None
    center_panel_inner: Any = None
    project_file_path: Optional[str] = None  # 關聯的 .abproj 存檔路徑

class AudioBalancerApp(ctk.CTk, *([TkinterDnD.DnDWrapper] if _DND_AVAILABLE else [])):
    def __init__(self):
        super().__init__()
        if _DND_AVAILABLE:
            try:
                self.TkdndVersion = TkinterDnD._require(self)
            except Exception:
                # 這裡失敗代表拖放會整個悄悄失效（所有 drop_target_register 都會no-op）
                # 且完全沒有錯誤視窗提示使用者；印出來至少終端機還看得到線索。
                # 已知一個真實案例：tkinterdnd2 0.4.3 只內附 Tcl/Tk 8.6 版的 tkdnd 原生庫，
                # 在 Tk 9.0（較新的 Python 建置環境）載入會直接炸「找不到 tkdnd_Init」；
                # 0.6.2 起才補上 *-tcl9 的版本，修法是升級套件，不是改這裡的程式碼。
                print("[Audio Master] 警告：tkdnd 初始化失敗，拖放功能將無法使用：")
                traceback.print_exc()

        self.title(f"Audio Master {APP_VERSION} — LUFS Balancer + Converter")
        self.geometry("1280x800")
        self.minsize(1000, 650)
        self.configure(fg_color=COLOR_BG)

        # Workspace 狀態
        self.workspaces: List[Workspace] = []
        self.active_ws_idx: int = 0
        # 整個視窗（所有工作區）視為一個專案，對應一個 .abproj 檔
        self.project_file_path: Optional[str] = None

        # 共用狀態
        self.current_audio = None
        self.original_lufs_val = None

        # 音訊引擎狀態
        self.is_playing = False
        self.playback_thread = None
        self.pause_position = 0
        self._just_paused = False  # 空白鍵播放/暫停/重頭三段式節奏用：True＝上次是「暫停」而非「跳轉/停止」
        self.export_folder = ""

        # 自動存檔
        self._autosave_job = None

        # Undo stack：儲存 (action_type, [(path, old_target_lufs), ...])
        self._undo_stack: list = []
        # Guard 防止 slider ↔ entry 互相觸發
        self._updating_lufs = False
        # Edit Window：同一時間只開一個；Cmd+4 負責開／關切換，其他開啟入口可重新載入選取內容。
        self._edit_window = None

        self.setup_ui_styles()
        self.create_layout()
        # 視窗變寬（例如移到大螢幕、放大視窗）時，中央工作區字級/列高等比放大一點，避免顯得空洞。
        self.bind("<Configure>", self._schedule_ui_scale_update)
        self.after(300, self._apply_ui_scale)
        self._start_true_peak_overlay_loop()

    # ========== Workspace Property Routers ==========

    @property
    def audio_files(self):
        return self.workspaces[self.active_ws_idx].audio_files

    @audio_files.setter
    def audio_files(self, val):
        self.workspaces[self.active_ws_idx].audio_files = val

    @property
    def file_table(self):
        return self.workspaces[self.active_ws_idx].file_table

    @property
    def dir_tree(self):
        return self.workspaces[self.active_ws_idx].dir_tree

    @property
    def current_folder(self):
        return self.workspaces[self.active_ws_idx].current_folder

    @current_folder.setter
    def current_folder(self, val):
        self.workspaces[self.active_ws_idx].current_folder = val

    @property
    def current_file_path(self):
        return self.workspaces[self.active_ws_idx].current_file_path

    @current_file_path.setter
    def current_file_path(self, val):
        self.workspaces[self.active_ws_idx].current_file_path = val

    @property
    def tree_item_paths(self):
        return self.workspaces[self.active_ws_idx].tree_item_paths

    # ========== UI Styles ==========

    # 中央工作區字級／列高的基準值（對應預設視窗寬度 BASE_WINDOW_W）；大螢幕上視窗變寬時
    # 會依 _apply_ui_scale() 等比放大，避免文字在大螢幕上顯得空洞（不影響左側資料夾樹）。
    BASE_WINDOW_W = 1280
    BASE_FILE_FONT_SIZE = 13
    BASE_FILE_ROWHEIGHT = 38
    MAX_UI_SCALE = 1.3

    def setup_ui_styles(self):
        style = ttk.Style(self)
        style.theme_use("default")

        style.configure("Treeview",
                        background=COLOR_PANEL,
                        foreground="#D1D1D6",
                        rowheight=30,
                        fieldbackground=COLOR_PANEL,
                        borderwidth=0,
                        font=("Roboto", 13))
        style.map("Treeview", background=[("selected", COLOR_SELECTED)], foreground=[("selected", COLOR_CYAN)])

        # 中央工作區表格獨立樣式：列高／勾選欄字級都比左側資料夾樹大一號，方便點擊勾選（不影響左樹）。
        style.configure("FileTable.Treeview",
                        background=COLOR_PANEL,
                        foreground="#D1D1D6",
                        rowheight=self.BASE_FILE_ROWHEIGHT,
                        fieldbackground=COLOR_PANEL,
                        borderwidth=0,
                        font=("Roboto", self.BASE_FILE_FONT_SIZE))
        style.map("FileTable.Treeview", background=[("selected", COLOR_SELECTED)], foreground=[("selected", COLOR_CYAN)])
        style.configure("FileTable.Treeview.Heading",
                        background="#1C1C1E",
                        foreground=COLOR_TEXT_DIM,
                        font=("Roboto", 13, "bold"),
                        borderwidth=0)
        style.map("FileTable.Treeview.Heading", background=[("active", "#3A3A3C")])

        style.configure("Treeview.Heading",
                        background="#1C1C1E",
                        foreground=COLOR_TEXT_DIM,
                        font=("Roboto", 13, "bold"),
                        borderwidth=0)
        style.map("Treeview.Heading", background=[("active", "#3A3A3C")])

        # 捲軸：扁平深色、無箭頭（原生 ttk → 拖曳由 C 層處理，比 CTkScrollbar 的
        # Python/canvas 拖曳順很多；橫向卡頓的根因就是 CTkScrollbar 的拖曳處理）。
        for _o in ("Vertical", "Horizontal"):
            try:
                style.element_create(f"AM.{_o}.Scrollbar.trough", "from", "default")
                style.element_create(f"AM.{_o}.Scrollbar.thumb", "from", "default")
                style.layout(f"AM.{_o}.TScrollbar", [
                    (f"AM.{_o}.Scrollbar.trough", {"children": [
                        (f"AM.{_o}.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})
                    ], "sticky": "nswe"})
                ])
            except Exception:
                # 退回原生版面（會帶箭頭），但仍可拖曳、仍套用顏色
                pass
            # 隱晦版：細、低對比、軌道與面板同色（看不見軌道），只有滑塊淡淡一條；
            # hover 才稍微亮一點，方便找到。
            style.configure(f"AM.{_o}.TScrollbar",
                            troughcolor=COLOR_PANEL, background="#3A3A3F",
                            bordercolor=COLOR_PANEL, borderwidth=0, relief="flat",
                            arrowcolor=COLOR_PANEL, width=8)
            style.map(f"AM.{_o}.TScrollbar",
                      background=[("active", "#54545C"), ("pressed", "#54545C")],
                      troughcolor=[("active", COLOR_PANEL)])

        self._current_ui_scale = 1.0
        self._check_icon_px = None
        self._rebuild_check_icons(self.BASE_FILE_ROWHEIGHT)

    # ========== 勾選框圖示（取代 ✅/⬜ emoji）==========

    def _render_check_icon(self, size, checked):
        """畫一顆與主題色一致的勾選方框圖示（4x 超取樣後縮小，邊緣較平滑）。"""
        ss = 4
        hi = size * ss
        img = Image.new("RGBA", (hi, hi), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        pad = hi * 0.12
        radius = hi * 0.28
        box = [pad, pad, hi - pad, hi - pad]
        if checked:
            draw.rounded_rectangle(box, radius=radius, fill=(0, 229, 255, 255))
            lw = max(2, int(hi * 0.10))
            draw.line(
                [(hi * 0.30, hi * 0.53), (hi * 0.45, hi * 0.68), (hi * 0.72, hi * 0.34)],
                fill=(20, 20, 22, 255), width=lw, joint="curve",
            )
        else:
            draw.rounded_rectangle(
                box, radius=radius,
                outline=(142, 142, 147, 255), width=max(2, int(hi * 0.05)),
                fill=(58, 58, 61, 255),
            )
        img = img.resize((size, size), Image.LANCZOS)
        return ImageTk.PhotoImage(img)

    def _reapply_check_icon(self, table, iid):
        tags = table.item(iid, "tags") or ()
        icon = self._check_icon_on if _CHECK_TAG_ON in tags else self._check_icon_off
        table.item(iid, image=icon)

    def _rebuild_check_icons(self, rowheight):
        """依列高重新產生勾選框圖示，並套用到所有工作區已存在的列（縮放時即時生效）。"""
        box_px = max(16, round(rowheight * 0.52))
        if self._check_icon_px == box_px:
            return
        self._check_icon_px = box_px
        self._check_icon_on = self._render_check_icon(box_px, True)
        self._check_icon_off = self._render_check_icon(box_px, False)
        for ws in getattr(self, "workspaces", []):
            table = getattr(ws, "file_table", None)
            if not table:
                continue
            for top in table.get_children(""):
                self._reapply_check_icon(table, top)
                for child in table.get_children(top):
                    self._reapply_check_icon(table, child)

    # ========== 大螢幕字級自動縮放（中央工作區）==========

    def _schedule_ui_scale_update(self, event=None):
        """視窗尺寸變動時 debounce 250ms 再重算縮放比例，避免拖曳過程中頻繁重繪造成卡頓。"""
        if event is not None and event.widget is not self:
            return
        if getattr(self, "_ui_scale_job", None):
            try:
                self.after_cancel(self._ui_scale_job)
            except Exception:
                pass
        self._ui_scale_job = self.after(250, self._apply_ui_scale)

    def _apply_ui_scale(self):
        self._ui_scale_job = None
        try:
            w = self.winfo_width()
        except Exception:
            return
        if w <= 1:
            return
        scale = max(1.0, min(self.MAX_UI_SCALE, w / self.BASE_WINDOW_W))
        scale = round(scale, 2)
        if getattr(self, "_current_ui_scale", None) == scale:
            return
        self._current_ui_scale = scale

        font_size = max(self.BASE_FILE_FONT_SIZE, round(self.BASE_FILE_FONT_SIZE * scale))
        rowheight = max(self.BASE_FILE_ROWHEIGHT, round(self.BASE_FILE_ROWHEIGHT * scale))
        style = ttk.Style(self)
        style.configure("FileTable.Treeview", rowheight=rowheight, font=("Roboto", font_size))
        style.configure("FileTable.Treeview.Heading", font=("Roboto", font_size, "bold"))
        self._rebuild_check_icons(rowheight)

    # ========== Layout ==========

    def _create_menu_bar(self):
        """macOS 頂端選單列：保留系統預設的 App 選單（Quit 等）；新增 Window 選單，
        裡面放「Edit Windows」（開啟 Cmd+4 的多軌剪輯視窗）。

        ⚠️ 這裡「不可」把 window_menu 用 Tk 保留字 name="window" 建立——那樣會讓 macOS 把它
        當成系統原生的視窗選單自動合併（多出 Minimize/Zoom/Move to Display…等項目），而系統
        原生視窗選單會自己接管鍵盤快速鍵、把 Cmd+1/2/3/4 這類數字鍵保留給「切換到第 N 個視窗」
        用，導致我們自訂的 Cmd+4／Edit Windows 選單指令用滑鼠點選單有效、但按快速鍵完全沒反應
        （已實測重現）。改成普通命名的選單、只加自己要的項目，就不會被系統接管。"""
        menubar = tk.Menu(self)

        app_menu = tk.Menu(menubar, name="apple", tearoff=0)
        menubar.add_cascade(menu=app_menu)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="新增工作區", command=self._on_add_workspace, accelerator="Cmd+T")
        file_menu.add_separator()
        file_menu.add_command(label="開啟專案…", command=lambda: self._open_project(), accelerator="Cmd+O")
        file_menu.add_command(label="儲存專案", command=lambda: self._save_project(), accelerator="Cmd+S")
        file_menu.add_command(label="另存新專案…", command=lambda: self._save_project_as())
        menubar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=0, postcommand=self._update_edit_menu_state)
        edit_menu.add_command(label="返回上一步", command=self._menu_undo, accelerator="Cmd+Z")
        edit_menu.add_command(label="重做", command=self._menu_redo, accelerator="Cmd+Shift+Z")
        edit_menu.add_separator()
        edit_menu.add_command(label="剪下", command=self._menu_cut, accelerator="Cmd+X")
        edit_menu.add_command(label="複製", command=self._menu_copy, accelerator="Cmd+C")
        edit_menu.add_command(label="貼上", command=self._menu_paste, accelerator="Cmd+V")
        edit_menu.add_command(label="刪除", command=self._menu_delete, accelerator="Delete")
        menubar.add_cascade(label="Edit", menu=edit_menu)
        self._edit_menu = edit_menu

        window_menu = tk.Menu(menubar, tearoff=0)
        window_menu.add_command(label="Edit Windows", command=self._open_edit_window, accelerator="Cmd+4 / Cmd+E")
        menubar.add_cascade(label="Window", menu=window_menu)

        self.config(menu=menubar)
        self._menubar = menubar
        self.bind("<Command-t>", lambda e: self._on_add_workspace())
        # 從 Edit Window 切回主畫面（點回主視窗、Cmd+Tab 切回來都算）就把 Edit Window
        # 裡目前的編輯狀態寫回主畫面，不用等存檔或關掉 Edit Window 才看到最新結果。
        self.bind("<FocusIn>", self._on_main_window_focus_in)
        # 換掉系統選單後，App 選單的 Quit／Cmd+Q 要導回我們自己的關閉流程（關閉前問存檔）。
        try:
            self.createcommand("tk::mac::Quit", self._on_close)
        except Exception:
            pass

    def _edit_window_open(self):
        editor = getattr(self, "_edit_window", None)
        if editor is None:
            return False
        try:
            exists = bool(editor.win.winfo_exists())
        except Exception:
            exists = False
        if not exists and self._edit_window is editor:
            self._edit_window = None
        return exists

    def _sync_open_edit_window_entries(self):
        """把仍開著的 Edit Window 狀態寫回 entries；存檔、autosave 與匯出共用。"""
        if not self._edit_window_open():
            return
        try:
            self._edit_window.sync_entries()
        except Exception:
            traceback.print_exc()

    def _on_main_window_focus_in(self, event=None):
        """<FocusIn> 會連同視窗內每個取得焦點的子元件一路往上通知，這裡只在事件真的
        是主視窗本身（不是裡面某個 Entry/Treeview）拿到焦點時才處理，避免一直重複同步。"""
        if event is not None and event.widget is not self:
            return
        self._sync_open_edit_window_entries()

    def _schedule_edit_window_follow(self, file_sel):
        """去抖動：Shift／Cmd+A 連續多選時，只在選取穩定下來後同步一次 Edit Window，
        不然拖曳選取每過一列都重新 load_entries 會很卡（同一套手法見 _schedule_wave_draw）。"""
        if getattr(self, "_ew_follow_job", None):
            try:
                self.after_cancel(self._ew_follow_job)
            except Exception:
                pass
        self._ew_follow_job = self.after(150, lambda: self._sync_edit_window_selection(list(file_sel)))

    def _sync_edit_window_selection(self, file_sel):
        """讓已開啟的 Edit Window 音軌完全跟隨主畫面目前的選取（選什麼就編輯什麼，仿
        Logic Pro 點列表換上方 Editor 內容），不再只是捲動標示既有軌道。選取是空的
        （例如點到資料夾標頭列）或裡面的檔案都解不出音訊，就不動作，避免誤觸清空
        正在編輯的畫面。"""
        self._ew_follow_job = None
        if not self._edit_window_open() or not file_sel:
            return
        by_path = {it["path"]: it for it in self.audio_files}
        entries = [by_path[p] for p in file_sel if p in by_path]
        entries = [e for e in entries if self._ensure_entry_audio_decoded(e)]
        if not entries:
            return
        current_paths = [t["entry"]["path"] for t in self._edit_window.tracks]
        requested_paths = [e["path"] for e in entries]
        if current_paths == requested_paths:
            return
        self._edit_window.sync_entries()
        self._edit_window.load_entries(entries)

    def _update_edit_menu_state(self):
        """Edit 選單開啟前呼叫：剪下/複製/貼上/刪除/重做只有在 Edit Window 開著才有意義，
        沒開就灰掉，避免點了沒反應搞不清楚狀況。返回上一步永遠可用（沒開 Edit Window 時
        退回主畫面自己的 LUFS/Gain undo）。"""
        state = "normal" if self._edit_window_open() else "disabled"
        for label in ("重做", "剪下", "複製", "貼上", "刪除"):
            try:
                self._edit_menu.entryconfigure(label, state=state)
            except Exception:
                pass

    def _menu_undo(self):
        if self._edit_window_open():
            self._edit_window.cmd_undo()
        else:
            self._undo()

    def _menu_redo(self):
        if self._edit_window_open():
            self._edit_window.cmd_redo()

    def _menu_cut(self):
        if self._edit_window_open():
            self._edit_window.cmd_cut()

    def _menu_copy(self):
        if self._edit_window_open():
            self._edit_window.cmd_copy()

    def _menu_paste(self):
        if self._edit_window_open():
            self._edit_window.cmd_paste()

    def _menu_delete(self):
        if self._edit_window_open():
            self._edit_window.cmd_delete()

    def create_layout(self):
        self._create_menu_bar()

        # row 0: Top Bar, row 1: Tab Bar, row 2: Main Content (weight=1), row 3: Border, row 4: Bottom Bar
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # ==================== 頂部標題與匯入列 ====================
        self.top_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.top_bar.grid(row=0, column=0, padx=20, pady=(15, 10), sticky="ew")
        self.top_bar.columnconfigure(1, weight=1)

        self.top_title = ctk.CTkLabel(self.top_bar, text="Audio Loudness Balancer Assistant", font=("Roboto", 18, "bold"), text_color="#D1D1D6")
        self.top_title.grid(row=0, column=0, sticky="w")

        self.top_main_title = ctk.CTkLabel(self.top_bar, text="批量音量平衡工具", font=("Roboto", 24, "bold"), text_color="white")
        self.top_main_title.grid(row=0, column=1, sticky="n")

        # 匯入按鈕：分成「Import File（選單一/多個音檔）」與「Import Folder（選整包資料夾）」
        self.import_frame = ctk.CTkFrame(self.top_bar, fg_color="transparent")
        self.import_frame.grid(row=0, column=2, padx=5)
        self.import_file_btn = ctk.CTkButton(self.import_frame, text="Import File", width=104,
                                             fg_color="#3A3A3C", hover_color="#4A4A4C",
                                             command=self._do_import_files)
        self.import_file_btn.pack(side="left", padx=(0, 6))
        self.import_folder_btn = ctk.CTkButton(self.import_frame, text="Import Folder", width=116,
                                               fg_color="#3A3A3C", hover_color="#4A4A4C",
                                               command=self._do_import_folder)
        self.import_folder_btn.pack(side="left")

        # ==================== 工作區 Tab Bar (row=1) ====================
        self.tab_bar = ctk.CTkFrame(self, fg_color="#111113", height=38, corner_radius=0)
        self.tab_bar.grid(row=1, column=0, sticky="ew")
        self.tab_bar.grid_propagate(False)

        self.tab_btn_frame = ctk.CTkFrame(self.tab_bar, fg_color="transparent")
        self.tab_btn_frame.pack(side="left", fill="y", padx=(15, 0))

        self.btn_add_ws = ctk.CTkButton(
            self.tab_bar, text="+", width=32, height=28,
            fg_color="#2C2C2E", hover_color="#3A3A3C",
            font=("Roboto", 16, "bold"), text_color=COLOR_CYAN,
            command=self._on_add_workspace
        )
        self.btn_add_ws.pack(side="left", padx=(0, 4), pady=5)

        self.btn_open_project = ctk.CTkButton(
            self.tab_bar, text="📂  開啟專案", width=96, height=28,
            fg_color="#2C2C2E", hover_color="#3A3A3C",
            font=("Roboto", 12), text_color="#D1D1D6",
            command=lambda: self._open_project()
        )
        self.btn_open_project.pack(side="left", padx=(8, 4), pady=5)

        self.btn_save_project = ctk.CTkButton(
            self.tab_bar, text="💾  儲存專案", width=96, height=28,
            fg_color="#2C2C2E", hover_color="#3A3A3C",
            font=("Roboto", 12), text_color="#D1D1D6",
            command=lambda: self._save_project()
        )
        self.btn_save_project.pack(side="left", padx=(0, 4), pady=5)

        self.btn_undo = ctk.CTkButton(
            self.tab_bar, text="↩︎  返回上一步", width=110, height=28,
            fg_color="#2C2C2E", hover_color="#3A3A3C",
            font=("Roboto", 12), text_color="#D1D1D6",
            command=lambda: self._undo()
        )
        self.btn_undo.pack(side="left", padx=(0, 4), pady=5)

        self.btn_edit_window = ctk.CTkButton(
            self.tab_bar, text="✂  Edit Window", width=110, height=28,
            fg_color="#2C2C2E", hover_color="#3A3A3C",
            font=("Roboto", 12), text_color="#D1D1D6",
            command=lambda: self._open_edit_window()
        )
        self.btn_edit_window.pack(side="left", padx=(0, 4), pady=5)

        # ==================== 中央三大區塊 (row=2) ====================
        self.main_content = ctk.CTkFrame(self, fg_color="transparent")
        self.main_content.grid(row=2, column=0, padx=15, pady=(0, 15), sticky="nsew")
        self.main_content.rowconfigure(0, weight=1)
        self.main_content.columnconfigure(0, weight=1)

        # ── 可拖移三欄 PanedWindow ─────────────────────────────────
        self._main_paned = tk.PanedWindow(
            self.main_content, orient=tk.HORIZONTAL,
            sashwidth=6, sashrelief="flat", sashcursor="sb_h_double_arrow",
            bg=COLOR_BG, bd=0, opaqueresize=True
        )
        self._main_paned.grid(row=0, column=0, sticky="nsew")

        # --- 第一區：資料夾結構 (Left) ---
        # 可收合：見 _toggle_left_panel / _snap_collapse_on_sash_release —— 按鈕一鍵收合，
        # 或把中間工作區的分隔線（sash）往左拖到底也會自動收合到只剩一條細條。
        self._left_collapsed = False
        self._left_panel_width = 220
        self.left_panel = ctk.CTkFrame(self._main_paned, fg_color=COLOR_PANEL, corner_radius=8)
        self._main_paned.add(self.left_panel, minsize=28, width=220, stretch="never")
        self.left_panel.rowconfigure(1, weight=1)
        self.left_panel.columnconfigure(0, weight=0)
        self.left_panel.columnconfigure(1, weight=1)

        self.btn_left_collapse = ctk.CTkButton(
            self.left_panel, text="‹", width=22, height=22, font=("Arial", 13, "bold"),
            fg_color="transparent", hover_color="#3A3A3C", text_color=COLOR_TEXT_DIM,
            command=self._toggle_left_panel
        )
        self.btn_left_collapse.grid(row=0, column=0, padx=(6, 0), pady=10, sticky="w")

        self.lbl_left_panel_title = ctk.CTkLabel(self.left_panel, text="資料夾結構", font=("Roboto", 14, "bold"), text_color="white")
        self.lbl_left_panel_title.grid(row=0, column=1, padx=(4, 10), pady=10, sticky="w")

        # Container 用於放置各工作區的 dir_tree
        self.left_content_container = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        self.left_content_container.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.left_content_container.rowconfigure(0, weight=1)
        self.left_content_container.columnconfigure(0, weight=1)
        self._main_paned.bind("<ButtonRelease-1>", self._snap_collapse_on_sash_release, add="+")

        # --- 第二區：多欄位檔案清單 (Center) ---
        self.center_panel = ctk.CTkFrame(self._main_paned, fg_color=COLOR_PANEL, corner_radius=8)
        self._main_paned.add(self.center_panel, minsize=200, stretch="always")
        self.center_panel.rowconfigure(0, weight=1)
        self.center_panel.columnconfigure(0, weight=1)

        # Container 用於放置各工作區的 file_table
        self.center_content_container = ctk.CTkFrame(self.center_panel, fg_color="transparent")
        self.center_content_container.grid(row=0, column=0, sticky="nsew")
        self.center_content_container.rowconfigure(0, weight=1)
        self.center_content_container.columnconfigure(0, weight=1)

        # --- 第三區：DAW 波形與電平表 (Right) ---
        self.right_panel = ctk.CTkFrame(self._main_paned, fg_color=COLOR_PANEL, corner_radius=8)
        self._main_paned.add(self.right_panel, minsize=280, width=400, stretch="never")
        self.right_panel.columnconfigure(0, weight=1)
        # 參數＋音量表那一列吃滿剩餘高度 → 視窗太矮時可捲動框會出現捲軸（初始單選版面用）
        self.right_panel.rowconfigure(3, weight=1)

        self.lbl_active_file = ctk.CTkLabel(self.right_panel, text="No File Selected", font=("Roboto", 14, "bold"), text_color="white")
        self.lbl_active_file.grid(row=0, column=0, padx=15, pady=(15, 5), sticky="w")

        self.waveform_canvas = tk.Canvas(self.right_panel, bg="#111113", highlightthickness=0, height=100, cursor="hand2")
        self.waveform_canvas.grid(row=1, column=0, padx=15, pady=(5, 5), sticky="ew")
        self.waveform_canvas.bind("<ButtonPress-1>", self.on_waveform_click)
        self.waveform_canvas.bind("<B1-Motion>", self.on_waveform_drag)
        self.waveform_canvas.bind("<ButtonRelease-1>", self.on_waveform_release)
        # 多選軌數太多、畫面塞不下時，滑鼠滾輪往下捲動查看其餘軌道
        self.waveform_canvas.configure(yscrollincrement=14)
        self.waveform_canvas.bind("<MouseWheel>", self._on_waveform_scroll)
        self.waveform_canvas.bind("<Button-4>", self._on_waveform_scroll)
        self.waveform_canvas.bind("<Button-5>", self._on_waveform_scroll)
        # 版面/視窗變動時，依目前尺寸重畫波形
        self.waveform_canvas.bind("<Configure>", self._on_waveform_configure)

        self.player_frame = ctk.CTkFrame(self.right_panel, fg_color="transparent")
        self.player_frame.grid(row=2, column=0, padx=15, pady=5, sticky="we")
        self.player_frame.columnconfigure(1, weight=1)

        self.lbl_time = ctk.CTkLabel(self.player_frame, text="00:00 / 00:00", font=("Roboto", 12), text_color=COLOR_TEXT_DIM)
        self.lbl_time.grid(row=0, column=0, sticky="w")

        self.scrub_var = ctk.DoubleVar(value=0)
        self.scrub_slider = ctk.CTkSlider(self.player_frame, from_=0, to=100, variable=self.scrub_var,
                                          height=12, progress_color=COLOR_CYAN, command=self.on_scrub)
        self.scrub_slider.grid(row=0, column=1, padx=10, sticky="ew")

        self.transport_controls = ctk.CTkFrame(self.player_frame, fg_color="transparent")
        self.transport_controls.grid(row=1, column=0, columnspan=2, pady=5)

        btn_args = {"width": 35, "height": 30, "font": ("Arial", 16), "fg_color": "#3A3A3C", "hover_color": "#4A4A4C"}
        self.btn_rewind = ctk.CTkButton(self.transport_controls, text="⏮", command=self.seek_backward, **btn_args)
        self.btn_rewind.pack(side="left", padx=2)

        self.play_btn = ctk.CTkButton(self.transport_controls, text="▶", command=self.play_original, **btn_args)
        self.play_btn.pack(side="left", padx=2)

        self.stop_btn = ctk.CTkButton(self.transport_controls, text="⏹", command=self.stop_playback, **btn_args)
        self.stop_btn.pack(side="left", padx=2)

        self.btn_forward = ctk.CTkButton(self.transport_controls, text="⏭", command=self.seek_forward, **btn_args)
        self.btn_forward.pack(side="left", padx=2)

        self.loop_var = ctk.BooleanVar(value=False)
        # 用純符號「↺」而非彩色 emoji「🔁」：emoji 固定走 Apple Color Emoji 字型渲染，
        # 就算跟其他鍵共用一樣的 font/尺寸/hover_color 設定，畫出來還是明顯比較大、比較花俏、
        # 風格對不上 ⏮▶⏹⏭ 這幾個純符號鍵——這是純文字符號 vs 彩色 emoji 的先天渲染差異，不是沒套用設定。
        self.btn_loop = ctk.CTkButton(self.transport_controls, text="↺", command=self.toggle_loop, **btn_args)
        self.btn_loop.pack(side="left", padx=2)

        self.ab_listen_var = ctk.BooleanVar(value=False)
        # A/B 開關移到傳輸鍵下方獨立一列：原始 在旋鈕左側、目標 在右側
        self.ab_frame = ctk.CTkFrame(self.player_frame, fg_color="transparent")
        self.ab_frame.grid(row=2, column=0, columnspan=2, pady=(2, 4))
        ctk.CTkLabel(self.ab_frame, text="原始", font=("Roboto", 12),
                     text_color="#D1D1D6").pack(side="left", padx=(0, 6))
        self.ab_listen_switch = ctk.CTkSwitch(self.ab_frame, text="目標",
                                              variable=self.ab_listen_var, progress_color=COLOR_RED,
                                              command=self.on_ab_toggle)
        self.ab_listen_switch.pack(side="left")

        # 參數＋音量表用『可捲動框』：視窗太矮時，右側會出現可拖曳的捲軸，
        # 讓最底部的音量表/裝置/輸出格式不會被切掉看不到。
        # 注意：這裡必須用「純 CTkFrame」，不可用 CTkScrollableFrame。
        # 多選時本區會被重排成右欄（雙欄版面），而 CTkScrollableFrame 一旦在 realize 後
        # 被重排/改尺寸，CTk 內部 canvas↔scrollbar 的 <Configure> 會無限遞迴 → 100% CPU 卡死
        # （已用 sample 確認、且實測純 Frame 不會卡）。代價：小視窗時參數區不會自動出現捲軸。
        self.lufs_wrapper = ctk.CTkFrame(self.right_panel, fg_color="transparent",
                                         border_width=1, border_color="#3A3A3C", corner_radius=8)
        self.lufs_wrapper.grid(row=3, column=0, padx=15, pady=5, sticky="nsew")
        self.lufs_wrapper.columnconfigure(0, weight=1)

        self.target_lufs_var = ctk.DoubleVar(value=-16.0)
        # LUFS Fader 移到第二段（與批次 ±Gain 對調位置）
        self.lufs_slider = ctk.CTkSlider(self.lufs_wrapper, from_=-30.0, to=-6.0, variable=self.target_lufs_var,
                                         button_color=COLOR_CYAN, progress_color=COLOR_CYAN, command=self._on_lufs_slider)
        self.lufs_slider.grid(row=2, column=0, columnspan=2, padx=20, pady=(10, 0), sticky="ew")

        self.t_lufs_frame = ctk.CTkFrame(self.lufs_wrapper, fg_color="transparent")
        self.t_lufs_frame.grid(row=3, column=0, columnspan=2, pady=(2, 4))
        # 直接輸入目標 LUFS
        ctk.CTkLabel(self.t_lufs_frame, text="Target", font=("Arial", 12), text_color=COLOR_TEXT_DIM).pack(side="left", padx=(0, 4))
        self.lufs_entry_var = tk.StringVar(value="-16.0")
        self.lufs_entry = ctk.CTkEntry(
            self.t_lufs_frame, textvariable=self.lufs_entry_var,
            width=88, height=32, font=("Roboto", 16, "bold"),
            text_color=COLOR_CYAN, fg_color="#1A1A1D",
            border_color="#3A3A3C", justify="center"
        )
        self.lufs_entry.pack(side="left")
        self.lufs_entry.bind("<Return>",   self._on_lufs_entry_return)
        self.lufs_entry.bind("<KP_Enter>", self._on_lufs_entry_return)
        self.lufs_entry.bind("<FocusOut>", self._on_lufs_entry_commit)
        # 滑鼠滾輪在數值上、上下滑動即可微調（每格 0.1）
        self.lufs_entry.bind("<MouseWheel>", self._on_lufs_scroll)
        self.lufs_entry.bind("<Button-4>", self._on_lufs_scroll)   # 部分系統的滾輪上
        self.lufs_entry.bind("<Button-5>", self._on_lufs_scroll)   # 部分系統的滾輪下
        # 直接按住數值往上/下拖曳＝加/減（仿專業音訊軟體數值欄位手感），跟滾輪微調並存。
        _bind_drag_scrub(self.lufs_entry, self._lufs_entry_drag_delta)
        ctk.CTkLabel(self.t_lufs_frame, text="LUFS", font=("Arial", 12), text_color=COLOR_TEXT_DIM).pack(side="left", padx=(4, 0))
        self.lbl_suggest_lufs = ctk.CTkLabel(self.t_lufs_frame, text="", font=("Arial", 10), text_color="#888888")
        self.lbl_suggest_lufs.pack(side="left", padx=(8, 0))

        # 批次 ±Gain Fader（row=0/1，置於最上方，與 LUFS Fader 對調位置）；上下限 ±20 dB
        # 滑桿/框格顯示「目前已套用的總增益」(目標 LUFS − 原始 LUFS)，不是每次選取都歸零，
        # 這樣調過的批次 dB 換選別的音檔再點回來時記錄還在（見 _refresh_gain_display）。
        # _gain_display_at_rest：目前顯示值當作『歇息基準』，供拖曳時計算相對位移用，避免
        # 從非 0 的既有總增益繼續拖曳時把既有增益重複疊加進去（見 _capture_gain_baseline）。
        self._gain_display_at_rest = 0.0
        self._gain_display_uniform = True
        self.gain_adj_var = ctk.DoubleVar(value=0.0)
        self.gain_slider = ctk.CTkSlider(self.lufs_wrapper, from_=-20.0, to=20.0, variable=self.gain_adj_var,
                                         button_color=COLOR_CYAN, progress_color=COLOR_CYAN, command=self._on_gain_slider)
        self.gain_slider.grid(row=0, column=0, columnspan=2, padx=20, pady=(15, 0), sticky="ew")

        self.gain_adj_frame = ctk.CTkFrame(self.lufs_wrapper, fg_color="transparent")
        self.gain_adj_frame.grid(row=1, column=0, columnspan=2, pady=(2, 4))
        ctk.CTkLabel(self.gain_adj_frame, text="Gain", font=("Arial", 12), text_color=COLOR_TEXT_DIM).pack(side="left", padx=(0, 4))
        self.gain_entry_var = tk.StringVar(value="0.0")
        self.gain_adj_entry = ctk.CTkEntry(
            self.gain_adj_frame, textvariable=self.gain_entry_var,
            width=72, height=32, font=("Roboto", 16, "bold"),
            text_color=COLOR_CYAN, fg_color="#1A1A1D",
            border_color="#3A3A3C", justify="center"
        )
        self.gain_adj_entry.pack(side="left")
        self.gain_adj_entry.bind("<Return>",   self._on_gain_entry_return)
        self.gain_adj_entry.bind("<KP_Enter>", self._on_gain_entry_return)
        self.gain_adj_entry.bind("<FocusOut>", self._on_gain_entry_commit)
        # 滑鼠滾輪在數值上、上下滑動即可微調（每格 0.1）
        self.gain_adj_entry.bind("<MouseWheel>", self._on_gain_scroll)
        self.gain_adj_entry.bind("<Button-4>", self._on_gain_scroll)
        self.gain_adj_entry.bind("<Button-5>", self._on_gain_scroll)
        _bind_drag_scrub(self.gain_adj_entry, self._gain_entry_drag_delta)
        ctk.CTkLabel(self.gain_adj_frame, text="dB", font=("Arial", 12), text_color=COLOR_TEXT_DIM).pack(side="left", padx=(4, 0))

        # 音量 bar 移到最下方（row=5）
        self.meter_frame = ctk.CTkFrame(self.lufs_wrapper, fg_color="transparent")
        self.meter_frame.grid(row=5, column=0, padx=20, pady=(8, 14), sticky="ew")

        self.level_prog_L = tk.Canvas(self.meter_frame, width=28, height=150, bg="#0A0A0A", highlightthickness=0)
        self.level_prog_L.pack(side="left", padx=(0, 5))

        self.level_prog_R = tk.Canvas(self.meter_frame, width=28, height=150, bg="#0A0A0A", highlightthickness=0)
        self.level_prog_R.pack(side="left", padx=5)

        self.scale_canvas = tk.Canvas(self.meter_frame, width=40, height=150, bg="#1C1C1E", highlightthickness=0)
        self.scale_canvas.pack(side="left", padx=(5, 0))

        scales = [0, -6, -12, -18, -24, -30]
        canvas_height = 150
        m = 8  # 與音量條刻度線相同的上下內縮，使標籤置中且與刻度線精準對齊
        for v in scales:
            y = int(round(m + (abs(v) / 30.0) * (canvas_height - 2 * m)))
            self.scale_canvas.create_text(5, y, text=str(v), anchor="w", fill="#AAAAAA", font=("Arial", 10))

        self.peak_frame = ctk.CTkFrame(self.meter_frame, fg_color="transparent")
        self.peak_frame.pack(side="left", padx=(10, 0), fill="y")

        ctk.CTkLabel(self.peak_frame, text="PEAK", font=("Arial", 9, "bold"), text_color="#555555").pack(pady=(5, 10))
        self.lbl_peak_L = ctk.CTkLabel(self.peak_frame, text="--", font=("Courier", 11, "bold"), text_color=COLOR_CYAN)
        self.lbl_peak_L.pack(pady=2)
        self.lbl_peak_R = ctk.CTkLabel(self.peak_frame, text="--", font=("Courier", 11, "bold"), text_color=COLOR_CYAN)
        self.lbl_peak_R.pack(pady=2)

        self.btn_peak_rst = ctk.CTkButton(self.peak_frame, text="RST", width=30, height=20, font=("Arial", 9), fg_color="#3A3A3C", command=self.reset_peaks)
        self.btn_peak_rst.pack(side="bottom", pady=5)

        self.max_peak_L = -100.0
        self.max_peak_R = -100.0

        # 輸出裝置放在音量表右側；由 _apply_meter_layout() 佈置。
        self.device_frame = ctk.CTkFrame(self.lufs_wrapper, fg_color="transparent")
        self.device_frame.grid(row=5, column=1, sticky="nw", padx=(8, 0), pady=(8, 14))

        try:
            _seen: set = set()
            out_devices = []
            for _d in sd.query_devices():
                if _d['max_output_channels'] > 0 and _d['name'] not in _seen:
                    _seen.add(_d['name'])
                    out_devices.append(_d['name'])
            default_out = sd.query_devices(kind='output')['name'] if out_devices else "System Default"
        except Exception:
            out_devices = []
            default_out = "System Default"

        if default_out not in out_devices:
            out_devices.insert(0, default_out)
        if "System Default" not in out_devices:
            out_devices.insert(0, "System Default")

        self.device_menu = ctk.CTkOptionMenu(self.device_frame, values=out_devices, fg_color="#3A3A3C", height=26, width=150, font=("Arial", 11), anchor="center")
        self.device_menu.set(default_out)
        self.device_menu.pack(side="top", anchor="nw", pady=(2, 0))
        self._apply_meter_layout()

        self.info_frame = ctk.CTkFrame(self.lufs_wrapper, fg_color="transparent")
        self.info_frame.grid(row=4, column=0, columnspan=2, padx=20, pady=(5, 10), sticky="ew")
        self.info_frame.columnconfigure((0,1,2), weight=1)

        self.card_current = ctk.CTkFrame(self.info_frame, fg_color="#1C1C1E", corner_radius=6)
        self.card_current.grid(row=0, column=0, padx=2, sticky="ew")
        ctk.CTkLabel(self.card_current, text="Current", font=("Arial", 10), text_color="#8E8E93").pack(pady=(5,0))
        self.lbl_info_current = ctk.CTkLabel(self.card_current, text="--", font=("Roboto", 14, "bold"), text_color=COLOR_CYAN)
        self.lbl_info_current.pack(pady=(0,5))

        self.card_target = ctk.CTkFrame(self.info_frame, fg_color="#1C1C1E", corner_radius=6)
        self.card_target.grid(row=0, column=1, padx=2, sticky="ew")
        ctk.CTkLabel(self.card_target, text="Target", font=("Arial", 10), text_color="#8E8E93").pack(pady=(5,0))
        self.lbl_info_target = ctk.CTkLabel(self.card_target, text="--", font=("Roboto", 14, "bold"), text_color=COLOR_CYAN)
        self.lbl_info_target.pack(pady=(0,5))

        self.card_gain = ctk.CTkFrame(self.info_frame, fg_color="#1C1C1E", corner_radius=6)
        self.card_gain.grid(row=0, column=2, padx=2, sticky="ew")
        ctk.CTkLabel(self.card_gain, text="Gain", font=("Arial", 10), text_color="#8E8E93").pack(pady=(5,0))
        self.lbl_info_gain = ctk.CTkLabel(self.card_gain, text="--", font=("Roboto", 14, "bold"), text_color=COLOR_CYAN)
        self.lbl_info_gain.pack(pady=(0,5))

        # ==================== 底部全域設定與匯出 ====================
        self.bottom_border = ctk.CTkFrame(self, fg_color="#3A3A3C", height=1, corner_radius=0)
        self.bottom_border.grid(row=3, column=0, sticky="ew")

        self.bottom_bar = ctk.CTkFrame(self, fg_color="#111113", corner_radius=0, height=60)
        self.bottom_bar.grid(row=4, column=0, sticky="ew")
        self.bottom_bar.grid_propagate(False)

        # 底部三區，平均分配：左＝輸出格式群組、中＝選擇輸出路徑+完整路徑、右＝自訂名稱+匯出。
        # （輸出格式在輸出路徑的左邊；中間路徑吃滿剩餘寬度 → 完整路徑不被吃掉。）
        self.bottom_bar.columnconfigure(0, weight=0)   # 左：輸出格式群組
        self.bottom_bar.columnconfigure(1, weight=1)   # 中：輸出路徑（吃滿剩餘寬度）
        self.bottom_bar.columnconfigure(2, weight=0)   # 右：自訂名稱 + 匯出
        self.bottom_bar.rowconfigure(0, weight=1)

        # ── 左：輸出格式 / 取樣率 / 位元率 / 靜音移除 ──
        self.settings_group = ctk.CTkFrame(self.bottom_bar, fg_color="transparent")
        self.settings_group.grid(row=0, column=0, sticky="w", padx=(16, 8), pady=0)
        ctk.CTkLabel(self.settings_group, text="公版:", font=("Arial", 11), text_color="#8E8E93").pack(side="left", padx=(0, 4))
        self.preset_menu = ctk.CTkOptionMenu(self.settings_group, values=PRESET_OPTIONS, fg_color="#3A3A3C", height=26, width=100, font=("Arial", 11), anchor="center", command=self._on_preset_changed)
        self.preset_menu.set(PRESET_PLACEHOLDER)
        self.preset_menu.pack(side="left", padx=(0, 10))
        ctk.CTkFrame(self.settings_group, width=1, height=20, fg_color="#3A3A3C").pack(side="left", padx=(0, 10))
        ctk.CTkLabel(self.settings_group, text="輸出格式:", font=("Arial", 11), text_color="#8E8E93").pack(side="left", padx=(0, 4))
        self.format_menu = ctk.CTkOptionMenu(self.settings_group, values=OUTPUT_FORMATS, fg_color="#3A3A3C", height=26, width=84, font=("Arial", 11), anchor="center", command=self._on_format_changed)
        self.format_menu.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(self.settings_group, text="取樣率:", font=("Arial", 11), text_color="#8E8E93").pack(side="left", padx=(0, 4))
        self.sr_menu = ctk.CTkOptionMenu(self.settings_group, values=SAMPLE_RATES, fg_color="#3A3A3C", height=26, width=84, font=("Arial", 11), anchor="center")
        self.sr_menu.set("48000")
        self.sr_menu.pack(side="left", padx=(0, 10))
        self.lbl_bit_menu = ctk.CTkLabel(self.settings_group, text="位元率:", font=("Arial", 11), text_color="#8E8E93")
        self.lbl_bit_menu.pack(side="left", padx=(0, 4))
        self.bit_menu = ctk.CTkOptionMenu(self.settings_group, values=BITRATES, fg_color="#3A3A3C", height=26, width=78, font=("Arial", 11), anchor="center")
        self.bit_menu.set("Original")
        self.bit_menu.pack(side="left", padx=(0, 10))

        # ── 中：選擇輸出路徑 + 完整路徑名稱（在輸出格式右邊；吃滿中間 → 完整路徑不被吃掉）──
        self.path_group = ctk.CTkFrame(self.bottom_bar, fg_color="transparent")
        self.path_group.grid(row=0, column=1, sticky="ew", padx=(8, 8), pady=0)
        self.path_group.columnconfigure(1, weight=1)
        self.btn_export_path = ctk.CTkButton(self.path_group, text="📁 選擇輸出路徑", width=120, height=32,
                                             fg_color="#3A3A3C", hover_color="#4A4A4C", font=("Arial", 12),
                                             command=self.select_export_folder)
        self.btn_export_path.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.lbl_export_path = ctk.CTkLabel(self.path_group, text="輸出:/尚未設定", text_color="#8E8E93",
                                            font=("Roboto Mono", 11), anchor="w", justify="left")
        self.lbl_export_path.grid(row=0, column=1, sticky="ew")
        # 點路徑直接在 Finder 開啟輸出資料夾（匯出完成後不用自己去翻路徑）
        self.lbl_export_path.bind("<Button-1>", lambda e: self._open_export_folder())

        # ── 右：自訂資料夾名稱 + 匯出 ──
        self.export_group = ctk.CTkFrame(self.bottom_bar, fg_color="transparent")
        self.export_group.grid(row=0, column=2, sticky="e", padx=(8, 16), pady=0)
        ctk.CTkLabel(self.export_group, text="自訂資料夾名稱:", text_color="#8E8E93", font=("Arial", 11)).pack(side="left", padx=(0, 5))
        default_folder_name = datetime.now().strftime("%Y%m%d_")
        self.folder_name_entry = ctk.CTkEntry(self.export_group, width=110, height=32, font=("Arial", 12), fg_color="#1C1C1E", border_width=1, border_color="#3A3A3C")
        self.folder_name_entry.insert(0, default_folder_name)
        self.folder_name_entry.pack(side="left", padx=(0, 12))
        self.btn_export = ctk.CTkButton(self.export_group, text="↗ 匯出音檔",
                                        font=("Roboto", 13, "bold"), width=110, height=36,
                                        fg_color="#00E5FF", text_color="black", hover_color="#00C8E0",
                                        command=self.start_export_thread)
        self.btn_export.pack(side="left", padx=0)

        # ---------------- 鍵盤快捷鍵 ----------------
        # 注意：customtkinter 的 CTkEntry 內層其實是 tkinter.Entry，focus_get() 會回傳內層
        # 的 tk.Entry 而非 CTkEntry，所以判斷「焦點是否在輸入框」必須兩者都檢查
        # （見 _focus_in_text_entry）。否則在右側參數欄打字時，Delete/Backspace 等全域
        # 快捷鍵會誤觸而把中間工作區選取的音檔刪掉。
        for seq, action in [
            ("<space>", "space"), ("<Left>", "left"), ("<Right>", "right"),
            ("<Up>", "up"), ("<Down>", "down"),
        ]:
            self.bind(seq, lambda e, a=action: self._handle_main_navigation_shortcut(a))
        # 跟下面 Cmd+A/Cmd+E 同一招保險：用 bind_all 補一層全域保險，但要先確認目前鍵盤焦點真的
        # 在主視窗（不是 Edit Window），否則 Edit Window 開著時主視窗這幾個鍵會誤觸。
        for seq, action in [
            ("<space>", "space"), ("<Left>", "left"), ("<Right>", "right"),
            ("<Up>", "up"), ("<Down>", "down"),
        ]:
            self.bind_all(
                seq,
                lambda e, a=action: self._handle_main_navigation_shortcut(a)
                if self._is_frontmost() else None,
                add="+",
            )
        # Delete/BackSpace 只在焦點確實落在檔案表/資料夾樹（或無特定焦點）時才刪檔，
        # 避免焦點在按鈕/選單/滑桿時誤刪當前選取的音檔。
        self.bind("<Delete>", lambda e: self.remove_selected_files() if self._delete_allowed() else None)
        self.bind("<BackSpace>", lambda e: self.remove_selected_files() if self._delete_allowed() else None)
        # 全選
        self.bind("<Command-a>", self._handle_select_all_shortcut)
        self.bind("<Command-A>", self._handle_select_all_shortcut)
        self.bind("<Control-a>", self._handle_select_all_shortcut)
        self.bind("<Control-A>", self._handle_select_all_shortcut)
        # macOS/Tk 有時會把 Cmd+A 轉成虛擬事件送給焦點 widget；用 bind_all 補上全域保險
        # （一樣要先確認焦點在主視窗，否則 Edit Window 開著時會被這裡誤攔截）。
        for seq in ("<Command-a>", "<Command-A>", "<Control-a>", "<Control-A>", "<<SelectAll>>"):
            self.bind_all(seq, lambda e: self._handle_select_all_shortcut(e) if self._is_frontmost() else None, add="+")
        # Undo
        self.bind("<Command-z>", lambda e: None if self._focus_in_text_entry() else self._undo())
        self.bind("<Control-z>", lambda e: None if self._focus_in_text_entry() else self._undo())
        # 儲存 / 開啟整個專案
        self.bind("<Command-s>", lambda e: self._save_project())
        self.bind("<Control-s>",  lambda e: self._save_project())
        self.bind("<Command-o>", lambda e: self._open_project())
        self.bind("<Control-o>",  lambda e: self._open_project())
        # Edit Window：Tk 9 會把省略事件類型的「<Command-4>」解析成
        # <Mod1-Button-4>（Cmd＋滑鼠按鈕 4），而不是鍵盤數字 4；數字 detail 因此必須明確
        # 寫 KeyPress。KP_4 另外列出，讓主鍵盤與數字鍵盤都能使用。
        # Cmd+4 是 Edit Window 開／關切換；Cmd+E 保留為主視窗的開啟備用鍵。
        # bind_all 則處理焦點落在內層 widget 時的 macOS/Tk 差異。
        for seq in (
            "<Command-KeyPress-4>", "<Control-KeyPress-4>",
            "<Command-KeyPress-KP_4>", "<Control-KeyPress-KP_4>",
        ):
            self.bind(seq, self._handle_edit_window_shortcut)
            self.bind_all(
                seq,
                lambda e: self._handle_edit_window_shortcut(e)
                if self._edit_window_shortcut_active() else None,
                add="+",
            )
        for seq in (
            "<Command-KeyPress-e>", "<Command-KeyPress-E>",
            "<Control-KeyPress-e>", "<Control-KeyPress-E>",
        ):
            self.bind(seq, self._handle_edit_window_open_shortcut)
            self.bind_all(
                seq,
                lambda e: self._handle_edit_window_open_shortcut(e) if self._is_frontmost() else None,
                add="+",
            )
        # 部分 Tk 9/macOS 鍵盤配置會讓 Command＋數字的 keysym 變成 "??"，但 event.char
        # 仍是 "4"。用無 detail 的 Command/Control KeyPress 再檢查一次 char，避免同一問題
        # 在不同實體鍵盤或輸入法上重現；不是數字 4 的事件一律放行。
        for seq in ("<Command-KeyPress>", "<Control-KeyPress>"):
            self.bind(seq, self._handle_edit_window_digit_fallback, add="+")
            self.bind_all(
                seq,
                lambda e: self._handle_edit_window_digit_fallback(e)
                if self._edit_window_shortcut_active() else None,
                add="+",
            )

        # ==================== 關閉時自動存檔 ====================
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # 讓滑鼠滾輪／觸控板在右側參數區任何位置都能捲動（子元件預設會吃掉滾輪事件）
        self._enable_wheel_scroll()
        # 保底：中間檔案列表上如果還有其他元件（現在是 True Peak 疊圖 Label，之後
        # 萬一又多疊別的東西）沒有正確轉發捲動，這裡在 app 層級再補一層兜底。
        self._enable_center_table_wheel_fallback()

        # CTkOptionMenu._draw() 結尾會呼叫 self._canvas.update_idletasks()；多選切換版面時
        # device_menu 會被 _apply_meter_layout 重排，這個同步 update 可能引發 <Configure> 遞迴。
        # 把它的 canvas update_idletasks 改成 no-op（繪製已在前面完成，不影響外觀）。
        self._neutralize_ctk_update(getattr(self, "device_menu", None))

        # ==================== 背景分析 → 主執行緒 UI 更新佇列（thread-safe）====================
        # 背景執行緒不可直接呼叫 tkinter（mainloop 未啟動前 self.after() 會丟
        # RuntimeError: main thread is not in main loop）。改用 queue 把要做的 UI
        # 更新丟給主執行緒，由主執行緒輪詢後執行。
        self._ui_queue = queue.Queue()
        self.after(100, self._poll_ui_queue)

        # ==================== 初始化工作區（從存檔還原或新建） ====================
        self._load_session()

        # ==================== 新版本更新提要（若使用者未勾選「不要再提醒我」） ====================
        self.after(400, self._maybe_show_whats_new)

        # ==================== 啟動裝置偵測輪詢 ====================
        self._device_poll_job = None
        self.after(2000, self._poll_audio_devices)

    def _neutralize_ctk_update(self, widget):
        """把某個 CTk 元件內層 canvas 的 update_idletasks() 改成 no-op。
        CTkScrollbar/CTkOptionMenu 的 _draw() 結尾會同步呼叫它，在版面 resize 時
        會造成 <Configure>→_draw→update→<Configure> 無限遞迴卡死；繪製本身已在前面
        以 itemconfig/coords 完成，省略這個同步刷新只是延到正常事件迴圈處理，外觀不變。"""
        try:
            cv = getattr(widget, "_canvas", None)
            if cv is not None:
                cv.update_idletasks = lambda *a, **k: None
        except Exception:
            pass

    def _tame_scrollable(self, sf):
        """馴服 CustomTkinter 的 CTkScrollableFrame，根除「多選切雙欄版面 → 100% CPU 卡死」。

        CTk 原始設計綁了兩條會互相觸發的同步 <Configure>：
          • 內層 frame <Configure> → 設 canvas scrollregion = bbox("all")
          • _parent_canvas <Configure> → _fit_frame_dimensions_to_canvas 設內層寬 = canvas 寬
        一旦在視窗 realize 後重排/改尺寸（如多選把捲動框 grid 到另一欄），這兩條會
        同步乒乓互觸、永不收斂 → Tk_UpdateObjCmd ↔ <Configure> 無限遞迴、UI 凍結。

        作法：把這兩條改成「去抖動 + 只在寬度真的改變時才動」的單一處理，
        讓它最多跑一兩輪就停，徹底打斷同步迴圈；捲動功能（內容填滿寬度、垂直捲動）維持不變。
        """
        try:
            canvas = sf._parent_canvas
            win_id = sf._create_window_id
        except Exception:
            return  # CTk 內部結構若有變動就放棄馴服（不影響其他功能）

        def _fit():
            self._sf_fit_job = None
            try:
                cw = canvas.winfo_width()
                if getattr(self, "_sf_last_w", None) != cw:
                    self._sf_last_w = cw
                    canvas.itemconfigure(win_id, width=cw)   # 只在寬度真的變了才設，避免多餘 <Configure>
                canvas.configure(scrollregion=canvas.bbox("all"))
            except Exception:
                pass

        def _sched(event=None):
            if getattr(self, "_layout_settling", False):
                return  # 版面切換凍結期：交由 _finish_relayout 統一配適
            if getattr(self, "_sf_fit_job", None):
                try:
                    self.after_cancel(self._sf_fit_job)
                except Exception:
                    pass
            self._sf_fit_job = self.after(40, _fit)

        try:
            canvas.unbind("<Configure>")     # 解除 CTk 原本的同步配適
            sf.unbind("<Configure>")
            canvas.bind("<Configure>", _sched)
            sf.bind("<Configure>", _sched)
        except Exception:
            pass

    def _enable_wheel_scroll(self):
        """讓滑鼠滾輪／觸控板在右側參數區任何位置都能捲動。
        作法：在整個 app 層級攔截滾輪事件（bind_all），只要『游標所在的元件位於參數區內』
        就捲動參數區的畫布。這比逐一綁定每個子元件穩——不會漏掉任何深層的內部元件。
        唯一例外是 LUFS／批次 數值框（滾輪保留給微調數值）。"""
        sf = getattr(self, "lufs_wrapper", None)
        canvas = getattr(sf, "_parent_canvas", None)
        if sf is None or canvas is None:
            return
        _dbg = os.path.exists("/tmp/AM_DBG")

        def _rect(w):
            return (w.winfo_rootx(), w.winfo_rooty(),
                    w.winfo_rootx() + w.winfo_width(), w.winfo_rooty() + w.winfo_height())

        def _wheel(event):
            # 用『游標螢幕座標是否落在參數區畫布內』判斷（不靠 event.widget，最穩）
            try:
                x, y = event.x_root, event.y_root
                cx0, cy0, cx1, cy1 = _rect(canvas)
                over = (cx0 <= x < cx1) and (cy0 <= y < cy1)
            except Exception:
                over = False
            if _dbg:
                try:
                    with open("/tmp/am_wheel.log", "a") as f:
                        f.write(f"wheel over={over} x={getattr(event,'x_root','?')} "
                                f"y={getattr(event,'y_root','?')} delta={getattr(event,'delta','?')} "
                                f"widget={getattr(event,'widget',None)!r}\n")
                except Exception:
                    pass
            if not over:
                return  # 游標不在參數區 → 放行（左側樹／中央清單自己捲）
            # 游標在 LUFS／批次 數值框上 → 不捲（滾輪保留給微調）
            for ent in (getattr(self, "lufs_entry", None), getattr(self, "gain_adj_entry", None)):
                if ent is None:
                    continue
                try:
                    ex0, ey0, ex1, ey1 = _rect(ent)
                    if ex0 <= x < ex1 and ey0 <= y < ey1:
                        return
                except Exception:
                    pass
            d = getattr(event, "delta", 0)
            if d == 0:
                num = getattr(event, "num", 0)
                d = 1 if num == 4 else (-1 if num == 5 else 0)
            if d:
                canvas.yview_scroll(-1 if d > 0 else 1, "units")
            return "break"

        # 綁在所有層級，確保不管事件落到哪都能攔到：app 全域 + 視窗 + 捲動框 + 畫布 + 每個子元件
        def _bind_one(w):
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                try:
                    w.bind(seq, _wheel, add="+")
                except Exception:
                    pass

        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            try:
                self.bind_all(seq, _wheel, add="+")
            except Exception:
                pass
        for tgt in (self, sf, canvas):
            _bind_one(tgt)
        try:
            inner = sf.nametowidget(self.meter_frame.winfo_parent())
        except Exception:
            inner = sf

        def _walk(w):
            _bind_one(w)
            for c in w.winfo_children():
                _walk(c)
        _walk(inner)

    def _enable_center_table_wheel_fallback(self):
        """保底：中間檔案列表只要游標畫面座標落在它範圍內，滾輪／觸控板事件不管實際
        打到哪個子元件（目前是 True Peak 疊圖 Label，見 _refresh_true_peak_overlays_
        for_table／_forward_wheel_to_table），都補轉發給目前的檔案列表捲動。
        用 bind_all 掛在 app 層級的「all」tag——這是所有元件 bindtags 裡最後才輪到的，
        只要更前面（元件自己或 Treeview 內建）已經處理並回傳 'break'，這裡根本不會被
        執行到，不會造成同一次滾動被捲兩次；只有『前面都沒接住』時才會補上這一次。
        直接呼叫 _scroll_table_by_wheel 捲動（不再 event_generate 轉發），所以不會有
        「自己送出的事件又繞回自己」的遞迴問題，也不需要額外的防遞迴旗標。"""
        def _rect(w):
            return (w.winfo_rootx(), w.winfo_rooty(),
                    w.winfo_rootx() + w.winfo_width(), w.winfo_rooty() + w.winfo_height())

        def _wheel(event, shift=False):
            table = getattr(self, "file_table", None)
            if table is None:
                return
            try:
                if not table.winfo_exists():
                    return
                x0, y0, x1, y1 = _rect(table)
                x, y = event.x_root, event.y_root
                over = x0 <= x < x1 and y0 <= y < y1
                self._wheel_dbg(
                    f"fallback: widget={event.widget!r} delta={getattr(event,'delta','?')} "
                    f"x={x} y={y} rect=({x0},{y0},{x1},{y1}) over={over} "
                    f"is_table={event.widget is table}"
                )
                if not over:
                    return
            except Exception:
                return
            if event.widget is table:
                return  # 已經是打在 table 本身，交給它既有的處理，不用再多轉一次
            # 跟 _forward_wheel_to_table 一樣直接捲（見 _scroll_table_by_wheel）。
            self._scroll_table_by_wheel(table, event, shift=shift)
            return "break"

        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind_all(seq, lambda e: _wheel(e, shift=False), add="+")
        for seq in ("<Shift-MouseWheel>", "<Shift-Button-4>", "<Shift-Button-5>"):
            self.bind_all(seq, lambda e: _wheel(e, shift=True), add="+")

    # ========== Workspace Management ==========

    def _bind_column_drag_reorder(self, tree):
        """讓中央表格的欄位標題可以左右拖曳互換順序（不含最左邊 #0 勾選欄，那個固定在最左）。
        原理：ttk.Treeview 沒有內建欄位拖曳重排，用滑鼠事件手動判斷拖過了哪一欄、
        即時交換 displaycolumns 裡兩者的位置。"""
        state = {"col": None}

        def _col_at(x):
            col_id = tree.identify_column(x)  # 例如 "#0"（樹欄）、"#1"、"#2"...（依目前顯示順序）
            if not col_id or col_id == "#0":
                return None
            try:
                idx = int(col_id[1:]) - 1
            except ValueError:
                return None
            disp = list(tree["displaycolumns"])
            if 0 <= idx < len(disp):
                return idx, disp
            return None

        def _on_press(event):
            if tree.identify_region(event.x, event.y) != "heading":
                state["col"] = None
                return
            hit = _col_at(event.x)
            state["col"] = hit[0] if hit else None

        def _on_drag(event):
            if state["col"] is None:
                return
            if tree.identify_region(event.x, event.y) != "heading":
                return
            hit = _col_at(event.x)
            if not hit:
                return
            idx, disp = hit
            src = state["col"]
            if idx == src:
                return
            disp[src], disp[idx] = disp[idx], disp[src]
            tree["displaycolumns"] = disp
            state["col"] = idx

        def _on_release(event):
            state["col"] = None

        tree.bind("<ButtonPress-1>", _on_press, add="+")
        tree.bind("<B1-Motion>", _on_drag, add="+")
        tree.bind("<ButtonRelease-1>", _on_release, add="+")

    def _bind_smooth_hscroll(self, tree):
        """中央表格欄位比可視寬度寬時，Shift+滾輪／觸控板兩指左右手勢的橫向捲動。
        ttk.Treeview 內建的 Shift-MouseWheel 預設處理是 xview_scroll(1,'units')，
        一個 tick 就整格跳一次，觸控板連續小幅滑動時看起來就是文字一格一格『卡卡』跳，
        不像原生 App 那樣跟手。改成依目前可視比例換算成 xview_moveto 的一小段 fraction，
        位移量跟滾動力度成比例、連續平滑；同一 widget 上的 bind 會覆蓋掉 class 內建預設，
        回傳 'break' 避免內建處理又跟著多跳一次。"""
        def _wheel(e):
            d = getattr(e, "delta", 0)
            if d == 0:
                num = getattr(e, "num", 0)
                d = 120 if num == 4 else (-120 if num == 5 else 0)
            if not d:
                return "break"
            try:
                first, last = tree.xview()
                visible_frac = last - first
                if visible_frac <= 0 or visible_frac >= 1.0:
                    return "break"  # 內容沒有超寬，沒有可以橫向捲動的空間
                width = max(tree.winfo_width(), 1)
                delta_px = -(d / 120.0) * 48.0
                new_first = first + (delta_px * visible_frac) / width
                new_first = max(0.0, min(new_first, 1.0 - visible_frac))
                tree.xview_moveto(new_first)
            except Exception:
                pass
            # True Peak 那兩欄是疊在儲存格上的獨立 tk.Label（見 _refresh_true_peak_overlays_
            # for_table），不會跟著 xview_moveto 自動移動，捲動時要自己再補畫一次位置，
            # 否則就是使用者說的『貼圖延遲跟著移動』。
            self._schedule_true_peak_overlay_refresh()
            return "break"

        for seq in ("<Shift-MouseWheel>", "<Shift-Button-4>", "<Shift-Button-5>"):
            tree.bind(seq, _wheel)
        # 上下捲動（一般滾輪／觸控板垂直手勢）沒有覆蓋掉 ttk 內建處理，只是額外補一個
        # instance-level 的 add="+" binding——它會在 class 內建的垂直捲動之前先觸發，
        # 所以這裡不能直接讀 bbox（讀到的還是捲動前的舊位置），要交給
        # _schedule_true_peak_overlay_refresh 的 after() 延遲一小段時間，讓 Tk 先把真正
        # 的捲動處理完，才能拿到捲動後的新位置。
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            tree.bind(seq, lambda e: self._schedule_true_peak_overlay_refresh(), add="+")

    def _add_workspace(self, name: str) -> int:
        ws = Workspace(name=name)
        self.workspaces.append(ws)
        idx = len(self.workspaces) - 1

        # --- Left inner frame ---
        inner_left = ctk.CTkFrame(self.left_content_container, fg_color="transparent")
        inner_left.grid(row=0, column=0, sticky="nsew")
        inner_left.rowconfigure(0, weight=1)
        inner_left.columnconfigure(0, weight=1)
        inner_left.grid_remove()

        tree = ttk.Treeview(inner_left, show="tree", selectmode="extended")
        tree.grid(row=0, column=0, padx=(10, 0), pady=(0, 0), sticky="nsew")
        # 資料夾：亮色；資料夾內音檔：淡灰（像內容預覽）
        tree.tag_configure("dirfolder", foreground="#E8E8EA")
        tree.tag_configure("dimfile", foreground="#7C828A")
        # stretch=False：欄寬由我們自己控制成 max(可視寬, 內容寬)，
        # 這樣面板被拉窄、名稱被吃到時，欄寬仍維持內容寬 → 橫向捲軸才會出現。
        tree.column("#0", minwidth=120, stretch=False)
        tree.bind("<Configure>", lambda e, w=ws: self._apply_tree_column_width(w), add="+")

        # 捲軸：用原生 ttk（拖曳由 C 層處理，順很多；橫向卡頓的根因是 CTkScrollbar 的
        # Python/canvas 拖曳處理）。扁平深色、無箭頭，只有內容被切到時才自動出現。
        sb_y = ttk.Scrollbar(inner_left, orient="vertical", style="AM.Vertical.TScrollbar", command=tree.yview)
        sb_y.grid(row=0, column=1, sticky="ns", padx=(2, 4), pady=(0, 0))
        sb_x = ttk.Scrollbar(inner_left, orient="horizontal", style="AM.Horizontal.TScrollbar", command=tree.xview)
        sb_x.grid(row=1, column=0, sticky="ew", padx=(10, 2), pady=(2, 8))

        # Shift+滾輪 / 觸控板 → 橫向捲動（比拖曳捲軸更順）
        def _hwheel(e, t=tree):
            d = getattr(e, "delta", 0)
            if d == 0:
                num = getattr(e, "num", 0)
                d = 1 if num == 4 else (-1 if num == 5 else 0)
            if d:
                t.xview_scroll(-1 if d > 0 else 1, "units")
            return "break"
        tree.bind("<Shift-MouseWheel>", _hwheel)
        tree.bind("<Shift-Button-4>", _hwheel)
        tree.bind("<Shift-Button-5>", _hwheel)

        def _auto_sb(sb):
            # 只有「需不需要顯示」真的改變時才動 grid，避免每次捲動都重排版面造成卡頓。
            state = {"shown": None}
            def _cb(lo, hi):
                try:
                    need = not (float(lo) <= 0.0 and float(hi) >= 1.0)
                    # 版面切換凍結期不切換捲軸顯示（grid/grid_remove 會改幾何 → 觸發迴圈）
                    if need != state["shown"] and not getattr(self, "_layout_settling", False):
                        sb.grid() if need else sb.grid_remove()
                        state["shown"] = need
                    sb.set(lo, hi)
                except Exception:
                    pass
            return _cb
        tree.configure(yscrollcommand=_auto_sb(sb_y), xscrollcommand=_auto_sb(sb_x))
        sb_y.grid_remove()
        sb_x.grid_remove()

        tree.bind("<ButtonPress-1>", self.on_tree_drag_start)
        tree.bind("<B1-Motion>", self.on_tree_drag_motion)
        tree.bind("<ButtonRelease-1>", self.on_tree_drag_release)
        # 雙擊僅展開/收合資料夾（ttk 內建行為），不再自動匯入到中央工作區。
        # 匯入只在「主動拖曳到中央工作區」時才會發生。
        # 從左側樹移除選取項目（含整包資料夾）；回傳 "break" 避免觸發全域 Delete（刪中央檔案）
        tree.bind("<Delete>", lambda e, w=ws: self._remove_tree_selection(w) or "break")
        tree.bind("<BackSpace>", lambda e, w=ws: self._remove_tree_selection(w) or "break")
        tree.bind("<Button-2>", lambda e, w=ws: self._show_tree_context_menu(e, w))
        tree.bind("<Button-3>", lambda e, w=ws: self._show_tree_context_menu(e, w))
        # 左側資料夾樹的全選：macOS 把 Cmd+A 轉成 <<SelectAll>> 送到焦點 widget（一定要綁它才收得到），
        # 另綁 <Command-a>/<Control-a> 給其他情況。
        tree.bind("<<SelectAll>>", lambda e, t=tree: self._select_all_tree(t))
        tree.bind("<Command-a>", lambda e, t=tree: self._select_all_tree(t))
        tree.bind("<Command-A>", lambda e, t=tree: self._select_all_tree(t))
        tree.bind("<Control-a>", lambda e, t=tree: self._select_all_tree(t))
        tree.bind("<Control-A>", lambda e, t=tree: self._select_all_tree(t))
        if _DND_AVAILABLE:
            try:
                tree.drop_target_register(DND_FILES)
                tree.dnd_bind("<<Drop>>", self._on_drop_files_left_tree)
            except Exception:
                pass

        ws.dir_tree = tree
        ws.left_panel_inner = inner_left

        # --- Center inner frame ---
        inner_center = ctk.CTkFrame(self.center_content_container, fg_color="transparent")
        inner_center.grid(row=0, column=0, sticky="nsew")
        inner_center.rowconfigure(0, weight=1)
        inner_center.columnconfigure(0, weight=1)
        inner_center.grid_remove()

        # 中央工作區：勾選（全選）擺在『真正的最左邊』→ 用 #0 樹欄當勾選欄（展開/收合箭頭也在這），
        # 檔名移到緊接其後的「檔案」欄。資料欄 values 依 cols 順序（True Peak 各自緊接在對應的
        # LUFS 欄位後面）：(檔名, 時長, 狀態, 原始LUFS, 原始TruePeak, 目標LUFS, 目標TruePeak)。
        cols = ("檔案", "Duration", "Status", "原始 LUFS", "原始 True Peak", "目標 LUFS", "目標 True Peak")
        ft = ttk.Treeview(inner_center, columns=cols, show="tree headings", selectmode="extended",
                          style="FileTable.Treeview")
        # 顯示順序：檔名緊接勾選欄之後、狀態欄擺最右。可以拖曳欄位標題互換順序（見 _bind_column_drag）。
        ft["displaycolumns"] = ("檔案", "Duration", "原始 LUFS", "原始 True Peak",
                                "目標 LUFS", "目標 True Peak", "Status")
        # 全選圖示統一用與檔案/資料夾同款的藍底黑勾（_render_check_icon），不要用綠底白勾的 ✅ emoji。
        if not hasattr(self, "_header_check_icon"):
            self._header_check_icon = self._render_check_icon(20, True)
        ft.heading("#0", text="", image=self._header_check_icon, command=lambda: self._toggle_all_exports())  # #0 = 勾選/全選
        ft.heading("檔案", text="檔案 / 資料夾")
        ft.heading("Duration", text="時長")
        ft.heading("Status", text="狀態")
        ft.heading("原始 LUFS", text="原始 LUFS")
        ft.heading("原始 True Peak", text="原始 True Peak")
        ft.heading("目標 LUFS", text="目標 LUFS")
        ft.heading("目標 True Peak", text="目標 True Peak")
        ft.column("#0", width=64, minwidth=58, anchor="center", stretch=False)   # 勾選欄（含展開箭頭）：加大方便點擊
        ft.column("檔案", width=180, minwidth=120, anchor="w", stretch=True)
        ft.column("Duration", width=74, minwidth=58, anchor="center", stretch=True)
        ft.column("Status", width=92, minwidth=72, anchor="center", stretch=True)
        ft.column("原始 LUFS", width=100, minwidth=84, anchor="center", stretch=True)
        ft.column("原始 True Peak", width=100, minwidth=84, anchor="center", stretch=True)
        ft.column("目標 LUFS", width=100, minwidth=84, anchor="center", stretch=True)
        ft.column("目標 True Peak", width=100, minwidth=84, anchor="center", stretch=True)
        ft.tag_configure("folder", foreground="#E0E0E0")
        ft.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")

        # 檔案很多需要上下滑動時，右邊緣顯示捲軸（同左側資料夾樹的樣式與自動顯示/隱藏行為）。
        ft_sb_y = ttk.Scrollbar(inner_center, orient="vertical", style="AM.Vertical.TScrollbar", command=ft.yview)
        ft_sb_y.grid(row=0, column=1, sticky="ns", padx=(0, 4), pady=10)
        ft.configure(yscrollcommand=_auto_sb(ft_sb_y))
        ft_sb_y.grid_remove()
        ws.file_table_sb_y = ft_sb_y

        self._bind_column_drag_reorder(ft)
        ft.bind("<<TreeviewSelect>>", self.on_table_select)
        # add="+"：<Button-1> 跟 <ButtonPress-1> 是同一個事件序列，_bind_column_drag_reorder
        # 已經先用 add="+" 掛了一個 <ButtonPress-1> 處理常式；這裡如果不加 add="+"，
        # 一般的 .bind() 會直接整個取代掉同一序列既有的所有 callback，
        # 害拖曳欄位互換順序的功能被悄悄蓋掉、完全沒反應（已實測重現這個 bug）。
        ft.bind("<Button-1>", self._on_file_table_click, add="+")
        ft.bind("<Button-2>", self.on_table_right_click)
        ft.bind("<Button-3>", self.on_table_right_click)
        # 回傳 "break" 攔截事件，避免再冒泡到 app 級的 <Delete> 綁定而連觸發兩次
        ft.bind("<Delete>", lambda e: (self.remove_selected_files(), "break")[1])
        ft.bind("<BackSpace>", lambda e: (self.remove_selected_files(), "break")[1])
        # 全選（Cmd/Ctrl+A）：直接綁在表格 widget 上。
        # macOS 上 Cmd+A 其實是被系統 Edit 選單攔截、再以虛擬事件 <<SelectAll>> 送到目前
        # 焦點 widget，所以「一定要」綁 <<SelectAll>>（這才是真正會收到的事件）；
        # 另外保險再綁 <Command-a>/<Control-a> 給非 macOS／直接按鍵的情況。
        ft.bind("<<SelectAll>>", self._select_all_files)
        ft.bind("<Command-a>", self._select_all_files)
        ft.bind("<Command-A>", self._select_all_files)
        ft.bind("<Control-a>", self._select_all_files)
        ft.bind("<Control-A>", self._select_all_files)
        if _DND_AVAILABLE:
            try:
                ft.drop_target_register(DND_FILES)
                ft.dnd_bind("<<Drop>>", self._on_drop_files)
            except Exception:
                pass

        # 空狀態引導：表格還沒有檔案時，浮一層提示告訴使用者「要用拖曳的」
        # （左樹檔案是預覽、雙擊只展開收合——不講的話新手會卡在這裡）。
        hint = ctk.CTkLabel(
            inner_center,
            text="這裡還沒有檔案\n\n⬅ 把左側清單的檔案或資料夾「拖曳」到這裡\n（也可以直接從 Finder 拖入音檔）",
            font=("Arial", 13), text_color="#6E6E73", fg_color="transparent", justify="center")
        hint.place(relx=0.5, rely=0.42, anchor="center")
        if _DND_AVAILABLE:
            try:
                # 提示層蓋在表格上方，也要能接 Finder 拖放，否則空表格正中央反而放不了檔案
                hint.drop_target_register(DND_FILES)
                hint.dnd_bind("<<Drop>>", self._on_drop_files)
            except Exception:
                pass
        ws.empty_hint = hint

        ws.file_table = ft
        ws.center_panel_inner = inner_center
        self._bind_smooth_hscroll(ft)

        return idx

    def _update_empty_hint(self, ws=None):
        """依工作區是否有檔案，顯示/隱藏中央表格的空狀態引導。"""
        ws = ws or self.workspaces[self.active_ws_idx]
        hint = getattr(ws, "empty_hint", None)
        if hint is None:
            return
        try:
            if ws.audio_files:
                hint.place_forget()
            else:
                hint.place(relx=0.5, rely=0.42, anchor="center")
        except Exception:
            pass

    def _switch_workspace(self, idx: int):
        self.stop_playback()

        # Hide current workspace
        if self.workspaces:
            old = self.workspaces[self.active_ws_idx]
            if old.left_panel_inner:
                old.left_panel_inner.grid_remove()
            if old.center_panel_inner:
                old.center_panel_inner.grid_remove()

        self.active_ws_idx = idx

        # Show new workspace
        new = self.workspaces[idx]
        new.left_panel_inner.grid()
        new.center_panel_inner.grid()

        # Clear right panel
        self.lbl_active_file.configure(text="No File Selected")
        self.current_audio = None
        self.original_lufs_val = None
        self.lbl_info_current.configure(text="--")
        self.lbl_info_target.configure(text="--")
        self.lbl_info_gain.configure(text="--")
        self.waveform_canvas.delete("all")
        self._current_wave_entries = []
        self._apply_right_layout()
        self.check_export_ready()

    def _refresh_tab_buttons(self):
        for w in self.tab_btn_frame.winfo_children():
            w.destroy()
        multi = len(self.workspaces) > 1  # 至少保留一個工作區 → 只有多於一個時才顯示叉叉
        for i, ws in enumerate(self.workspaces):
            is_active = (i == self.active_ws_idx)
            # 此工作區已存成 .abproj → 顯示名稱；尚未存檔 → 名稱後加 •（提示需另存）
            label = ws.name if ws.project_file_path else ws.name + " •"
            # 每個 tab 用一個小 frame 包住：名稱鈕 + 右側叉叉（可直接關閉該工作區）
            tab = ctk.CTkFrame(self.tab_btn_frame,
                               fg_color=COLOR_CYAN if is_active else "#2C2C2E", corner_radius=6)
            tab.pack(side="left", padx=(0, 4), pady=5)

            # 行內改名中的那一個 tab → 直接用輸入框取代名稱鈕，可即時打字
            if getattr(self, "_renaming_idx", None) == i:
                rn_var = tk.StringVar(value=ws.name)
                rn_entry = tk.Entry(
                    tab, textvariable=rn_var, width=14,
                    font=("Roboto", 12, "bold"), justify="center",
                    bg="#1A1A1D", fg="white", insertbackground="white",
                    relief="flat", highlightthickness=1,
                    highlightbackground=COLOR_CYAN, highlightcolor=COLOR_CYAN,
                )
                rn_entry.pack(side="left", padx=3, pady=4)
                rn_entry.bind("<Return>",   lambda e, idx=i, en=rn_entry: self._commit_inline_rename(idx, en))
                rn_entry.bind("<KP_Enter>", lambda e, idx=i, en=rn_entry: self._commit_inline_rename(idx, en))
                rn_entry.bind("<FocusOut>", lambda e, idx=i, en=rn_entry: self._commit_inline_rename(idx, en))
                rn_entry.bind("<Escape>",   lambda e: self._cancel_inline_rename())
                # 延後一拍再 focus + 全選，確保 widget 已建立、游標一定會進到輸入框
                self.after(1, lambda en=rn_entry: self._focus_rename_entry(en))
                continue

            name_btn = ctk.CTkButton(
                tab, text=label,
                width=96 if multi else 116, height=28,
                fg_color="transparent", corner_radius=6,
                text_color="black" if is_active else "#8E8E93",
                # hover 不再變深/反黑：把 hover 色設成 tab 本身的底色 → 滑過去外觀不變
                hover_color=COLOR_CYAN if is_active else "#2C2C2E",
                font=("Roboto", 12, "bold") if is_active else ("Roboto", 12),
                command=lambda idx=i: self._on_tab_click(idx)
            )
            name_btn.pack(side="left", padx=(3, 0))
            # 雙擊名稱 → 直接在上面打字改名（不再跳出對話框）
            name_btn.bind("<Double-Button-1>", lambda e, idx=i: self._begin_inline_rename(idx))
            name_btn.bind("<Button-2>", lambda e, idx=i: self._show_ws_context_menu(e, idx))
            name_btn.bind("<Button-3>", lambda e, idx=i: self._show_ws_context_menu(e, idx))
            if multi:
                # 小而低調的關閉鈕：平常是淡淡的 ✕，hover 時變成紅色圓底 + 白色 ✕
                x_color = "#0B4A54" if is_active else "#9A9AA0"
                close_btn = ctk.CTkButton(
                    tab, text="✕", width=18, height=18,
                    fg_color="transparent", corner_radius=9,
                    text_color=x_color, hover_color="#E5484D",
                    font=("Roboto", 10),
                    command=lambda idx=i: self._close_workspace(idx)
                )
                close_btn.pack(side="left", padx=(1, 5), pady=5)
                close_btn.bind("<Enter>", lambda e, b=close_btn: b.configure(text_color="#FFFFFF"))
                close_btn.bind("<Leave>", lambda e, b=close_btn, c=x_color: b.configure(text_color=c))

    def _on_tab_click(self, idx):
        """點 tab 切換工作區；若點的已經是目前工作區就不重建按鈕
        （避免重建把第二次點擊吃掉，讓雙擊改名能穩定觸發）。"""
        if idx == self.active_ws_idx:
            return
        self._switch_workspace(idx)
        self._refresh_tab_buttons()

    def _begin_inline_rename(self, idx):
        """雙擊（或右鍵選重命名）→ 把該 tab 變成可直接打字的輸入框。"""
        self._renaming_idx = idx
        self._refresh_tab_buttons()

    def _commit_inline_rename(self, idx, entry):
        """套用行內改名：Enter / 失焦時。"""
        if getattr(self, "_renaming_idx", None) != idx:
            return  # 已處理過（避免 Return 與 FocusOut 重複觸發）
        try:
            new_name = entry.get().strip()
        except Exception:
            new_name = ""
        self._renaming_idx = None
        if new_name and idx < len(self.workspaces):
            self.workspaces[idx].name = new_name
            self._schedule_autosave()
        self._refresh_tab_buttons()

    def _cancel_inline_rename(self):
        """Esc → 取消行內改名，名稱不變。"""
        self._renaming_idx = None
        self._refresh_tab_buttons()

    def _focus_rename_entry(self, entry):
        """讓行內改名輸入框取得焦點並全選文字。"""
        try:
            if entry.winfo_exists():
                entry.focus_set()
                entry.select_range(0, "end")
                entry.icursor("end")
        except Exception:
            pass

    def _on_add_workspace(self):
        n = len(self.workspaces) + 1
        idx = self._add_workspace(name=f"工作區 {n}")
        self._switch_workspace(idx)
        self._refresh_tab_buttons()
        self._schedule_autosave()

    # ========== Project File (per-workspace) ==========

    def _projects_folder(self) -> str:
        folder = os.path.join(os.path.expanduser("~"), "Documents", "Audio Balancer Projects")
        os.makedirs(folder, exist_ok=True)
        return folder

    def _show_ws_context_menu(self, event, idx):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="✏️  重命名", command=lambda: self._begin_inline_rename(idx))
        menu.add_separator()
        menu.add_command(label="💾  儲存專案（全部工作區）", command=lambda: self._save_project())
        menu.add_command(label="📂  另存專案為...", command=lambda: self._save_project_as())
        menu.add_command(label="📂  開啟專案...", command=lambda: self._open_project())
        menu.add_separator()
        menu.add_command(label="✕  關閉此工作區", command=lambda: self._close_workspace(idx))
        menu.post(event.x_root, event.y_root)

    # ── 專案 = 整個視窗（所有工作區）；存/讀都是一整包 ─────────────────
    def _serialize_workspace(self, ws):
        """把單一工作區序列化成 dict（樹結構 + 中央檔案清單）。"""
        ws_data = {
            "name": ws.name,
            "current_folder": ws.current_folder,
            "project_file_path": ws.project_file_path,   # 每個工作區各自關聯的 .abproj
            "tree_nodes": self._serialize_dir_tree(ws),
            "audio_files": [],
        }
        for e in ws.audio_files:
            lufs_val = e["lufs"] if isinstance(e["lufs"], float) else None
            target_val = e["target_lufs"] if isinstance(e.get("target_lufs"), float) else lufs_val
            tp_val = e["true_peak"] if isinstance(e.get("true_peak"), float) else None
            ws_data["audio_files"].append({
                "path": e["path"], "name": e["name"], "duration": e["duration"],
                "lufs": lufs_val, "target_lufs": target_val, "export": e.get("export", True),
                "source_bit_depth": e.get("source_bit_depth"),
                "true_peak": tp_val,
                "edit_regions": e.get("edit_regions"),  # Edit Window 的非破壞性剪輯記錄
            })
        return ws_data

    def _project_data(self):
        """整個專案（所有工作區）的可存檔資料。session 自動存檔與 .abproj 共用此格式。"""
        self._sync_open_edit_window_entries()
        return {
            "version": 2,
            "type": "audio_master_project",
            "export_folder": self.export_folder,
            "active_ws_idx": self.active_ws_idx,
            "workspaces": [self._serialize_workspace(ws) for ws in self.workspaces],
        }

    def _restore_workspace_into(self, ws, ws_data):
        """把序列化的工作區資料還原到既有的 ws（樹 + 中央清單）。"""
        saved_proj = ws_data.get("project_file_path")
        ws.project_file_path = saved_proj if (saved_proj and os.path.isfile(saved_proj)) else None
        tree_nodes = ws_data.get("tree_nodes")
        saved_folder = ws_data.get("current_folder", "")
        if tree_nodes:
            self._restore_dir_tree(ws, tree_nodes)
        elif saved_folder and os.path.isdir(saved_folder):
            self._populate_dir_tree_for_ws(ws, saved_folder)
        for ef in ws_data.get("audio_files", []):
            path = ef["path"]
            # 檔案暫時不存在（外接/網路碟未掛載、被移走）→ 不要直接丟掉，否則 autosave
            # 會把『縮水後』的清單寫回，永久刪掉這些檔的記錄。改標成「離線」保留下來：
            # 不分析、不匯出，但仍序列化回存；碟接回再重新匯入即可。
            exists = os.path.isfile(path)
            lufs_saved = ef.get("lufs")
            target_saved = ef.get("target_lufs")
            tp_saved = ef.get("true_peak")
            dur_saved = ef.get("duration", "--:--")
            export_val = ef.get("export", True)
            entry = {
                "name": ef["name"], "path": path, "duration": dur_saved,
                "status": "🟡 載入中" if exists else "🔴 離線",
                "lufs": lufs_saved if lufs_saved is not None else "--",
                "target_lufs": target_saved, "audio": None, "export": export_val,
                "source_bit_depth": ef.get("source_bit_depth"),
                "true_peak": tp_saved if isinstance(tp_saved, float) else None,
                "edit_regions": ef.get("edit_regions"),
                "_table": ws.file_table,
            }
            ws.audio_files.append(entry)
            lufs_display = f"{lufs_saved:.1f} LUFS" if lufs_saved is not None else "--"
            target_display = f"{target_saved:.1f} LUFS" if target_saved is not None else "--"
            orig_tp_display, target_tp_display = self._true_peak_displays(entry)
            self._insert_file_row_into(ws.file_table, path, export_val,
                                       dur_saved, entry["status"], lufs_display, target_display,
                                       orig_tp_display, target_tp_display)
            if exists:
                # lufs_saved 存在時代表存檔裡已經有忠實的原始 LUFS → 只補回 AudioSegment/時長，
                # 不要讓背景重新量測把它蓋掉（見 analyze_single_file 的 preserve_saved_lufs 說明）。
                threading.Thread(target=self.analyze_single_file,
                                 args=(entry,), kwargs={"preserve_saved_lufs": lufs_saved is not None},
                                 daemon=True).start()
        self._update_empty_hint(ws)

    def _clear_all_workspaces(self):
        for ws in self.workspaces:
            try:
                if ws.left_panel_inner:
                    ws.left_panel_inner.destroy()
                if ws.center_panel_inner:
                    ws.center_panel_inner.destroy()
            except Exception:
                pass
        self.workspaces = []
        self.active_ws_idx = 0

    def _flash_saved(self, path):
        """在『儲存專案』按鈕上短暫顯示已儲存，讓使用者確定真的有存到（避免「沒有任何作用」的疑慮）。"""
        try:
            self.btn_save_project.configure(text="✅ 已儲存")
            self.after(1600, lambda: self.btn_save_project.configure(text="💾  儲存專案"))
        except Exception:
            pass

    def _save_project(self):
        """儲存『目前這個工作區』到它自己的 .abproj；該工作區還沒存過 → 自動跳『另存新檔』。"""
        ws = self.workspaces[self.active_ws_idx]
        if not ws.project_file_path:
            self._save_project_as()
            return
        try:
            self._write_workspace_file(ws.project_file_path, ws)
        except Exception:
            traceback.print_exc()
            messagebox.showerror("儲存失敗", f"無法儲存專案：\n{ws.project_file_path}", parent=self)
            return
        self._refresh_tab_buttons()
        self._flash_saved(ws.project_file_path)

    def _save_project_as(self):
        ws = self.workspaces[self.active_ws_idx]
        path = filedialog.asksaveasfilename(
            title="另存新檔",
            initialfile=ws.name + ".abproj",
            initialdir=self._projects_folder(),
            defaultextension=".abproj",
            filetypes=[("Audio Balancer Project", "*.abproj"), ("All Files", "*")],
        )
        if not path:
            return
        # 手動補上副檔名（不完全依賴 defaultextension —— macOS/Tk 有時不會自動補）
        if not path.lower().endswith(".abproj"):
            path += ".abproj"
        ws.project_file_path = path
        try:
            self._write_workspace_file(path, ws)
        except Exception:
            traceback.print_exc()
            messagebox.showerror("儲存失敗", f"無法儲存專案：\n{path}", parent=self)
            return
        self._refresh_tab_buttons()
        self._flash_saved(path)

    def _write_workspace_file(self, path, ws):
        """把單一工作區寫成 .abproj（檔案格式相容：workspaces 內放這一個工作區）。"""
        self._sync_open_edit_window_entries()
        data = {
            "version": 2,
            "type": "audio_master_project",
            "export_folder": self.export_folder,
            "workspaces": [self._serialize_workspace(ws)],
        }
        # 原子寫入：先寫暫存檔再 os.replace，避免寫到一半當機留下截斷的損毀 JSON。
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _open_project(self):
        """開啟 .abproj：把裡面的工作區『新增到目前工作區的最右邊』並切換過去（不取代現有工作區）。"""
        path = filedialog.askopenfilename(
            title="開啟專案",
            initialdir=self._projects_folder(),
            # 「所有檔案」放第一個 → 在 macOS/Tk 上 .abproj 不會被灰掉、一定點得到
            filetypes=[("All Files", "*"), ("Audio Balancer Project", "*.abproj")],
        )
        if not path or not os.path.isfile(path):
            return
        self._open_project_path(path)

    def _open_project_path(self, path):
        """直接開啟指定路徑的 .abproj（供選單開檔與雙擊檔案／Finder 開啟共用）。"""
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            traceback.print_exc()
            messagebox.showerror("開啟失敗", f"無法開啟專案檔：\n{path}", parent=self)
            return
        if "workspaces" not in data:   # 相容舊版單一工作區（沒有 "workspaces" 欄位）
            data = {"workspaces": [data], "export_folder": data.get("export_folder", "")}
        self._append_project_data(data, path)

    def _append_project_data(self, data, path):
        """把專案檔的工作區附加到最右邊，切換到新加入的第一個。
        若該檔只含單一工作區 → 把它綁定到此檔（之後 Cmd+S 直接存回）。"""
        self.stop_playback()
        ws_list = data.get("workspaces", [])
        if not isinstance(ws_list, list) or not ws_list:
            return
        ws_list = [w for w in ws_list if isinstance(w, dict)]
        if not ws_list:
            return
        # 只在「該檔是單一工作區、且尚未有別的工作區綁到同一個 .abproj」時才綁定，
        # 否則重複開啟同一檔會出現兩個都綁同路徑的工作區、autosave 互相覆寫。
        already_bound = any(getattr(w, "project_file_path", None) == path for w in self.workspaces)
        bind = (len(ws_list) == 1) and not already_bound
        first_new_idx = len(self.workspaces)
        for ws_data in ws_list:
            idx = self._add_workspace(ws_data.get("name", f"工作區 {len(self.workspaces) + 1}"))
            self._restore_workspace_into(self.workspaces[idx], ws_data)
            self.workspaces[idx].project_file_path = path if bind else None
        saved_export = data.get("export_folder", "")
        if saved_export and os.path.isdir(saved_export) and not self.export_folder:
            self.export_folder = saved_export
            self._update_export_path_label()
        self._switch_workspace(first_new_idx)   # 切到剛加入、位於最右邊的那一個
        self._refresh_tab_buttons()
        self.check_export_ready()
        self._schedule_autosave()

    def _close_workspace(self, idx):
        if len(self.workspaces) <= 1:
            return  # 至少保留一個工作區
        ws = self.workspaces[idx]
        # 有內容的工作區要先確認：關閉會連同所有檔案設定一起消失，且不可復原（誤點 ✕ 的保險）
        has_content = bool(ws.audio_files) or bool(
            ws.dir_tree is not None and ws.dir_tree.get_children(""))
        if has_content:
            if not messagebox.askyesno(
                    "關閉工作區",
                    f"確定要關閉工作區「{ws.name}」？\n\n"
                    f"其中 {len(ws.audio_files)} 個檔案的清單與目標設定將一併移除，無法復原。\n"
                    "（不會刪除磁碟上的原始音檔）",
                    icon="warning", default="no", parent=self):
                return
        ws.left_panel_inner.destroy()
        ws.center_panel_inner.destroy()
        self.workspaces.pop(idx)
        new_idx = min(idx, len(self.workspaces) - 1)
        self.active_ws_idx = new_idx
        self._switch_workspace(new_idx)
        self._refresh_tab_buttons()
        self._schedule_autosave()

    def _on_drop_files(self, event):
        """從 Finder 拖入檔案或資料夾"""
        valid_exts = IMPORTABLE_EXTS
        raw = event.data or ""
        # tkinterdnd2 在 macOS 傳回的路徑用空格分隔，帶括號
        paths = self.tk.splitlist(raw)
        for p in paths:
            p = p.strip()
            if os.path.isfile(p) and p.lower().endswith(valid_exts):
                self.add_file_to_table(p)
            elif os.path.isdir(p):
                # 遞迴收集（與左樹匯入一致），並容忍權限不足等讀取錯誤
                for root, _dirs, files in os.walk(p, onerror=lambda e: None):
                    for fname in sorted(files):
                        if fname.lower().endswith(valid_exts):
                            self.add_file_to_table(os.path.join(root, fname))

    def _on_drop_files_left_tree(self, event):
        """從 Finder 拖入檔案或資料夾到左側資料夾樹：加進樹狀結構（與 Import File／Import Folder 一致，
        保留現有內容、不清掉），不會直接進中央工作區。"""
        tree = event.widget
        ws = next((w for w in self.workspaces if w.dir_tree == tree), None)
        if ws is None:
            ws = self.workspaces[self.active_ws_idx]
        valid_exts = IMPORTABLE_EXTS
        raw = event.data or ""
        # tkinterdnd2 在 macOS 傳回的路徑用空格分隔，帶括號
        paths = self.tk.splitlist(raw)
        loose_files = []
        touched = False
        for p in paths:
            p = p.strip()
            if os.path.isfile(p) and p.lower().endswith(valid_exts):
                loose_files.append(p)
            elif os.path.isdir(p):
                self._add_folder_to_dir_tree(ws, p)
                touched = True
        if loose_files:
            self._add_files_to_dir_tree(ws, loose_files)
            touched = True
        if touched:
            self._refresh_dir_tree_counts(ws)
            self._schedule_autosave()

    # ========== Session Save / Restore ==========

    def _session_path(self):
        return os.path.join(os.path.expanduser("~"), ".audio_balancer_session.json")

    # ========== App 偏好設定（跟專案/session 無關，例如「不要再提醒我」）==========

    def _prefs_path(self):
        return os.path.join(os.path.expanduser("~"), ".audio_master_prefs.json")

    def _load_prefs(self):
        try:
            with open(self._prefs_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_prefs(self, prefs):
        try:
            with open(self._prefs_path(), "w", encoding="utf-8") as f:
                json.dump(prefs, f, ensure_ascii=False, indent=2)
        except Exception:
            traceback.print_exc()

    def _maybe_show_whats_new(self):
        """開啟時提示本版更新內容；使用者若勾選「不要再提醒我」，之後同一版本不會再彈出
        （但下次升級到新版本、有新的 WHATS_NEW_NOTES 時仍會提示）。"""
        notes = WHATS_NEW_NOTES.get(APP_VERSION)
        if not notes:
            return
        if self._load_prefs().get("whats_new_dismissed_version") == APP_VERSION:
            return
        self._show_whats_new_dialog(notes)

    def _show_whats_new_dialog(self, notes):
        dialog = ctk.CTkToplevel(self)
        dialog.title(f"Audio Master {APP_VERSION} 新功能")
        dialog.configure(fg_color=COLOR_BG)
        dialog.resizable(False, False)
        dialog.transient(self)

        ctk.CTkLabel(dialog, text=f"Audio Master {APP_VERSION} 新功能",
                     font=("Roboto", 16, "bold"), text_color="white").pack(padx=28, pady=(22, 14))

        # 更新項目一多，內容高度可能遠超過主視窗；這裡包一層「原生 Canvas＋ttk 捲軸」
        # （不能用 CTkScrollableFrame，會跟其他地方一樣有 resize 無限遞迴風險），
        # 讓超出可視高度的部分改成上下滑動閱讀，捲軸樣式跟中間檔案列表共用同一份
        # AM.Vertical.TScrollbar，視覺上一致。實際高度會在下面依主視窗高度動態夾住。
        CANVAS_W = 480
        scroll_wrap = ctk.CTkFrame(dialog, fg_color="transparent")
        scroll_wrap.pack(padx=28, pady=(0, 6))
        scroll_wrap.grid_columnconfigure(0, weight=1)
        scroll_wrap.grid_rowconfigure(0, weight=1)

        canvas = tk.Canvas(scroll_wrap, bg=COLOR_BG, highlightthickness=0,
                           width=CANVAS_W, height=10)
        canvas.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(scroll_wrap, orient="vertical",
                           style="AM.Vertical.TScrollbar", command=canvas.yview)
        sb.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        sb.grid_remove()

        body = ctk.CTkFrame(canvas, fg_color="transparent")
        body_id = canvas.create_window((0, 0), window=body, anchor="nw", width=CANVAS_W)
        for i, line in enumerate(notes):
            # 第一條當作本輪更新的headline，用品牌青色＋粗體特別標出來，其餘維持一般說明文字的淺灰。
            if i == 0:
                color, font = COLOR_CYAN, ("Roboto", 14, "bold")
            else:
                color, font = "#D1D1D6", ("Roboto", 13)
            ctk.CTkLabel(body, text=f"•  {line}", font=font, text_color=color,
                        justify="left", anchor="w", wraplength=CANVAS_W - 20).pack(fill="x", pady=4)

        dont_show_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(dialog, text="不要再提醒我", variable=dont_show_var,
                        font=("Roboto", 12), text_color=COLOR_TEXT_DIM,
                        checkmark_color="black", fg_color=COLOR_CYAN,
                        hover_color="#00C8E0").pack(anchor="w", padx=28, pady=(14, 4))

        def on_close():
            if dont_show_var.get():
                prefs = self._load_prefs()
                prefs["whats_new_dismissed_version"] = APP_VERSION
                self._save_prefs(prefs)
            dialog.destroy()

        ctk.CTkButton(dialog, text="知道了", fg_color=COLOR_CYAN, text_color="black",
                     hover_color="#00C8E0", font=("Roboto", 13, "bold"),
                     command=on_close, width=120).pack(pady=(6, 22))

        dialog.protocol("WM_DELETE_WINDOW", on_close)

        def _on_body_configure(event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
        body.bind("<Configure>", _on_body_configure)

        def _wheel(event):
            self._wheel_dbg(f"whatsnew: widget={event.widget!r} delta={getattr(event,'delta','?')} "
                            f"num={getattr(event,'num','?')}")
            d = getattr(event, "delta", 0)
            if d == 0:
                num = getattr(event, "num", 0)
                d = 1 if num == 4 else (-1 if num == 5 else 0)
            if d:
                canvas.yview_scroll(-1 if d > 0 else 1, "units")
            return "break"

        # 不逐一遞迴綁每個子元件（CTkLabel／CTkFrame 內部還包了一層 canvas，很容易漏綁）。
        # Tk 每個元件預設 bindtags 最後一定包含「所在 Toplevel 的路徑」這一個 tag，
        # 直接把滾輪綁在 dialog（這個 Toplevel 本身）上，裡面任何子元件（不管多深、
        # 不管是不是 CTk 內部實作細節）收到滾輪事件，都會轉一輪經過這個 tag、
        # 一定會呼叫到，不會有綁不到的死角。
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            dialog.bind(seq, _wheel, add="+")

        def _auto_sb(lo, hi):
            try:
                need = not (float(lo) <= 0.0 and float(hi) >= 1.0)
                sb.grid() if need else sb.grid_remove()
                sb.set(lo, hi)
            except Exception:
                pass
        canvas.configure(yscrollcommand=_auto_sb)

        # 量出所有捲動區以外的固定高度（標題、勾選框、按鈕與各處留白），
        # 再依主視窗目前高度夾住整個彈窗，超出的部分交給上面的捲軸捲動，
        # 內容本身較短時則照原樣縮回自然高度，不會多留一大塊空白。
        dialog.update_idletasks()
        fixed_h = dialog.winfo_reqheight() - canvas.winfo_reqheight()
        body_natural_h = body.winfo_reqheight()
        max_total_h = max(360, self.winfo_height() - 40)
        max_canvas_h = max(120, max_total_h - fixed_h)
        canvas.configure(height=min(body_natural_h, max_canvas_h))

        dialog.update_idletasks()
        dialog.grab_set()
        # 置中於主視窗
        x = self.winfo_x() + (self.winfo_width() - dialog.winfo_width()) // 2
        y = self.winfo_y() + (self.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _serialize_dir_tree(self, ws):
        """把左側目錄樹序列化成可存檔的節點清單（前序、含 parent 索引），
        讓多個 Import File／Import Folder 累積的結構能跨重啟保留。"""
        tree = ws.dir_tree
        if tree is None:
            return []
        nodes = []
        index_of = {}  # iid -> 在 nodes 內的索引
        def walk(parent_iid):
            for iid in tree.get_children(parent_iid):
                kind = "file" if tree.tag_has("dimfile", iid) else "folder"
                nodes.append({
                    "name": tree.item(iid, "text"),
                    "path": ws.tree_item_paths.get(iid, ""),
                    "parent": index_of.get(parent_iid, -1),
                    "kind": kind,
                })
                index_of[iid] = len(nodes) - 1
                walk(iid)
        walk("")
        return nodes

    def _restore_dir_tree(self, ws, nodes):
        """由序列化節點清單重建左側目錄樹；離線路徑保留，避免外接碟未掛載時被 autosave 洗掉。"""
        tree = ws.dir_tree
        tree.delete(*tree.get_children())
        ws.tree_item_paths.clear()
        iid_by_index = {}
        for i, n in enumerate(nodes):
            path = n.get("path", "")
            parent_iid = iid_by_index.get(n.get("parent", -1), "")
            if parent_iid is None:
                parent_iid = ""
            kind = n.get("kind")
            if kind not in ("file", "folder"):
                ext = os.path.splitext(path)[1].lower()
                kind = "file" if (os.path.isfile(path) or ext in IMPORTABLE_EXTS) else "folder"
            tag = "dimfile" if kind == "file" else "dirfolder"
            node = tree.insert(parent_iid, "end", text=n.get("name", ""), open=True, tags=(tag,))
            iid_by_index[i] = node
            if path:
                ws.tree_item_paths[node] = path
        self._refresh_dir_tree_counts(ws)

    def _schedule_autosave(self):
        """Debounce: cancel pending save and reschedule 800 ms later."""
        if self._autosave_job is not None:
            try:
                self.after_cancel(self._autosave_job)
            except Exception:
                pass
        self._autosave_job = self.after(800, self._autosave_all)

    def _is_empty_project(self):
        """目前所有工作區是否都沒有任何左側樹節點與中央音檔（＝空專案）。"""
        for ws in self.workspaces:
            if ws.audio_files:
                return False
            try:
                if ws.dir_tree is not None and ws.dir_tree.get_children(""):
                    return False
            except Exception:
                pass
        return True

    def _file_has_project_content(self, path):
        """磁碟上的存檔原本是否就有內容（樹節點或音檔）。"""
        try:
            if not path or not os.path.isfile(path):
                return False
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            for ws in d.get("workspaces", []):
                if ws.get("audio_files") or ws.get("tree_nodes"):
                    return True
        except Exception:
            pass
        return False

    def _is_workspace_empty(self, ws):
        """單一工作區是否沒有任何左側樹節點與中央音檔。"""
        if ws.audio_files:
            return False
        try:
            return not (ws.dir_tree is not None and ws.dir_tree.get_children(""))
        except Exception:
            return True

    def _autosave_all(self):
        """自動存檔：session（隨時還原用）＋ 把每個已綁定 .abproj 的工作區同步寫回它自己的檔。"""
        self._save_session()
        for ws in self.workspaces:
            p = ws.project_file_path
            if not p:
                continue
            # 安全防護：別用「空狀態」自動覆蓋掉原本有內容的 .abproj（先前存檔變空的根因）。
            if self._is_workspace_empty(ws) and self._file_has_project_content(p):
                continue
            try:
                self._write_workspace_file(p, ws)
            except Exception:
                pass

    def _save_session(self):
        self._autosave_job = None
        # 安全防護：同理，別用空狀態覆蓋掉原本有內容的 session（避免重開後整個專案不見）。
        if self._is_empty_project() and self._file_has_project_content(self._session_path()):
            return
        try:
            # 原子寫入：先寫 .tmp 再 os.replace，避免 autosave 寫到一半被中斷而留下半截
            # 損毀 JSON（下次啟動解析失敗 → 整包 session 歸零）。
            path = self._session_path()
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._project_data(), f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except Exception:
            traceback.print_exc()

    def _on_close(self):
        # 關閉前先問是否要存檔：是→存檔後關閉／否→不存檔直接關閉／取消→留在應用程式內。
        if not self._is_empty_project():
            choice = messagebox.askyesnocancel(
                "關閉應用程式",
                "要在關閉前儲存目前的工作區嗎？",
                icon="question", default="yes")
            if choice is None:
                return
            if choice:
                # Root destroy 不會觸發子 Toplevel 的 WM_DELETE_WINDOW protocol；先把仍開著的
                # Edit Window 最新 Region/Fade 寫回 entry，再儲存專案／session。
                if self._edit_window_open():
                    try:
                        self._edit_window.sync_entries()
                    except Exception:
                        traceback.print_exc()
                self._autosave_all()

        if self._device_poll_job is not None:
            try:
                self.after_cancel(self._device_poll_job)
            except Exception:
                pass
        # 關閉前停掉音訊串流，避免留下還在播放的殭屍 stream（分析執行緒為 daemon，會隨程序結束）。
        try:
            sd.stop()
        except Exception:
            pass
        self.is_playing = False
        self.destroy()

    def _populate_dir_tree_for_ws(self, ws, folder_path):
        """Rebuild the left-panel dir tree for a workspace from a folder path."""
        if not folder_path or not os.path.isdir(folder_path):
            return
        ws.current_folder = folder_path
        tree = ws.dir_tree
        tree.delete(*tree.get_children())
        ws.tree_item_paths.clear()
        self._add_folder_subtree(ws, "", folder_path)
        self._refresh_dir_tree_counts(ws)

    def _add_folder_subtree(self, ws, parent_node, folder_path):
        """在左側樹的 parent_node 底下，加入 folder_path 的子樹（遞迴走訪內容）。"""
        valid_exts = IMPORTABLE_EXTS
        tree = ws.dir_tree
        root_node = tree.insert(parent_node, "end", text=os.path.basename(folder_path) or folder_path,
                                open=True, tags=("dirfolder",))
        ws.tree_item_paths[root_node] = folder_path
        node_map = {folder_path: root_node}

        # onerror 容忍權限不足等讀取錯誤（不中斷整批匯入）；followlinks 維持預設 False，避免符號連結造成無限迴圈
        for root, dirs, files in os.walk(folder_path, onerror=lambda e: None):
            pnode = node_map.get(root)
            if not pnode:
                continue
            for d in sorted(dirs):
                dir_path = os.path.join(root, d)
                node = tree.insert(pnode, "end", text=d, tags=("dirfolder",))
                node_map[dir_path] = node
                ws.tree_item_paths[node] = dir_path
            for fname in sorted(files):
                if fname.lower().endswith(valid_exts):
                    # 資料夾內的音檔：以淺灰色呈現（像「內容預覽」），仍可拖到中央工作區
                    file_node = tree.insert(pnode, "end", text=fname, tags=("dimfile",))
                    ws.tree_item_paths[file_node] = os.path.join(root, fname)

    def _load_session(self):
        """Restore last session from disk; fall back to a blank workspace if none."""
        session_path = self._session_path()
        data = None
        if os.path.exists(session_path):
            try:
                with open(session_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                traceback.print_exc()
                data = None

        if not data or not data.get("workspaces"):
            # No saved session — create a fresh workspace
            self._add_workspace("工作區 1")
            self._switch_workspace(0)
            self._refresh_tab_buttons()
            return

        # --- Restore workspaces ---
        ws_list = data["workspaces"]
        if not isinstance(ws_list, list):
            ws_list = []
        for ws_data in ws_list:
            if not isinstance(ws_data, dict):
                continue  # 結構毀損的項目略過，不讓整個還原崩掉
            idx = self._add_workspace(ws_data.get("name", f"工作區 {len(self.workspaces) + 1}"))
            self._restore_workspace_into(self.workspaces[idx], ws_data)

        # 相容舊版 session（專案關聯是『整個視窗一個』）→ 沿用到對應的工作區：
        # 優先綁定名稱與檔名相符的工作區；否則若只有單一工作區就綁它。
        legacy_proj = data.get("project_file_path")
        if legacy_proj and os.path.isfile(legacy_proj):
            unbound = [w for w in self.workspaces if not w.project_file_path]
            base = os.path.splitext(os.path.basename(legacy_proj))[0]
            match = next((w for w in unbound if w.name == base), None)
            if match is None and len(self.workspaces) == 1 and unbound:
                match = unbound[0]
            if match is not None:
                match.project_file_path = legacy_proj

        # --- Restore export folder ---
        saved_export = data.get("export_folder", "")
        if saved_export and os.path.isdir(saved_export):
            self.export_folder = saved_export
            self._update_export_path_label()

        # 每個工作區各自的 .abproj 關聯已於 _restore_workspace_into 還原。

        # --- Switch to previously active workspace ---
        active = min(data.get("active_ws_idx", 0), len(self.workspaces) - 1)
        self._switch_workspace(active)
        self._refresh_tab_buttons()
        self.check_export_ready()

    def _rename_workspace_dialog(self, idx: int):
        new_name = simpledialog.askstring(
            "重命名工作區",
            "輸入工作區名稱:",
            initialvalue=self.workspaces[idx].name,
            parent=self
        )
        if new_name and new_name.strip():
            self.workspaces[idx].name = new_name.strip()
            self._refresh_tab_buttons()
            self._schedule_autosave()

    @staticmethod
    def _get_check(table, iid):
        """讀取 #0 勾選欄的狀態（語意值仍為 ✅/⬜ 字串，但實際顯示改用圖示，狀態存在 tags 裡）。"""
        tags = table.item(iid, "tags") or ()
        return "✅" if _CHECK_TAG_ON in tags else "⬜"

    def _set_check(self, table, iid, glyph):
        """設定 #0 勾選欄狀態：清空文字、換成勾選圖示，狀態記在 tags（chk_on/chk_off）。"""
        tags = tuple(t for t in (table.item(iid, "tags") or ()) if t not in _CHECK_TAGS)
        tags = tags + (_CHECK_TAG_ON if glyph == "✅" else _CHECK_TAG_OFF,)
        icon = self._check_icon_on if glyph == "✅" else self._check_icon_off
        table.item(iid, text="", tags=tags, image=icon)

    def _sync_folder_check(self, table, file_iid):
        """子檔變動後，讓母資料夾的勾選字反映『底下是否全勾』。"""
        parent = table.parent(file_iid)
        if parent and table.tag_has("folder", parent):
            kids = table.get_children(parent)
            all_on = bool(kids) and all(self._get_check(table, k) == "✅" for k in kids)
            self._set_check(table, parent, "✅" if all_on else "⬜")

    def _on_file_table_click(self, event):
        """點 #0 勾選欄切換勾選；點資料夾的勾選欄則一鍵切換其底下所有檔案，
        同時把該資料夾底下所有音檔選取起來（供右側批次 dB/LUFS、播放清單使用）。
        #0 同時是展開/收合箭頭所在，點到箭頭時交給 ttk 處理、不切換勾選。"""
        tree = event.widget
        item = tree.identify_row(event.y)
        if not item:
            return
        # 只處理 #0（勾選欄）；點檔名或其他資料欄不切換勾選
        if tree.identify_region(event.x, event.y) != "tree":
            return
        if "indicator" in (tree.identify_element(event.x, event.y) or ""):
            return  # 點到資料夾的展開箭頭 → 讓 ttk 自己展開/收合
        ws = next((w for w in self.workspaces if w.file_table == tree), None)

        if tree.tag_has("folder", item):
            children = tree.get_children(item)
            if not children:
                return
            any_checked = any(self._get_check(tree, c) == "✅" for c in children)
            new_val = "⬜" if any_checked else "✅"
            for c in children:
                self._set_check(tree, c, new_val)
                if ws:
                    entry = next((e for e in ws.audio_files if e["path"] == c), None)
                    if entry:
                        entry["export"] = (new_val == "✅")
            self._set_check(tree, item, new_val)
            self._schedule_autosave()
            # 點資料夾同時選取底下所有音檔（不含資料夾節點本身）；用 break 蓋掉 ttk 預設的
            # 單列選取行為，否則點完之後選取範圍會被收斂回只剩資料夾這一列。
            tree.selection_set(children)
            self.check_export_ready()
            return "break"
        else:
            new_val = "⬜" if self._get_check(tree, item) == "✅" else "✅"
            self._set_check(tree, item, new_val)
            if ws:
                entry = next((e for e in ws.audio_files if e["path"] == item), None)
                if entry:
                    entry["export"] = (new_val == "✅")
                    self._schedule_autosave()
            self._sync_folder_check(tree, item)
        self.check_export_ready()  # 勾選變動 → 匯出鈕上的就緒計數即時更新

    def _toggle_all_exports(self):
        """切換目前工作區所有檔案的匯出勾選（全選/全不選）。"""
        items = self._iter_file_iids()
        if not items:
            return
        # 若有任何一個是勾選的，就全部取消；否則全部勾選
        any_checked = any(self._get_check(self.file_table, item) == "✅" for item in items)
        new_val = "⬜" if any_checked else "✅"
        for item in items:
            self._set_check(self.file_table, item, new_val)
            entry = next((e for e in self.audio_files if e["path"] == item), None)
            if entry:
                entry["export"] = (new_val == "✅")
        for top in self.file_table.get_children(""):
            if self.file_table.tag_has("folder", top):
                self._set_check(self.file_table, top, new_val)
        self._schedule_autosave()
        self.check_export_ready()  # 全選/全不選 → 就緒計數即時更新

    def _ready_export_count(self, ws):
        """計算此工作區『實際會被匯出』的檔案數：狀態為就緒且有勾選匯出。
        匯出流程只處理 status==就緒 且 export 勾選的檔案，故計數需與其一致，
        否則會出現「勾 3 個卻顯示 12 個就緒」的不一致。"""
        return sum(1 for e in ws.audio_files
                   if e["status"] == "🟢 就緒" and e.get("export", True))

    # ================= 專案功能方法 =================

    def get_selected_device(self):
        dev = self.device_menu.get()
        return None if dev == "System Default" else dev

    def _on_preset_changed(self, preset):
        """公版格式選單：獨立於輸出格式選單之外，選到一個公版就把格式／取樣率／
        位元率(深度)一次套成該公版內容，套用完立刻把選單跳回 PRESET_PLACEHOLDER
        （公版是一次性套用的動作，不是持續套用的模式，見 PRESET_OPTIONS 的說明）。"""
        if preset not in PRESET_PROFILES:
            return
        profile = PRESET_PROFILES[preset]
        self.format_menu.set(profile["format"])
        self._on_format_changed(profile["format"])
        self.sr_menu.set(profile["sr"])
        self.bit_menu.set(profile["bit"])
        self.preset_menu.set(PRESET_PLACEHOLDER)

    def _on_format_changed(self, fmt):
        """格式切換時，動態切換位元率／位元深度選單：
        有損格式(mp3/aac/ogg/wma/opus/m4a) → 位元率(kbps)清單；
        無損格式(wav/aif/aiff/flac) → 位元深度(16/24/32-bit)清單；
        Original(不轉檔，維持每個檔案原本格式) → 跟隨來源檔案本身，但選單仍可直接點選。"""
        key = fmt.lower()
        if key in LOSSY_FORMATS:
            self.lbl_bit_menu.configure(text="位元率:")
            self.bit_menu.configure(values=BITRATES, state="normal")
            if self.bit_menu.get() not in BITRATES:
                self.bit_menu.set("128")
        elif key in LOSSLESS_FORMATS:
            self.lbl_bit_menu.configure(text="位元深度:")
            self.bit_menu.configure(values=BIT_DEPTHS, state="normal")
            if self.bit_menu.get() not in BIT_DEPTHS:
                self.bit_menu.set("Original")
        else:
            self.lbl_bit_menu.configure(text="位元率:")
            self.bit_menu.configure(values=BITRATES, state="normal")
            self.bit_menu.set("Original")

    def _enqueue_ui(self, fn, *args):
        """供背景執行緒呼叫：把一個 UI 更新動作排進佇列，交由主執行緒執行。"""
        self._ui_queue.put((fn, args))

    def _poll_ui_queue(self):
        """主執行緒每 100ms 輪詢一次，執行背景執行緒排入的 UI 更新動作。"""
        try:
            while True:
                fn, args = self._ui_queue.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    traceback.print_exc()
        except queue.Empty:
            pass
        finally:
            self.after(100, self._poll_ui_queue)

    def _poll_audio_devices(self):
        """每 2 秒檢查一次裝置清單，有變動時自動更新下拉選單（已去除重複）。"""
        try:
            _seen: set = set()
            current = []
            for _d in sd.query_devices():
                if _d['max_output_channels'] > 0 and _d['name'] not in _seen:
                    _seen.add(_d['name'])
                    current.append(_d['name'])
        except Exception:
            # 暫時查詢失敗 → 維持現有清單與選取，別誤判成「所有裝置都被拔除」而重設選取
            self._device_poll_job = self.after(2000, self._poll_audio_devices)
            return

        existing = list(self.device_menu.cget("values"))
        # 過濾掉 "System Default" 再比較真實裝置
        existing_real = [v for v in existing if v != "System Default"]

        if sorted(current) != sorted(existing_real):
            selected = self.device_menu.get()
            new_values = ["System Default"] + current if current else ["System Default"]
            self.device_menu.configure(values=new_values)
            # 保留原本選擇，若裝置已拔除則回到 System Default
            self.device_menu.set(selected if selected in new_values else "System Default")

        self._device_poll_job = self.after(2000, self._poll_audio_devices)

    def apply_soft_clipper(self, samples_float32):
        return np.tanh(samples_float32)

    def _measure_true_peak_db(self, samples_float, oversample=4):
        """以分塊多相 FIR 超取樣估算 True Peak，避免建立整檔 4× 暫存陣列。"""
        if samples_float is None:
            return -100.0

        samples = np.asarray(samples_float)
        if samples.size == 0:
            return -100.0
        if samples.ndim == 1:
            samples = samples[:, np.newaxis]
        elif samples.ndim != 2:
            raise ValueError("samples_float 必須是 (frames,) 或 (frames, channels)")

        oversample = max(1, int(oversample))
        n_frames, n_channels = samples.shape
        if n_frames < 2 or oversample == 1:
            peak = float(np.max(np.abs(samples)))
            return 20.0 * math.log10(max(peak, 1e-10))

        peak = 0.0
        for channel in range(n_channels):
            for start in range(0, n_frames, _TRUE_PEAK_CHUNK_FRAMES):
                end = min(n_frames, start + _TRUE_PEAK_CHUNK_FRAMES)
                read_start = max(0, start - _TRUE_PEAK_OVERLAP_FRAMES)
                read_end = min(n_frames, end + _TRUE_PEAK_OVERLAP_FRAMES)

                # 只複製目前區塊並保持 float32；峰值記憶體不隨整檔長度增加。
                block = np.asarray(
                    samples[read_start:read_end, channel],
                    dtype=np.float32,
                )
                if block.size:
                    peak = max(
                        peak,
                        abs(float(np.min(block))),
                        abs(float(np.max(block))),
                    )

                upsampled = resample_poly(
                    block,
                    oversample,
                    1,
                    padtype="constant",
                )
                # overlap 只供 FIR 邊界計算；每個 chunk 僅量中央有效區域。
                core_start = (start - read_start) * oversample
                core_end = core_start + (end - start) * oversample
                core = upsampled[core_start:core_end]
                if core.size:
                    peak = max(peak, float(np.max(np.abs(core))))

        return 20.0 * math.log10(max(peak, 1e-10))

    # ─────────────────────────────────────────────────────────
    # Edit Window 非破壞性編輯：region 渲染（預覽播放、匯出共用）
    # ─────────────────────────────────────────────────────────

    def _decode_source_samples(self, path, cache):
        """回傳 (float32 樣本陣列, sr, channels)，-1.0~1.0。cache 是呼叫端自備的 dict，
        同一次渲染裡多個 region 指到同一個來源檔時只解碼一次。優先重用已經在中央清單
        載入的 AudioSegment（entry['audio']），沒有才直接從磁碟讀。"""
        cached = cache.get(path)
        if cached is not None:
            return cached
        audio = None
        for e in self.audio_files:
            if e["path"] == path and e.get("audio") is not None:
                audio = e["audio"]
                break
        if audio is None:
            try:
                audio = AudioSegment.from_file(path)
            except Exception:
                cache[path] = (np.zeros(0, dtype=np.float32), 44100, 1)
                return cache[path]
        samples = np.array(audio.get_array_of_samples())
        channels = audio.channels
        if channels > 1:
            samples = samples.reshape((-1, channels))
        max_val = float(2 ** (8 * audio.sample_width - 1))
        samples = samples.astype(np.float32) / max_val
        result = (samples, audio.frame_rate, channels)
        cache[path] = result
        return result

    def _render_region_list(self, regions, out_sr, out_channels, cache=None):
        """把一串 EditRegion 依各自的 track_offset 疊回一條 float32 陣列（0~1 正規化，
        非破壞性：來源樣本本身不變）。region 之間若重疊直接相加。"""
        if cache is None:
            cache = {}
        total_dur = max((r.track_offset + r.playback_length for r in regions), default=0.0)
        total_len = max(1, int(round(total_dur * out_sr)))
        shape = (total_len, out_channels) if out_channels > 1 else (total_len,)
        out = np.zeros(shape, dtype=np.float64)

        for r in regions:
            if r.length <= 0:
                continue
            samples, sr, ch = self._decode_source_samples(r.source_path, cache)
            if samples.size == 0:
                continue
            s_idx = max(0, int(round(r.src_start * sr)))
            e_idx = min(len(samples), int(round(r.src_end * sr)))
            if e_idx <= s_idx:
                continue
            seg = samples[s_idx:e_idx].astype(np.float64)

            # 聲道數不同 → 簡單擴/縮混成目標聲道數
            if ch != out_channels:
                if ch > 1:
                    mono = seg.mean(axis=1)
                else:
                    mono = seg
                if out_channels > 1:
                    seg = np.repeat(mono[:, None], out_channels, axis=1)
                else:
                    seg = mono

            # Flex Time／Flex Pitch：都用同一顆 phase vocoder（見 _flex_time_stretch／
            # _flex_pitch_shift），在原始取樣率 sr 下處理，之後再照原本流程重取樣成 out_sr。
            if abs(r.pitch_semitones) > 1e-6:
                seg = _flex_pitch_shift(seg, r.pitch_semitones)
            if abs(r.time_stretch_ratio - 1.0) > 1e-6:
                seg = _flex_time_stretch(seg, r.time_stretch_ratio)

            # 取樣率不同 → 線性內插重新取樣，讓貼到別的檔案軌道時長度/音高正確
            if sr != out_sr and len(seg) > 1:
                src_n = len(seg)
                dst_n = max(1, int(round(src_n * out_sr / sr)))
                x_src = np.linspace(0.0, 1.0, src_n)
                x_dst = np.linspace(0.0, 1.0, dst_n)
                if seg.ndim > 1:
                    seg = np.stack([np.interp(x_dst, x_src, seg[:, c]) for c in range(seg.shape[1])], axis=1)
                else:
                    seg = np.interp(x_dst, x_src, seg)

            n = len(seg)
            if r.fade_in > 0:
                fi = min(n, int(round(r.fade_in * out_sr)))
                if fi > 0:
                    ramp = _fade_curve_gain(
                        np.linspace(0.0, 1.0, fi),
                        r.fade_in_curve,
                    )
                    seg[:fi] = seg[:fi] * (ramp[:, None] if seg.ndim > 1 else ramp)
            if r.fade_out > 0:
                fo = min(n, int(round(r.fade_out * out_sr)))
                if fo > 0:
                    ramp = _fade_curve_gain(
                        np.linspace(1.0, 0.0, fo),
                        r.fade_out_curve,
                    )
                    seg[-fo:] = seg[-fo:] * (ramp[:, None] if seg.ndim > 1 else ramp)

            # Automation：Region 中間的音量自動化節點，套在 Fade 之上（兩者都只是逐取樣
            # 相乘的增益曲線，疊加順序不影響結果）。
            env = _envelope_gain_curve(r.gain_nodes, n, out_sr)
            if env is not None:
                seg = seg * (env[:, None] if seg.ndim > 1 else env)

            off_idx = max(0, int(round(r.track_offset * out_sr)))
            end_write = min(total_len, off_idx + n)
            write_n = end_write - off_idx
            if write_n > 0:
                out[off_idx:end_write] += seg[:write_n]

        return np.clip(out, -1.0, 1.0).astype(np.float32)

    def _entry_edit_regions(self, entry):
        """把 entry['edit_regions']（存檔用的 dict 列表）還原成 EditRegion 物件列表；
        None 代表從未編輯；空 list 代表使用者已明確刪光，兩者不可混為一談。"""
        saved = entry.get("edit_regions")
        if saved is None:
            return None
        if not isinstance(saved, list):
            return None
        try:
            regions = [EditRegion.from_dict(d) for d in saved]
        except Exception:
            return None
        # 只有一段、完全對應原始檔頭到尾、沒有淡入淡出／Flex／Automation → 等同沒編輯過，不必渲染。
        if len(regions) == 1:
            r = regions[0]
            audio = entry.get("audio")
            dur = audio.duration_seconds if audio is not None else None
            if (r.source_path == entry["path"] and abs(r.src_start) < 1e-6
                    and abs(r.track_offset) < 1e-6 and r.fade_in <= 0 and r.fade_out <= 0
                    and abs(r.time_stretch_ratio - 1.0) < 1e-6 and abs(r.pitch_semitones) < 1e-6
                    and not r.gain_nodes
                    and dur is not None and abs(r.src_end - dur) < 1e-6):
                return None
        return regions

    def _entry_duration_label(self, entry, source_duration=None):
        """依目前 Regions 產生列表時長；未編輯或恢復完整原音時使用來源檔長度。"""
        regions = self._entry_edit_regions(entry)
        if regions is None:
            if source_duration is None:
                audio = entry.get("audio")
                source_duration = audio.duration_seconds if audio is not None else None
            if source_duration is None:
                return entry.get("duration", "--:--")
            duration = max(0.0, float(source_duration))
        else:
            duration = max(
                (region.track_offset + region.length for region in regions),
                default=0.0,
            )
        mins, secs = divmod(int(duration), 60)
        return f"{mins:02d}:{secs:02d}"

    def _render_edited_audio(self, entry):
        """若這個檔案在 Edit Window 裡有非破壞性編輯，依 edit_regions 重新組出一份新的
        AudioSegment；沒有編輯記錄時直接回傳原始 entry['audio']，行為與編輯前完全一樣。

        這份『渲染後的結果』就是主畫面（波形／播放／時長）跟匯出共用的同一份資料——
        Edit Window 剪輯完、sync_entries() 把新的 edit_regions 寫回 entry 之後，主畫面
        看到、聽到的就會是編輯後的樣子，不必等到真正匯出才生效。entry['audio'] 本身
        永遠維持匯入時的原始音訊不變，只作為 region 的解碼來源（_decode_source_samples），
        這樣其他軌貼上/複製這個檔案片段時，來源座標才不會因為這裡被『烤』過而跑掉。

        用 base_audio／edit_regions 物件本身的參照身分（is）當快取鍵：sync_entries() 每次
        都會指派一份新的 edit_regions list，物件一換就自動重渲染；沒編輯過或物件都沒變時
        直接回傳快取，避免主畫面每次選取/重畫都重新算一次 PCM。"""
        base_audio = entry["audio"]
        regions = self._entry_edit_regions(entry)
        if regions is None:
            entry.pop("_rendered_audio_cache", None)
            return base_audio
        regions_obj = entry.get("edit_regions")
        cached = entry.get("_rendered_audio_cache")
        if cached is not None and cached[0] is base_audio and cached[1] is regions_obj:
            return cached[2]
        rendered = self._render_region_list(regions, base_audio.frame_rate, base_audio.channels)
        max_val = float(2 ** (8 * base_audio.sample_width - 1))
        int_dtype = np.array(base_audio.get_array_of_samples()).dtype
        rendered_int = np.clip(np.rint(rendered * max_val), -max_val, max_val - 1).astype(int_dtype)
        result = base_audio._spawn(rendered_int.tobytes())
        # 存住 regions_obj 本身（不只是 id）：只存 id 的話，舊 list 被 GC 後 id 可能被
        # 別的物件重用，造成誤判快取命中；抓著物件參照就不會有這個問題。
        entry["_rendered_audio_cache"] = (base_audio, regions_obj, result)
        return result

    def suggest_target_lufs(self, filename):
        name = filename.lower().replace("sound_", "").replace(".wav", "").replace("_", "")

        priority_targets = [
            ("transitionbgm", -22.0),
            ("freebgm", -14.0),
            ("basebgm", -21.0),
            ("freescoring", -16.5),
            ("freeresult", -13.0),
            ("freestart", -12.0),
            ("featurewin", -8.0),
            ("featurelock", -19.0),
            ("bonusretrigger", -8.0),
            ("spinstop", -30.0),
            ("bigwin", -14.0),
            ("megawin", -12.0),
            ("jumbowin", -10.0),
            ("scatter", -12.0),
            ("bgm", -21.0),
            ("scoring", -21.0),
            ("result", -13.0),
            ("transition", -22.0),
            ("start", -12.0),
            ("lock", -19.0),
        ]

        for key, val in priority_targets:
            if key in name:
                return val
        return -16.0

    def select_prev_file(self, event=None):
        items = self._iter_file_iids()
        if not items: return
        sel = self.file_table.selection()
        if not sel or sel[0] not in items:
            self.file_table.selection_set(items[-1])
            self.file_table.see(items[-1])
            self.on_table_select(None)
        else:
            idx = items.index(sel[0])
            if idx > 0:
                self.file_table.selection_set(items[idx - 1])
                self.file_table.see(items[idx - 1])
                self.on_table_select(None)

    def select_next_file(self, event=None):
        items = self._iter_file_iids()
        if not items: return
        sel = self.file_table.selection()
        if not sel or sel[0] not in items:
            self.file_table.selection_set(items[0])
            self.file_table.see(items[0])
            self.on_table_select(None)
        else:
            idx = items.index(sel[0])
            if idx < len(items) - 1:
                self.file_table.selection_set(items[idx + 1])
                self.file_table.see(items[idx + 1])
                self.on_table_select(None)

    def reset_peaks(self):
        self.max_peak_L = -100.0
        self.max_peak_R = -100.0
        self.lbl_peak_L.configure(text="--", text_color=COLOR_CYAN)
        self.lbl_peak_R.configure(text="--", text_color=COLOR_CYAN)

    # ================= UI 邏輯與功能 =================

    def _do_import_folder(self):
        """Import Folder：可『一次複選多個資料夾』，全部加入左側結構（保留現有內容）。"""
        folders = self._choose_folders_multi()
        if not folders:
            return
        ws = self.workspaces[self.active_ws_idx]
        for folder_path in folders:
            self._add_folder_to_dir_tree(ws, folder_path)
        self._refresh_dir_tree_counts(ws)
        self._schedule_autosave()

    def _choose_folders_multi(self):
        """開啟可『複選』的資料夾選取器，回傳資料夾路徑清單。
        macOS 原生 tkinter 的 askdirectory 無法複選 → 改用 osascript 的
        『choose folder ... with multiple selections allowed』；失敗時退回單選。"""
        if sys.platform == "darwin":
            script = (
                'set theFolders to choose folder with prompt '
                '"選擇要匯入的資料夾（可按 ⌘ / ⇧ 複選）" with multiple selections allowed\n'
                'set out to ""\n'
                'repeat with f in theFolders\n'
                '    set out to out & POSIX path of f & linefeed\n'
                'end repeat\n'
                'return out'
            )
            try:
                res = subprocess.run(["osascript", "-e", script],
                                     capture_output=True, text=True, timeout=600)
                if res.returncode == 0:
                    return [os.path.normpath(p) for p in res.stdout.splitlines() if p.strip()]
                return []  # returncode != 0 多半是使用者按取消
            except Exception:
                pass
        # 後備：非 macOS 或 osascript 失敗 → 單選
        folder_path = filedialog.askdirectory(title="選擇要匯入的資料夾")
        return [folder_path] if folder_path else []

    # ── 左側目錄樹：資料夾檔案數量 / 欄寬自適應 helpers ──────────────
    def _folder_base_name(self, ws, iid):
        """資料夾節點的『原始名稱』（去掉已加上的「 (數量)」後綴）。"""
        path = ws.tree_item_paths.get(iid, "")
        if path:
            return os.path.basename(os.path.normpath(path)) or path
        text = ws.dir_tree.item(iid, "text")
        if text.endswith(")") and "(" in text:
            head, _, tail = text.rpartition("(")
            if tail[:-1].strip().isdigit():
                return head.rstrip()
        return text

    def _refresh_dir_tree_counts(self, ws):
        """在每個『資料夾節點』名稱後標上其底下的音檔數量，例如 BaseGame  (12)。"""
        tree = ws.dir_tree
        if tree is None:
            return

        def walk(iid):
            children = tree.get_children(iid)
            path = ws.tree_item_paths.get(iid, "")
            if not children:
                if iid != "" and tree.tag_has("dimfile", iid):
                    return 1  # 葉節點音檔
                if iid != "" and tree.tag_has("dirfolder", iid):
                    base = self._folder_base_name(ws, iid)   # 空資料夾 → (0)
                    tree.item(iid, text=f"{base}  (0)")
                return 0
            count = sum(walk(c) for c in children)
            if iid != "":
                base = self._folder_base_name(ws, iid)
                tree.item(iid, text=f"{base}  ({count})")
            return count

        walk("")
        self._fit_dir_tree_column(ws)

    def _fit_dir_tree_column(self, ws):
        """讓 #0 欄寬剛好容納最長的項目名稱：名稱沒被切到時不需橫向捲軸；
        名稱過長時欄寬超過可視範圍 → 橫向捲軸才會自動出現。"""
        tree = ws.dir_tree
        if tree is None:
            return
        try:
            f = tkfont.Font(font=("Roboto", 13))
        except Exception:
            return
        maxw = [0]

        def walk(iid, depth):
            for c in tree.get_children(iid):
                w = f.measure(tree.item(c, "text")) + depth * 20 + 44
                if w > maxw[0]:
                    maxw[0] = w
                walk(c, depth + 1)

        walk("", 1)
        ws._tree_content_w = max(120, maxw[0])
        self._apply_tree_column_width(ws)

    _LEFT_COLLAPSE_W = 28     # 收合後只留一條可點的細條（含展開箭頭）
    _LEFT_SNAP_THRESHOLD = 70  # 拖曳 sash 小於這個寬度時，直接吸附收合成細條

    def _toggle_left_panel(self):
        """收合／展開左側資料夾樹：收合時只留一條細條＋展開箭頭，不整個拿掉分頁，
        才能保留一個固定可點的地方再展開。"""
        if self._left_collapsed:
            self._main_paned.paneconfigure(self.left_panel, width=self._left_panel_width, minsize=28)
            self.lbl_left_panel_title.grid()
            self.left_content_container.grid()
            self.btn_left_collapse.configure(text="‹")
            self._left_collapsed = False
        else:
            cur_w = self.left_panel.winfo_width()
            if cur_w > self._LEFT_SNAP_THRESHOLD:
                self._left_panel_width = cur_w
            self._main_paned.paneconfigure(self.left_panel, width=self._LEFT_COLLAPSE_W, minsize=self._LEFT_COLLAPSE_W)
            self.lbl_left_panel_title.grid_remove()
            self.left_content_container.grid_remove()
            self.btn_left_collapse.configure(text="›")
            self._left_collapsed = True

    def _snap_collapse_on_sash_release(self, event=None):
        """把中間工作區往左拖到底（即左側樹狀圖被拖到很窄）時，直接吸附收合成細條，
        而不是卡在一個尷尬的極窄寬度看不清楚內容。放開滑鼠後才檢查，不影響拖曳中的手感。"""
        if self._left_collapsed:
            return
        try:
            cur_w = self.left_panel.winfo_width()
        except Exception:
            return
        if 0 < cur_w <= self._LEFT_SNAP_THRESHOLD:
            self._toggle_left_panel()

    def _apply_tree_column_width(self, ws):
        """#0 欄寬 = max(可視寬, 內容寬)：
        面板夠寬 → 欄寬=可視寬（填滿、不留白、不出現橫向捲軸）；
        面板被拉窄到吃到字 → 欄寬維持內容寬 → 橫向捲軸自動出現。"""
        tree = getattr(ws, "dir_tree", None)
        if tree is None:
            return
        if getattr(self, "_layout_settling", False):
            return  # 版面切換凍結期：不改欄寬，避免與其他幾何回饋互觸成迴圈
        try:
            view_w = tree.winfo_width()
            if view_w <= 1:
                view_w = 200
            content_w = getattr(ws, "_tree_content_w", 0) or view_w
            new_w = view_w if content_w <= view_w else content_w
            if abs(tree.column("#0", "width") - new_w) > 2:
                tree.column("#0", width=new_w, stretch=False)
        except Exception:
            pass

    def _add_folder_to_dir_tree(self, ws, folder_path):
        """把資料夾整包加入左側樹（保留現有內容；同一資料夾不重複加入）。"""
        if not folder_path or not os.path.isdir(folder_path):
            return
        existing_roots = {ws.tree_item_paths.get(iid) for iid in ws.dir_tree.get_children("")}
        if folder_path in existing_roots:
            return  # 已匯入過同一資料夾，避免重複
        self._add_folder_subtree(ws, "", folder_path)
        if not ws.current_folder:
            ws.current_folder = folder_path

    def _do_import_files(self):
        """Import File：選一個或多個音檔，加入左側欄位（依母資料夾分組、不清掉現有內容）。"""
        paths = filedialog.askopenfilenames(
            title="選擇要匯入的音檔",
            filetypes=[("音訊檔", "*.wav *.mp3 *.flac *.aiff *.aif *.ogg *.m4a *.opus *.wma *.aac"),
                       ("所有檔案", "*.*")],
        )
        if not paths:
            return
        ws = self.workspaces[self.active_ws_idx]
        self._add_files_to_dir_tree(ws, list(paths))
        self._schedule_autosave()

    def _add_files_to_dir_tree(self, ws, paths):
        """把選取的音檔加入左側目錄樹：依母資料夾分組、去重複、保留現有內容。"""
        valid_exts = IMPORTABLE_EXTS
        files = [p for p in paths if os.path.isfile(p) and p.lower().endswith(valid_exts)]
        if not files:
            return
        tree = ws.dir_tree
        existing_paths = set(ws.tree_item_paths.values())
        # 既有的「母資料夾節點」：目錄路徑 -> node iid（供同資料夾的散檔掛在同一節點下）
        folder_nodes = {p: iid for iid, p in ws.tree_item_paths.items()
                        if tree.exists(iid) and os.path.isdir(p)}
        for fpath in files:
            if fpath in existing_paths:
                continue
            parent = os.path.dirname(fpath)
            node = folder_nodes.get(parent)
            if node is None:
                node = tree.insert("", "end", text=os.path.basename(parent) or parent,
                                   open=True, tags=("dirfolder",))
                ws.tree_item_paths[node] = parent
                folder_nodes[parent] = node
            fnode = tree.insert(node, "end", text=os.path.basename(fpath), tags=("dimfile",))
            ws.tree_item_paths[fnode] = fpath
            existing_paths.add(fpath)
        if not ws.current_folder:
            ws.current_folder = os.path.dirname(files[0])
        self._refresh_dir_tree_counts(ws)

    def _iter_tree_descendants(self, tree, iid):
        """回傳某節點底下所有子孫節點的 iid（深度優先）。"""
        out = []
        for child in tree.get_children(iid):
            out.append(child)
            out.extend(self._iter_tree_descendants(tree, child))
        return out

    def _remove_tree_selection(self, ws):
        """從左側目錄樹移除選取的節點（含其所有子節點），並清掉對應的 path 記錄。"""
        tree = ws.dir_tree
        sel = list(tree.selection())
        if not sel:
            return
        # 移除多個節點或整包資料夾（含其所有子項）先確認，避免誤按 Delete 整批消失
        if len(sel) > 1 or any(tree.tag_has("dirfolder", iid) for iid in sel):
            if not messagebox.askyesno(
                    "從清單移除",
                    f"確定要從左側清單移除選取的 {len(sel)} 個項目（含其底下所有內容）？\n"
                    "（不會刪除磁碟上的原始檔案）",
                    icon="warning", default="no", parent=self):
                return
        for iid in sel:
            if not tree.exists(iid):
                continue
            for sub in self._iter_tree_descendants(tree, iid):
                ws.tree_item_paths.pop(sub, None)
            ws.tree_item_paths.pop(iid, None)
            tree.delete(iid)
        self._refresh_dir_tree_counts(ws)
        self._schedule_autosave()

    def _show_tree_context_menu(self, event, ws):
        """左側樹右鍵選單：移除選取項目。"""
        tree = ws.dir_tree
        row = tree.identify_row(event.y)
        if row and row not in tree.selection():
            tree.selection_set(row)
        sel = tree.selection()
        if not sel:
            return
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=f"✕  從清單移除（{len(sel)}）",
                         command=lambda: self._remove_tree_selection(ws))
        menu.post(event.x_root, event.y_root)

    def _populate_dir_tree_mixed(self, ws, paths):
        """用選取的資料夾與／或檔案重建左側目錄樹。
        資料夾 → 走訪其內容成子樹；散檔 → 依母資料夾分組為根節點。
        """
        valid_exts = IMPORTABLE_EXTS
        folders = [p for p in paths if os.path.isdir(p)]
        files = [p for p in paths if os.path.isfile(p) and p.lower().endswith(valid_exts)]
        if not folders and not files:
            return

        tree = ws.dir_tree
        tree.delete(*tree.get_children())
        ws.tree_item_paths.clear()

        # 1) 選取的資料夾各自成一棵根子樹
        for folder_path in folders:
            self._add_folder_subtree(ws, "", folder_path)

        # 2) 選取的散檔依母資料夾分組
        folder_nodes = {}
        for fpath in files:
            parent = os.path.dirname(fpath)
            if parent not in folder_nodes:
                node = tree.insert("", "end", text=os.path.basename(parent) or parent,
                                   open=True, tags=("dirfolder",))
                ws.tree_item_paths[node] = parent
                folder_nodes[parent] = node
            fnode = tree.insert(folder_nodes[parent], "end", text=os.path.basename(fpath), tags=("dimfile",))
            ws.tree_item_paths[fnode] = fpath

        # 3) 設定 current_folder 供 session 還原
        if folders:
            ws.current_folder = folders[0]
        elif files:
            ws.current_folder = os.path.dirname(files[0])
        self._refresh_dir_tree_counts(ws)

    def on_tree_drag_start(self, event):
        # 找到觸發事件的實際 dir_tree widget
        source_tree = event.widget
        item = source_tree.identify_row(event.y)
        if item:
            selected = source_tree.selection()
            if item not in selected:
                source_tree.selection_set(item)

            self.drag_items = []
            # 找到這個 tree 對應的 workspace
            ws = next((w for w in self.workspaces if w.dir_tree == source_tree), None)
            if ws is None:
                return
            for sel_item in source_tree.selection():
                path = ws.tree_item_paths.get(sel_item)
                # 同時支援單檔與資料夾
                if path and (os.path.isfile(path) or os.path.isdir(path)):
                    self.drag_items.append((sel_item, path))

            if self.drag_items:
                count = len(self.drag_items)
                first_path = self.drag_items[0][1]
                if os.path.isdir(first_path):
                    name = os.path.basename(first_path) + "/"
                else:
                    name = os.path.basename(first_path)
                self.drag_label_text = f"{name}" if count == 1 else f"{count} 個項目"

                if hasattr(self, 'drag_label') and self.drag_label:
                    self.drag_label.destroy()
                self.drag_label = tk.Label(self, text=self.drag_label_text,
                                           bg="#00E5FF", fg="black",
                                           font=("Arial", 11, "bold"),
                                           padx=8, pady=4, relief="flat")

    def on_tree_drag_motion(self, event):
        if hasattr(self, 'drag_label') and self.drag_label:
            x = event.x_root - self.winfo_rootx() + 12
            y = event.y_root - self.winfo_rooty() + 12
            self.drag_label.place(x=x, y=y)

    def on_tree_drag_release(self, event):
        if hasattr(self, 'drag_label') and self.drag_label:
            self.drag_label.destroy()
            self.drag_label = None

        if not hasattr(self, 'drag_items') or not self.drag_items:
            return

        x = self.file_table.winfo_rootx()
        y = self.file_table.winfo_rooty()
        w = self.file_table.winfo_width()
        h = self.file_table.winfo_height()

        if x <= event.x_root <= x + w and y <= event.y_root <= y + h:
            AUDIO_EXTS = IMPORTABLE_EXTS
            existing_paths = {f["path"] for f in self.audio_files}
            for _, full_path in self.drag_items:
                if os.path.isfile(full_path):
                    if full_path not in existing_paths:
                        self.add_file_to_table(full_path)
                        existing_paths.add(full_path)
                elif os.path.isdir(full_path):
                    # 遞迴帶入子資料夾的音檔（與左樹計數一致，不再只抓最上層、漏掉巢狀內容）
                    for root, _dirs, files in os.walk(full_path, onerror=lambda e: None):
                        for fname in sorted(files):
                            fpath = os.path.join(root, fname)
                            if os.path.isfile(fpath) and fname.lower().endswith(AUDIO_EXTS):
                                if fpath not in existing_paths:
                                    self.add_file_to_table(fpath)
                                    existing_paths.add(fpath)

        self.drag_items = []

    # ── 中央工作區：母資料夾分組樹 helpers ─────────────────────────
    def _ensure_folder_node(self, table, file_path):
        """回傳 file_path 所屬「母資料夾」分組節點的 iid，必要時建立之。"""
        folder_path = os.path.dirname(file_path)
        folder_iid = f"__folder__::{folder_path}"
        if not table.exists(folder_iid):
            folder_name = os.path.basename(folder_path) or folder_path or "（根目錄）"
            # #0 = 勾選欄（資料夾預設勾選，點一下切換其底下全部）；檔名放「檔案」欄
            table.insert("", "end", iid=folder_iid,
                         values=(f"📁 {folder_name}", "", "", "", "", "", ""), tags=("folder",), open=True)
            self._set_check(table, folder_iid, "✅")
        return folder_iid

    def _insert_file_row_into(self, table, file_path, export_val, dur, status, lufs_display, target_display,
                              orig_tp_display="--", target_tp_display="--"):
        """把單一檔案列插入對應母資料夾節點底下（tree headings 階層結構）。
        #0 樹欄當勾選欄（圖示呈現，狀態存在 tags），檔名放在緊接其後的「檔案」欄。"""
        folder_iid = self._ensure_folder_node(table, file_path)
        if table.exists(file_path):
            return  # 已存在則略過，避免重複
        table.insert(folder_iid, "end", iid=file_path,
                     values=(os.path.basename(file_path), dur, status, lufs_display, orig_tp_display,
                             target_display, target_tp_display),
                     tags=("file",))
        self._set_check(table, file_path, "✅" if export_val else "⬜")
        self._refresh_folder_row_count(table, folder_iid)

    def _iter_file_iids(self, table=None):
        """攤平母資料夾分組，回傳所有「檔案」節點 iid（略過資料夾節點）。"""
        table = table or self.file_table
        result = []
        for top in table.get_children(""):
            if table.tag_has("folder", top):
                result.extend(table.get_children(top))
            else:
                result.append(top)
        return result

    def _prune_empty_folder_nodes(self, table=None):
        """移除底下已無檔案的母資料夾分組節點；還有檔案的資料夾順便刷新音檔數量。"""
        table = table or self.file_table
        for top in list(table.get_children("")):
            if not table.tag_has("folder", top):
                continue
            if table.get_children(top):
                self._refresh_folder_row_count(table, top)
            else:
                table.delete(top)

    def _refresh_folder_row_count(self, table, folder_iid):
        """更新中央工作區母資料夾分組列的顯示文字，帶上底下目前的音檔數量，例如「📁 BaseGame  (12)」。"""
        if not table.exists(folder_iid):
            return
        n = len(table.get_children(folder_iid))
        folder_path = folder_iid[len("__folder__::"):]
        folder_name = os.path.basename(folder_path) or folder_path or "（根目錄）"
        table.set(folder_iid, "檔案", f"📁 {folder_name}  ({n})")

    def add_file_to_table(self, file_path):
        # 資料層去重：同一路徑已在此工作區 → 略過。表格層本來就會擋重複列（_insert_file_row_into），
        # 但 audio_files 若進了重複 entry，就緒計數會多算、匯出會輸出兩份（Finder 重複拖入就會踩到）。
        if any(f["path"] == file_path for f in self.audio_files):
            return
        fname = os.path.basename(file_path)
        entry = {"name": fname, "path": file_path, "duration": "--:--", "status": "🟡 載入中",
                 "lufs": "--", "target_lufs": None, "audio": None, "export": True,
                 "source_bit_depth": None, "true_peak": None,
                 "_table": self.file_table}
        self.audio_files.append(entry)
        # 依「母資料夾」自動分組顯示（上方可展開／收合）
        self._insert_file_row_into(self.file_table, file_path, True,
                                   entry["duration"], entry["status"], entry["lufs"], "--")
        threading.Thread(target=self.analyze_single_file, args=(entry,), daemon=True).start()
        self._update_empty_hint()
        self.check_export_ready()
        self._schedule_autosave()

    def _focus_in_text_entry(self):
        """目前鍵盤焦點是否落在任何文字輸入框內。

        customtkinter 的 CTkEntry 內層是 tkinter.Entry，focus_get() 會回傳內層的
        tk.Entry，因此兩種型別都要判斷；否則在右側參數欄（LUFS、批次 ±Gain、
        資料夾名稱…）打字時，全域快捷鍵會誤觸到中間工作區的操作。
        """
        return isinstance(self.focus_get(), (ctk.CTkEntry, tk.Entry))

    def _is_frontmost(self):
        """目前鍵盤焦點是否在主視窗（而不是 Edit Window 之類的其他 Toplevel）。
        bind_all 是全域保險，沒有這層判斷的話 Edit Window 開著時，主視窗的全域快捷鍵
        會跟 Edit Window 自己的快捷鍵一起被誤觸。"""
        try:
            focused = self.focus_displayof()
            return focused is not None and focused.winfo_toplevel() == self
        except Exception:
            return False

    def _edit_window_shortcut_active(self):
        """Cmd+4 只在 Audio Master 主視窗或目前的 Edit Window 取得焦點時生效。"""
        try:
            focused = self.focus_displayof()
            if focused is None:
                return False
            top = focused.winfo_toplevel()
            if top == self:
                return True
            return self._edit_window_open() and top == self._edit_window.win
        except Exception:
            return False

    def _handle_main_navigation_shortcut(self, action):
        """主視窗的播放/方向鍵共用入口；有執行動作就回傳 break，避免 bind_all 再做一次。"""
        if self._focus_in_text_entry():
            return None
        if action == "space":
            if self._focus_blocks_space():
                return None
            self.toggle_play_pause()
        elif action == "left":
            self.seek_backward()
        elif action == "right":
            self.seek_forward()
        elif action == "up":
            if self.focus_get() in (self.file_table, self.dir_tree):
                return None
            self.select_prev_file()
        elif action == "down":
            if self.focus_get() in (self.file_table, self.dir_tree):
                return None
            self.select_next_file()
        else:
            return None
        return "break"

    def _handle_edit_window_shortcut(self, event=None):
        """Cmd+4：切換 Edit Window；回傳 break 防止 bind_all 再執行一次。"""
        self._toggle_edit_window()
        return "break"

    def _handle_edit_window_open_shortcut(self, event=None):
        """Cmd+E 備用入口：只負責開啟 Edit Window，不改變原有行為。"""
        self._open_edit_window()
        return "break"

    def _handle_edit_window_digit_fallback(self, event):
        """Tk 9/macOS 若遺失數字 keysym，仍可用 event.char 辨識 Cmd/Ctrl＋4。"""
        char = str(getattr(event, "char", "") or "")
        keysym = str(getattr(event, "keysym", "") or "")
        if char == "4" or keysym in ("4", "KP_4"):
            return self._handle_edit_window_shortcut(event)
        return None

    def _delete_allowed(self):
        """app 級 Delete/BackSpace 是否該執行刪檔：只在焦點落在檔案表／資料夾樹（或無特定
        焦點、或視窗本身）時才允許，避免焦點在按鈕/選單/滑桿時誤刪選取的音檔。"""
        if self._focus_in_text_entry():
            return False
        return self.focus_get() in (self.file_table, self.dir_tree, self, None)

    def _focus_blocks_space(self):
        """空白鍵是否該讓給目前焦點元件（傳統 ttk/tk 按鈕、下拉選單、核取方塊的空白鍵有自己
        的用途；同時觸發播放會造成雙重動作）。CTk 控制項以 Canvas 實作、不綁空白鍵，不在此列，
        因此波形/表格上仍可用空白鍵播放。"""
        foc = self.focus_get()
        if foc is None:
            return False
        try:
            cls = foc.winfo_class()
        except Exception:
            return False
        return cls in ("TButton", "Button", "TCombobox", "Checkbutton", "TCheckbutton", "Radiobutton", "TRadiobutton")

    def remove_selected_files(self):
        selected = self.file_table.selection()
        # 選到資料夾節點時，展開成其底下所有檔案一併移除
        file_iids = []
        for iid in selected:
            if self.file_table.tag_has("folder", iid):
                file_iids.extend(self.file_table.get_children(iid))
            else:
                file_iids.append(iid)

        # 批次刪除保險：一次移除 2 個以上（例如全選誤按 Delete）先確認；單檔維持即刪不打擾。
        # 移除可用 Cmd+Z 復原（見 _undo_remove_files），此對話框只是避免誤觸的第一道防線。
        if len(file_iids) > 1:
            if not messagebox.askyesno(
                    "移除檔案",
                    f"確定要從工作區移除選取的 {len(file_iids)} 個檔案？\n"
                    "（不會刪除磁碟上的原始音檔；可用 Cmd+Z 復原）",
                    icon="warning", default="no", parent=self):
                return

        ws = self.workspaces[self.active_ws_idx]
        removed = []
        for iid in file_iids:
            entry = next((f for f in self.audio_files if f["path"] == iid), None)
            if entry is not None:
                removed.append(dict(entry))
            if self.file_table.exists(iid):
                self.file_table.delete(iid)
            self.audio_files = [f for f in self.audio_files if f["path"] != iid]

            if self.current_file_path == iid:
                self.stop_playback()
                self.lbl_active_file.configure(text="No File Selected")
                self.current_audio = None
                self.original_lufs_val = None
                self.lbl_info_current.configure(text="--")
                self.lbl_info_gain.configure(text="--")
                self.waveform_canvas.delete("all")

        if removed:
            self._undo_stack.append(("remove_files", (ws, removed)))
            if len(self._undo_stack) > 50:
                self._undo_stack = self._undo_stack[-50:]

        # 清除變空的母資料夾分組節點
        self._prune_empty_folder_nodes()
        self._update_empty_hint()
        self.check_export_ready()
        self._schedule_autosave()

    def on_table_right_click(self, event):
        selected = self.file_table.selection()
        if not selected:
            return
        # 展開資料夾節點成其底下檔案，取得實際作用的檔案清單
        file_iids = []
        for iid in selected:
            if self.file_table.tag_has("folder", iid):
                file_iids.extend(self.file_table.get_children(iid))
            else:
                file_iids.append(iid)
        file_iids = list(dict.fromkeys(file_iids))  # 去重、保序

        menu = tk.Menu(self, tearoff=0)
        if file_iids:
            # 失敗/離線檔的重試出口（以前只能移除再重匯）
            menu.add_command(label=f"🔄 重新分析（{len(file_iids)}）",
                             command=lambda p=list(file_iids): self._reanalyze_files(p))
            # 依檔名語意批次建議目標 LUFS（bgm/win/spinstop…），主動選用才生效、可 Cmd+Z 還原
            menu.add_command(label=f"✨ 依檔名建議目標 LUFS（{len(file_iids)}）",
                             command=lambda p=list(file_iids): self._suggest_targets_for(p))
            menu.add_separator()
        menu.add_command(label=f"移除選取的 {len(selected)} 個檔案",
                        command=lambda: self.remove_selected_files())
        menu.post(event.x_root, event.y_root)

    def _reanalyze_files(self, paths):
        """重新分析選取檔案：離線檔接回磁碟後、或分析失敗後的重試出口。"""
        for p in paths:
            entry = next((e for e in self.audio_files if e["path"] == p), None)
            if not entry:
                continue
            lufs_display = f"{entry['lufs']:.1f} LUFS" if isinstance(entry.get("lufs"), float) else "--"
            if not os.path.isfile(p):
                entry["status"] = "🔴 離線"
                self.update_table_row(p, entry.get("duration", "--:--"), entry["status"], lufs_display, None)
                continue
            entry["status"] = "🟡 載入中"
            self.update_table_row(p, entry.get("duration", "--:--"), entry["status"], lufs_display, None)
            threading.Thread(target=self.analyze_single_file, args=(entry,), daemon=True).start()

    def _suggest_targets_for(self, paths):
        """把 suggest_target_lufs 的檔名語意表套用到選取檔案（batch 設定目標的快速出口）。
        推 undo 快照，Cmd+Z 可整批還原。"""
        self._push_lufs_undo()
        applied = 0
        for p in paths:
            entry = next((e for e in self.audio_files if e["path"] == p), None)
            if not entry:
                continue
            t = self.suggest_target_lufs(entry["name"])
            entry["target_lufs"] = float(t)
            if self.file_table.exists(p):
                self.file_table.set(p, "目標 LUFS", f"{t:.1f} LUFS")
                self._sync_true_peak_cells(self.file_table, p, entry)
            applied += 1
        # 右側 fader/資訊卡跟上「目前主檔」的新目標
        cur = next((e for e in self.audio_files if e["path"] == self.current_file_path), None)
        if cur and isinstance(cur.get("target_lufs"), float):
            self.target_lufs_var.set(cur["target_lufs"])
            self.update_target_lufs(cur["target_lufs"], from_selection=True)
        if applied:
            self._schedule_autosave()
            self._schedule_wave_draw()  # 依檔名建議的目標 LUFS 套用後 → 波形即時依新增益重畫

    def analyze_single_file(self, entry, preserve_saved_lufs=False):
        """解碼檔案＋量測 LUFS／True Peak。

        preserve_saved_lufs=True：專案重新開啟時使用——這顆音檔的「原始 LUFS」「原始 True Peak」
        已經從 .abproj／session 存檔忠實讀回 entry，這裡只補回播放/波形需要的 AudioSegment，
        不重新量測覆蓋掉它們。這兩個值都應該是『這顆音當初被匯入時』的量測值，從此凍結，不該因為
        重新開啟專案、或磁碟上的來源檔案在那之後被其他動作（例如匯出到同一路徑）覆蓋，就悄悄跟著
        改變、甚至被目標值追上（見這次修的 bug：重開專案後原始 LUFS 被目標 LUFS 蓋掉）。
        """
        try:
            audio = AudioSegment.from_file(entry["path"])
            entry["audio"] = audio
            entry["source_bit_depth"] = _probe_audio_bit_depth(entry["path"]) or _audio_bit_depth(audio)
            entry["duration"] = self._entry_duration_label(
                entry,
                audio.duration_seconds,
            )

            keep_saved_lufs = preserve_saved_lufs and isinstance(entry.get("lufs"), float)
            keep_saved_tp = preserve_saved_lufs and isinstance(entry.get("true_peak"), float)

            if keep_saved_lufs:
                lufs = entry["lufs"]
            if not keep_saved_lufs or not keep_saved_tp:
                analysis_audio = audio if audio.channels <= 5 else audio.set_channels(2)
                samples = np.array(analysis_audio.get_array_of_samples())
                if analysis_audio.channels > 1:
                    samples = samples.reshape((-1, analysis_audio.channels))

                max_val = float(2 ** (8 * analysis_audio.sample_width - 1))
                samples = samples.astype(np.float32) / max_val

                if not keep_saved_lufs:
                    meter = pyln.Meter(audio.frame_rate, block_size=0.400)
                    if len(samples) / audio.frame_rate < 0.4:
                        pad_length = int(np.ceil(0.4 * audio.frame_rate)) - len(samples)
                        if samples.ndim == 1:
                            analysis_samples = np.pad(samples, (0, pad_length), mode='constant')
                        else:
                            analysis_samples = np.pad(samples, ((0, pad_length), (0, 0)), mode='constant')
                        lufs = meter.integrated_loudness(analysis_samples)
                    else:
                        lufs = meter.integrated_loudness(samples)
                    entry["lufs"] = lufs

                if not keep_saved_tp:
                    entry["true_peak"] = self._measure_true_peak_db(samples)

            if entry.get("target_lufs") is None:
                entry["target_lufs"] = lufs  # 預設目標 = 原始 LUFS（不改音量）
            entry["status"] = "🟢 就緒"
            target_display = f"{entry['target_lufs']:.1f} LUFS"
            orig_tp_disp, target_tp_disp = self._true_peak_displays(entry)
            self._enqueue_ui(self.update_table_row, entry["path"], entry["duration"], entry["status"],
                             f"{lufs:.1f} LUFS", target_display, entry.get("_table"),
                             orig_tp_disp, target_tp_disp)
            self._enqueue_ui(self._schedule_autosave)

        except Exception as e:
            traceback.print_exc()
            entry["status"] = "🔴 失敗"
            self._enqueue_ui(self.update_table_row, entry["path"], "--:--", entry["status"], "Error", None,
                             entry.get("_table"))

    def analyze_all_files(self):
        for entry in self.audio_files:
            try:
                audio = AudioSegment.from_file(entry["path"])
                entry["audio"] = audio
                entry["source_bit_depth"] = _probe_audio_bit_depth(entry["path"]) or _audio_bit_depth(audio)
                entry["duration"] = self._entry_duration_label(
                    entry,
                    audio.duration_seconds,
                )

                analysis_audio = audio if audio.channels <= 5 else audio.set_channels(2)
                samples = np.array(analysis_audio.get_array_of_samples())
                if analysis_audio.channels > 1:
                    samples = samples.reshape((-1, analysis_audio.channels))
                max_val = float(2 ** (8 * analysis_audio.sample_width - 1))
                samples = samples.astype(np.float32) / max_val

                meter = pyln.Meter(audio.frame_rate, block_size=0.400)

                if len(samples) / audio.frame_rate < 0.4:
                    pad_length = int(np.ceil(0.4 * audio.frame_rate)) - len(samples)
                    analysis_samples = np.pad(samples, (0, pad_length), mode='constant') if samples.ndim == 1 else np.pad(samples, ((0, pad_length), (0, 0)), mode='constant')
                    lufs = meter.integrated_loudness(analysis_samples)
                else:
                    lufs = meter.integrated_loudness(samples)

                entry["lufs"] = lufs
                entry["target_lufs"] = lufs  # 預設目標 = 原始 LUFS（不改音量）
                entry["status"] = "🟢 就緒"
                self._enqueue_ui(self.update_table_row, entry["path"], entry["duration"], entry["status"],
                                 f"{lufs:.1f} LUFS", f"{lufs:.1f} LUFS", entry.get("_table"))
                self._enqueue_ui(self._schedule_autosave)

            except Exception as e:
                traceback.print_exc()
                entry["status"] = "🔴 失敗"
                self._enqueue_ui(self.update_table_row, entry["path"], "--:--", entry["status"], "Error", None,
                                 entry.get("_table"))

    def update_table_row(self, iid, dur, status, lufs, target_lufs=None, table=None,
                         orig_tp=None, target_tp=None):
        # 還原 session 時會同時分析多個工作區的檔案，每個工作區各有自己的
        # file_table；用 entry 記住的 table 路由到正確的那個表，沒帶就更新作用中的。
        table = table or self.file_table
        # 背景分析執行緒可能在工作區已關閉、表格已 destroy 後才回寫 → 用 try 包住避免 TclError
        try:
            if not table.exists(iid):
                return
            table.set(iid, "Duration", dur)
            table.set(iid, "Status", status)
            table.set(iid, "原始 LUFS", lufs)
            if target_lufs is not None:
                table.set(iid, "目標 LUFS", target_lufs)
            if orig_tp is not None:
                table.set(iid, "原始 True Peak", orig_tp)
            if target_tp is not None:
                table.set(iid, "目標 True Peak", target_tp)
        except Exception:
            return
        self.check_export_ready()  # 分析完成（載入中 → 就緒）→ 就緒計數即時更新

    def _format_true_peak(self, tp_val):
        return f"{tp_val:.1f} dBTP"

    def _true_peak_displays(self, entry):
        """回傳 (原始 True Peak 顯示字串, 目標 True Peak 顯示字串)。
        原始 True Peak 是量到就凍結的值；目標 True Peak 是原始值隨『目標 LUFS − 原始 LUFS』
        這個增益等比平移算出來的（線性增益不改變波形相對峰值關係，不需要為了顯示重新超取樣量測）。
        文字本身的顏色是另外用疊加 Label 畫的，見 _refresh_true_peak_overlays。"""
        tp = entry.get("true_peak")
        if not isinstance(tp, float):
            return "--", "--"
        orig_disp = self._format_true_peak(tp)
        lufs = entry.get("lufs")
        target = entry.get("target_lufs")
        if isinstance(lufs, float) and isinstance(target, float):
            target_disp = self._format_true_peak(tp + (target - lufs))
        else:
            target_disp = "--"
        return orig_disp, target_disp

    def _sync_true_peak_cells(self, table, path, entry):
        if not table.exists(path):
            return
        orig_disp, target_disp = self._true_peak_displays(entry)
        table.set(path, "原始 True Peak", orig_disp)
        table.set(path, "目標 True Peak", target_disp)

    # ─────────────────────────────────────────────────────────
    # True Peak 數值文字真正變色：ttk.Treeview 同一列沒辦法只讓單一儲存格的文字變色
    # （tag_configure 的 foreground 是整列套用），所以在這兩欄的儲存格正上方疊一個
    # 剛好蓋住儲存格範圍的 tk.Label，文字內容一樣、但顏色可以自己決定；隨捲動/縮放/
    # 展開收合/選取狀態改變，用一個輕量的週期性重新整理 (_refresh_true_peak_overlays)
    # 保持位置跟顏色同步，比為每一種可能觸發重排的事件個別綁定更穩、也更簡單。
    # ─────────────────────────────────────────────────────────

    def _true_peak_color(self, tp_val):
        if tp_val > -1.0:
            return COLOR_RED       # 逼近/超過 True Peak 安全邊界
        if tp_val > -3.0:
            return "#FFD700"       # 偏高，留意
        return "#E0A64D"           # 一般情況：琥珀色，跟白色/青色的 LUFS 數字明確區隔

    def _true_peak_value_for(self, entry, col):
        tp = entry.get("true_peak")
        if not isinstance(tp, float):
            return None
        if col == "原始 True Peak":
            return tp
        lufs, target = entry.get("lufs"), entry.get("target_lufs")
        if isinstance(lufs, float) and isinstance(target, float):
            return tp + (target - lufs)
        return None

    def _start_true_peak_overlay_loop(self):
        self._refresh_true_peak_overlays()

    def _refresh_true_peak_overlays(self):
        self._do_refresh_true_peak_overlays()
        self.after(200, self._refresh_true_peak_overlays)

    def _do_refresh_true_peak_overlays(self):
        """實際重新整理疊圖位置/顏色的動作，跟『排下一次 200ms 週期』分開成兩個函式：
        週期輪詢是保底（涵蓋任何沒被個別事件攔到的情況，例如視窗縮放、分析完成回填），
        但捲動時真正要跟手，得靠 _schedule_true_peak_overlay_refresh 在捲動事件當下立刻
        補畫一次——如果那邊直接呼叫 _refresh_true_peak_overlays()，會多排出一條完全獨立的
        200ms 續發鏈，越滾越多個同時跑的計時器；呼叫這個『不含續發』的版本才不會有這個問題。"""
        for ws in getattr(self, "workspaces", []):
            table = ws.file_table
            if table is None:
                continue
            try:
                if table.winfo_exists():
                    self._refresh_true_peak_overlays_for_table(table, ws)
            except Exception:
                pass

    def _schedule_true_peak_overlay_refresh(self, delay=16):
        """捲動（左右／上下）時讓 True Peak 疊圖立刻跟上，不必等到下一次 200ms 輪詢——
        這就是使用者看到『貼圖延遲跟著畫面移動』的根因。這裡要用『節流』（leading-edge
        throttle）而不是『去抖』（debounce）：先前是每個滾輪 tick 都 cancel 掉前一個排定的
        refresh 再重新排一次，觸控板連續手勢一次送出幾十個 tick、間隔遠小於 delay，導致
        refresh 永遠被下一個 tick 取消、直到手勢完全停下才補畫一次——結果整段捲動過程中
        疊圖完全沒跟上，跟沒修一樣。改成：已經有排定中的 refresh 就不重排、直接讓它照原定
        時間執行（執行時讀到的是當下最新的捲動位置），沒有排定中才新排一個，這樣連續捲動時
        會穩定以約略 delay 的節奏持續補畫，而不是要等到停下來才動。"""
        if getattr(self, "_tp_overlay_refresh_job", None):
            return
        self._tp_overlay_refresh_job = self.after(delay, self._fire_true_peak_overlay_refresh)

    def _fire_true_peak_overlay_refresh(self):
        self._tp_overlay_refresh_job = None
        self._do_refresh_true_peak_overlays()

    def _on_true_peak_label_click(self, table, iid, mode):
        """把 True Peak 疊圖 Label 上的點擊轉發成 Treeview 選取，行為對齊一般儲存格點擊。"""
        if mode == "shift":
            anchor = table.focus() or iid
            items = list(self._iter_file_iids(table))
            try:
                i0, i1 = items.index(anchor), items.index(iid)
            except ValueError:
                i0 = i1 = items.index(iid) if iid in items else 0
            lo, hi = min(i0, i1), max(i0, i1)
            table.selection_set(items[lo:hi + 1])
        elif mode == "toggle":
            cur = list(table.selection())
            if iid in cur:
                cur.remove(iid)
            else:
                cur.append(iid)
            table.selection_set(cur)
        else:
            table.selection_set(iid)
        table.focus(iid)

    def _forward_wheel_to_table(self, event, table, shift=False):
        """True Peak 疊圖 Label 也蓋住了滾輪事件：游標在這兩欄上時，事件目標是 Label
        本身（沒有任何捲動綁定），底下的 Treeview 完全收不到，導致游標移到這兩欄
        上、上下左右都捲不動。轉發成同一個虛擬事件送回 Treeview，讓它照原本的垂直/
        Shift+橫向捲動處理（見 _bind_smooth_hscroll），行為對齊一般儲存格上滾動。
        關鍵是 when="now"：event_generate 預設 when="tail" 只是把事件排進佇列尾端，
        要等下一輪 mainloop 才會真正處理——觸控板連續捲動時一次送出一長串事件，每個都
        多繞一層佇列，疊圖 Label 收到的節奏會漸漸落後於真正的捲動，就是使用者說的
        『貼圖延遲』。when="now" 讓它在這裡立刻同步派送、跑完當下的 class binding
        （含真正捲動與下面排的疊圖重畫），不再多一層排隊延遲。"""
        self._wheel_dbg(
            f"label-forward: widget={event.widget!r} delta={getattr(event,'delta','?')} "
            f"num={getattr(event,'num','?')} shift={shift}"
        )
        self._scroll_table_by_wheel(table, event, shift=shift)
        return "break"

    @staticmethod
    def _wheel_dbg(msg):
        """滾輪問題的臨時診斷用：只有存在旗標檔時才寫，平常完全沒有成本。
        旗標／記錄都放家目錄（打包成 .app 之後 /tmp 不一定寫得進去）。"""
        try:
            home = os.path.expanduser("~")
            if not (os.path.exists(os.path.join(home, "AM_DBG")) or os.path.exists("/tmp/AM_DBG")):
                return
            with open(os.path.join(home, "am_wheel.log"), "a") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    def _scroll_table_by_wheel(self, table, event, shift=False):
        """直接捲動表格，不再用 event_generate 把滾輪事件「轉發」回 Treeview。

        轉發本身在單元測試裡是會動的（Tk 9.0.1 下 delta=-3、-120 都能捲），所以它不是
        已知的破綻；但它多依賴了一層 ttk 內建 <MouseWheel> class binding 的實作細節
        （delta 換算成列數的方式各版本不同），而且產生出來的事件沒有座標（x_root/y_root
        會是 -1），一旦哪個環節對不上就是「完全不動又不報錯」，很難查。
        直接呼叫 yview_scroll/xview_moveto 少掉整層不確定性：只看 delta 的正負號自己捲，
        跟左側資料夾樹的 _hwheel 同一套做法，不管 delta 是 1 還是 120 都一定會動。"""
        d = getattr(event, "delta", 0)
        if d == 0:
            num = getattr(event, "num", 0)
            d = 1 if num == 4 else (-1 if num == 5 else 0)
        if not d:
            return
        if shift:
            # 橫向：沿用 _bind_smooth_hscroll 的等比例平滑捲動，維持跟手的手感。
            try:
                first, last = table.xview()
                visible_frac = last - first
                if visible_frac <= 0 or visible_frac >= 1.0:
                    return
                width = max(table.winfo_width(), 1)
                # delta 已經是 120 的倍數就照比例，否則一次事件當作一格，避免被除成 0。
                notches = (d / 120.0) if abs(d) >= 120 else (1.0 if d > 0 else -1.0)
                delta_px = -notches * 48.0
                new_first = first + (delta_px * visible_frac) / width
                new_first = max(0.0, min(new_first, 1.0 - visible_frac))
                table.xview_moveto(new_first)
            except Exception:
                pass
        else:
            try:
                table.yview_scroll(-1 if d > 0 else 1, "units")
            except Exception:
                pass
        # True Peak 疊圖不會自己跟著捲，捲完要立刻補畫位置。
        self._schedule_true_peak_overlay_refresh()

    def _refresh_true_peak_overlays_for_table(self, table, ws):
        store = getattr(ws, "_tp_overlays", None)
        if store is None:
            store = {}
            ws._tp_overlays = store
        by_path = {e["path"]: e for e in ws.audio_files}
        selected = set(table.selection())
        seen = set()
        for iid in self._iter_file_iids(table):
            entry = by_path.get(iid)
            if not entry:
                continue
            for col in ("原始 True Peak", "目標 True Peak"):
                key = (iid, col)
                try:
                    bbox = table.bbox(iid, col)
                    disp_text = table.set(iid, col)
                except Exception:
                    bbox = None
                    disp_text = ""
                if not bbox or not disp_text or disp_text == "--":
                    lbl = store.pop(key, None)
                    if lbl is not None:
                        try:
                            lbl.place_forget()
                        except Exception:
                            pass
                    continue
                seen.add(key)
                x, y, w, h = bbox
                val = self._true_peak_value_for(entry, col)
                color = self._true_peak_color(val) if val is not None else "#8E8E93"
                bg = COLOR_SELECTED if iid in selected else COLOR_PANEL
                lbl = store.get(key)
                try:
                    if lbl is None:
                        lbl = tk.Label(table, text=disp_text, font=("Roboto", self.BASE_FILE_FONT_SIZE),
                                       fg=color, bg=bg, anchor="center", bd=0, highlightthickness=0)
                        # 這個 Label 疊在儲存格正上方，點擊會被它整個接走、傳不到底下的
                        # Treeview，導致點這兩欄選不到該列；轉發點擊給 Treeview，行為對齊
                        # 一般儲存格點擊（含 Shift/Cmd 多選），見 _on_true_peak_label_click。
                        lbl.bind("<Button-1>", lambda e, t=table, i=iid: self._on_true_peak_label_click(t, i, "single"))
                        lbl.bind("<Shift-Button-1>", lambda e, t=table, i=iid: self._on_true_peak_label_click(t, i, "shift"))
                        lbl.bind("<Command-Button-1>", lambda e, t=table, i=iid: self._on_true_peak_label_click(t, i, "toggle"))
                        lbl.bind("<Control-Button-1>", lambda e, t=table, i=iid: self._on_true_peak_label_click(t, i, "toggle"))
                        # 游標在這兩欄的疊圖 Label 上時，滾輪事件會被 Label 整個接走、傳不到
                        # 底下的 Treeview，導致游標移到這兩欄上下左右都捲不動；轉發回 Treeview。
                        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                            lbl.bind(seq, lambda e, t=table: self._forward_wheel_to_table(e, t))
                        for seq in ("<Shift-MouseWheel>", "<Shift-Button-4>", "<Shift-Button-5>"):
                            lbl.bind(seq, lambda e, t=table: self._forward_wheel_to_table(e, t, shift=True))
                        store[key] = lbl
                    else:
                        lbl.configure(text=disp_text, fg=color, bg=bg,
                                      font=("Roboto", self.BASE_FILE_FONT_SIZE))
                    lbl.place(x=x, y=y, width=w, height=h)
                except Exception:
                    store.pop(key, None)
        for key in [k for k in store if k not in seen]:
            lbl = store.pop(key, None)
            if lbl is not None:
                try:
                    lbl.destroy()
                except Exception:
                    pass

    def on_table_select(self, event):
        if event is not None and hasattr(event, 'widget'):
            event.widget.focus_set()  # 確保鍵盤 focus 在 file_table 上
        selected = self.file_table.selection()
        # 只取「檔案」節點（略過母資料夾分組節點）
        file_sel = [s for s in selected if not self.file_table.tag_has("folder", s)]
        # 換選取 → 取消尚未套用的批次 Gain 拖曳工作、解除「拖曳中」旗標；
        # 滑桿/框格顯示的數字改成反映「這個檔案目前已套用的總增益」（見下方），不再一律歸零，
        # 這樣調過的批次 dB（例如 -3dB）換選別的音檔再點回來時記錄還在。
        if hasattr(self, "gain_adj_var"):
            if getattr(self, "_gain_apply_job", None):
                try:
                    self.after_cancel(self._gain_apply_job)
                except Exception:
                    pass
                self._gain_apply_job = None
            self._gain_active = False
        if not file_sel:
            self._current_wave_entries = []
            self._apply_right_layout()
            if hasattr(self, "gain_adj_var"):
                self.gain_adj_var.set(0.0)
                self.gain_entry_var.set("0.0")
                self._gain_display_at_rest = 0.0
                self._gain_display_uniform = True
            return

        by_path = {it["path"]: it for it in self.audio_files}

        path = file_sel[0]  # 以第一個選取檔案為主檔（播放／LUFS 控制對象）
        fname = os.path.basename(path)
        if len(file_sel) > 1:
            self.lbl_active_file.configure(text=f"{fname}　（已選 {len(file_sel)} 個）")
        else:
            self.lbl_active_file.configure(text=fname)
        self.stop_playback()

        # Edit Window 開著時，裡面的音軌要完全跟著主畫面目前的選取換，選什麼就編輯什麼
        # （見 _schedule_edit_window_follow／_sync_edit_window_selection），不是只標亮。
        self._schedule_edit_window_follow(file_sel)

        entry = by_path.get(path)
        if entry and entry["audio"]:
            self.current_file_path = entry["path"]
            # 用 Edit Window 剪輯後的結果播放/顯示，不是原始 entry['audio']：剪輯完之後
            # 主畫面看到、聽到的就該是編輯後的樣子（沒編輯過就等於原始檔，行為不變）。
            self.current_audio = self._render_edited_audio(entry)
            self.playback_duration = self.current_audio.duration_seconds
            self.lbl_time.configure(text=f"00:00 / {self.format_time(self.playback_duration)}")
            self.original_lufs_val = entry["lufs"] if isinstance(entry["lufs"], float) else None

            target_val = entry.get("target_lufs")
            if target_val is None:
                target_val = entry["lufs"] if isinstance(entry["lufs"], float) else -16.0
            self.target_lufs_var.set(target_val)
            self.update_target_lufs(target_val, from_selection=True)

        # 批次 ±Gain 滑桿/框格：顯示這次選取檔案『目前已套用的總增益』，不再每次選取都歸零
        # ——例如先前對幾顆音效批次調過 -3dB，選別的檔案後再點回來，這裡會重新算出 -3.0 顯示回去。
        self._refresh_gain_display()

        # 波形：一律只顯示主檔（file_sel[0]）的單一波形；多選其餘檔案要編輯／比對
        # 請用 Cmd+4 開 Edit Window。大量選取（如 Cmd+A 全選）時用去抖動
        # （_schedule_wave_draw）避免連續選取觸發重畫卡住。
        sel_entries = []
        for p in file_sel:
            e = by_path.get(p)
            if e and e.get("audio") is not None:
                sel_entries.append(e)
        self._current_wave_entries = sel_entries
        self._apply_right_layout()
        self._schedule_wave_draw()

    def _ensure_entry_audio_decoded(self, entry):
        """Edit Window 需要 entry['audio']（解碼後的 AudioSegment）才能建出軌道。正常情況下
        匯入／重新開啟專案時，背景執行緒（analyze_single_file，見 _restore_workspace_into）
        會補上這個欄位，但如果使用者在那個背景執行緒還沒跑完之前就點了 Edit Window，或那個
        背景執行緒因為任何原因失敗，就會出現『音檔明明在清單裡、卻說沒有已分析完成的音檔』
        的情況。這裡在真的要開 Edit Window 的當下，同步（主執行緒）補做一次解碼——這個
        App 處理的是音效/短 Jingle，就算同步解碼也幾乎不會感覺到卡頓，不必為此多開一條背景
        執行緒再等它跑完。"""
        if entry.get("audio") is not None:
            return True
        path = entry.get("path")
        if not path or not os.path.isfile(path):
            return False
        try:
            entry["audio"] = AudioSegment.from_file(path)
            return True
        except Exception:
            traceback.print_exc()
            return False

    def _open_edit_window(self):
        """Cmd+4／選單 Windows → Edit Windows：開啟（或重新載入）多軌剪輯視窗。
        以目前中央表格選取的音檔為準；沒有選取就用目前的主檔；都沒有就用整個工作區存在的
        音檔（一般直接按快捷鍵、不先選取也能開），真的一個都沒有才提示。"""
        file_sel = [s for s in self.file_table.selection() if not self.file_table.tag_has("folder", s)]
        by_path = {it["path"]: it for it in self.audio_files}
        entries = [by_path[p] for p in file_sel if p in by_path]
        if not entries and getattr(self, "current_file_path", None):
            e = by_path.get(self.current_file_path)
            if e:
                entries = [e]
        if not entries:
            entries = list(self.audio_files)
        entries = [e for e in entries if self._ensure_entry_audio_decoded(e)]
        if not entries:
            messagebox.showinfo("Edit Window", "請先匯入至少一個已分析完成的音檔。", parent=self)
            return
        if not self._edit_window_open():
            self._edit_window = EditWindow(self)
        else:
            current_paths = [t["entry"]["path"] for t in self._edit_window.tracks]
            requested_paths = [e["path"] for e in entries]
            if current_paths == requested_paths:
                self._edit_window.win.deiconify()
                self._edit_window.win.lift()
                self._edit_window.win.focus_force()
                self._edit_window.canvas.focus_set()
                return
            # 換成另一組音檔前先保存現有 Edit Window 的編輯，避免重新 load 時遺失。
            self._edit_window.sync_entries()
        self._edit_window.load_entries(entries)

    def _toggle_edit_window(self):
        """Cmd+4：Edit Window 已開就正常儲存並關閉，否則依目前選取內容開啟。"""
        if not self._edit_window_open():
            self._open_edit_window()
            return

        editor = self._edit_window
        editor.on_close()
        # Toplevel 消失後把焦點交還主視窗，下一次 Cmd+4 才能立即再次開啟。
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _schedule_wave_draw(self, delay=90):
        """去抖動排程波形重畫：取消前一個未執行的工作，延遲後只畫最後一次。
        避免 Shift 連續多選／Cmd+A 全選時每次選取變動都同步重畫而卡住。"""
        if getattr(self, "_sel_wave_job", None):
            try:
                self.after_cancel(self._sel_wave_job)
            except Exception:
                pass
        self._sel_wave_job = self.after(delay, self._do_wave_draw)

    def _do_wave_draw(self):
        self._sel_wave_job = None
        entries = getattr(self, "_current_wave_entries", []) or []
        try:
            if entries:
                self.draw_waveform(self._render_edited_audio(entries[0]), entries[0])
            else:
                self.waveform_canvas.delete("all")
        except Exception:
            traceback.print_exc()

    _WAVE_CACHE_RES = 2000  # 每個檔案快取的峰值取樣點數，與畫布像素寬無關，畫的時候再依 track_w 重新取樣

    def _wave_gain_factor(self, entry):
        """依 entry 目前的目標 LUFS 相對原始 LUFS 換算線性增益，供波形即時反映調整後音量。"""
        orig = entry.get("lufs")
        target = entry.get("target_lufs")
        if not isinstance(orig, float) or not isinstance(target, float):
            return 1.0
        return 10 ** ((target - orig) / 20.0)

    @staticmethod
    def _compute_peaks(audio, res):
        """把一份 AudioSegment 掃成『絕對音量（已除以滿刻度，0~1）』的峰值陣列，
        供 _get_cached_peaks／_get_effective_peaks 共用（純函式，不碰任何快取狀態）。"""
        dtype = {1: np.int8, 2: np.int16, 4: np.int32}.get(audio.sample_width, np.int16)
        raw = np.frombuffer(audio.raw_data, dtype=dtype)
        channels = audio.channels or 1
        n_frames = len(raw) // channels
        if n_frames <= 0:
            return np.zeros(1, dtype=np.float32)
        chunk = max(1, n_frames // min(res, n_frames))
        usable = (n_frames // chunk) * chunk
        mat = raw[:usable * channels].reshape(-1, chunk, channels)
        peaks = np.abs(mat).max(axis=1).max(axis=1).astype(np.float32)
        full_scale = float(2 ** (8 * audio.sample_width - 1))
        return peaks / full_scale if full_scale else peaks

    def _get_cached_peaks(self, entry):
        """回傳 entry 原始音訊（entry['audio']，未套用 Edit Window 剪輯）的峰值快取。
        Edit Window 自己畫每個 region 的波形時，是拿這份『原始檔案的峰值』依 src_start/
        src_end 的比例去切片（見 _draw_region），所以這裡一定要維持指向未剪輯的原始音訊，
        不能改成編輯後的結果，否則切片比例會全部跑掉。主畫面單軌波形要看『剪輯後』的樣子，
        請改用 _get_effective_peaks。
        只在第一次（或音檔物件變動後，用 is 比對而非重算）解碼一次，之後拖 dB/LUFS、
        調整視窗尺寸都直接複用同一份快取、只做便宜的重取樣，不重新掃整段 PCM。"""
        audio = entry.get("audio")
        if audio is None:
            return None
        cached = entry.get("_peak_cache")
        if cached is not None and cached[0] is audio:
            return cached[1]
        peaks = self._compute_peaks(audio, self._WAVE_CACHE_RES)
        entry["_peak_cache"] = (audio, peaks)
        return peaks

    def _peek_cached_peaks(self, entry):
        """非阻塞版本：只在已經有快取時才回傳，否則直接回傳 None，絕不在呼叫當下解碼。
        Edit Window 的多軌波形必須用這個版本，交由背景執行緒
        （_queue_peak_decode）處理實際解碼，避免多選超多檔案時在主執行緒卡住 UI；
        單軌（draw_waveform）解單一檔案很快，仍可直接呼叫 _get_cached_peaks。"""
        audio = entry.get("audio")
        if audio is None:
            return None
        cached = entry.get("_peak_cache")
        if cached is not None and cached[0] is audio:
            return cached[1]
        return None

    def _queue_peak_decode(self, entry):
        """把尚未解碼的音檔丟到背景執行緒建立峰值快取，完成後排程重畫。
        多選超多檔案時，若同步在主執行緒逐一解碼整批 PCM 會卡死 UI（實測 120 首 3 分鐘音檔
        同步解碼要近 10 秒、期間視窗完全沒反應，看起來就像『顯示不出來』）；改成背景解碼、
        先畫骨架＋『解碼中』佔位線，解出來後再補上真正的波形。"""
        path = entry.get("path")
        pending = getattr(self, "_peak_decode_pending", None)
        if pending is None:
            pending = set()
            self._peak_decode_pending = pending
        if path in pending:
            return
        pending.add(path)

        def _worker():
            try:
                self._get_cached_peaks(entry)
            except Exception:
                traceback.print_exc()
            finally:
                pending.discard(path)
                self._enqueue_ui(self._schedule_wave_draw)

        threading.Thread(target=_worker, daemon=True).start()

    def _get_effective_peaks(self, entry):
        """主畫面單軌波形用：反映 Edit Window 剪輯結果的峰值快取。沒編輯過時渲染結果就是
        entry['audio'] 本身，直接複用 _get_cached_peaks，不會多存一份重複的峰值陣列；
        有編輯過才另外對渲染後的音訊算一份、快取鍵是渲染結果物件本身（跟著
        _render_edited_audio 的快取一起失效／更新）。"""
        rendered = self._render_edited_audio(entry)
        if rendered is entry.get("audio"):
            return self._get_cached_peaks(entry)
        cached = entry.get("_edited_peak_cache")
        if cached is not None and cached[0] is rendered:
            return cached[1]
        peaks = self._compute_peaks(rendered, self._WAVE_CACHE_RES)
        entry["_edited_peak_cache"] = (rendered, peaks)
        return peaks

    def draw_waveform(self, audio, entry=None):
        self.waveform_canvas.delete("all")
        width = self.waveform_canvas.winfo_width()
        height = self.waveform_canvas.winfo_height()

        if width <= 1 or height <= 1:
            width = 370
            height = 120
        self._active_track_width = width  # 單軌：播放桿/seek 以整寬為基準
        # 單軌顯示不需要捲動：重設捲動範圍/位置，避免殘留上次多軌捲動的狀態
        self.waveform_canvas.configure(scrollregion=(0, 0, width, height))
        self.waveform_canvas.yview_moveto(0)

        # 每 1 秒一條格線，量化時間軸（畫在波形底下）
        duration = audio.duration_seconds
        if duration > 0:
            px_per_sec = width / duration
            sec = 1
            while sec * px_per_sec < width:
                gx = sec * px_per_sec
                self.waveform_canvas.create_line(gx, 0, gx, height, fill="#242428")
                sec += 1

        peaks_abs = self._get_effective_peaks(entry) if entry is not None else None
        if peaks_abs is None:
            if entry is not None:
                self._queue_peak_decode(entry)
            return

        w = max(1, width)
        idxs = np.linspace(0, len(peaks_abs) - 1, w).astype(int)
        resized = peaks_abs[idxs]
        gain = self._wave_gain_factor(entry) if entry is not None else 1.0
        scaled = resized * gain

        center_y = height / 2
        for x, peak in enumerate(scaled):
            line_height = min(peak, 1.0) * (height / 2) * 0.9
            # 增益調整後若超過 0dBFS → 警示色，一眼看出會削波
            line_color = "#FF5A4D" if peak > 1.0 else "#4DA6FF"
            self.waveform_canvas.create_line(x, center_y - line_height, x, center_y + line_height, fill=line_color)

    def _playhead_yrange(self):
        return 0, self.waveform_canvas.winfo_height()

    def _on_waveform_configure(self, event=None):
        """波形畫布尺寸改變 → 去抖動後依新尺寸重畫（避免每個 resize 事件都重算）。"""
        if getattr(self, "_layout_settling", False):
            return  # 版面切換凍結期：交由 _finish_relayout 統一重畫
        if getattr(self, "_wave_redraw_job", None):
            try:
                self.after_cancel(self._wave_redraw_job)
            except Exception:
                pass
        self._wave_redraw_job = self.after(60, self._redraw_waveforms)

    def _redraw_waveforms(self):
        self._wave_redraw_job = None
        entries = [e for e in getattr(self, "_current_wave_entries", []) if e.get("audio") is not None]
        if entries:
            self.draw_waveform(self._render_edited_audio(entries[0]), entries[0])

    def _apply_meter_layout(self):
        """音量表與輸出裝置選單的佈置：裝置選單放在音量表右側。"""
        lw = self.lufs_wrapper
        lw.columnconfigure(0, weight=0)
        lw.columnconfigure(1, weight=1)
        self.meter_frame.grid_configure(row=5, column=0, columnspan=1, sticky="w")
        self.device_frame.grid_configure(row=5, column=1, columnspan=1, sticky="nw", padx=(8, 0), pady=(8, 14))
        try:
            self.device_menu.pack_configure(fill="none", anchor="nw")
        except Exception:
            pass

    def _apply_right_layout(self):
        """右側面板固定為單欄垂直堆疊（波形／播放器／參數依序往下排）。只需套用一次。

        ⚠️ 前提：lufs_wrapper 必須是「純 CTkFrame」（見其建立處說明）。若改回
        CTkScrollableFrame，這裡的重排會踩到 CTk 內部 <Configure> 無限遞迴而 100% CPU 卡死。
        另外這裡「不可」呼叫 update_idletasks()——波形重畫已用 _schedule_wave_draw 去抖動排程，
        幾何會在事件迴圈自然收斂。"""
        if getattr(self, "_right_layout_applied", False):
            return
        self._right_layout_applied = True
        # 幾何變動會驚動多個「因 <Configure> 改幾何」的回饋（CTk 捲動框配適、左樹欄寬、
        # 捲軸自動隱藏、波形重畫…），彼此互觸成無限迴圈卡死。對策：套用期間先「凍結」這些
        # 回饋，讓 Tk 幾何自行收斂，再做一次乾淨的最終配置。
        self._layout_settling = True
        rp = self.right_panel
        try:
            self._main_paned.paneconfigure(rp, width=400)
        except Exception:
            pass
        rp.columnconfigure(1, weight=0, minsize=0)
        rp.columnconfigure(0, weight=1, minsize=0)
        rp.rowconfigure(1, weight=0)
        rp.rowconfigure(2, weight=0)
        rp.rowconfigure(3, weight=1)   # 參數捲動框吃滿剩餘高度 → 視窗矮時內部捲動
        self.lbl_active_file.grid_configure(row=0, column=0, columnspan=1, sticky="w")
        self.waveform_canvas.grid_configure(row=1, column=0, rowspan=1, sticky="ew", pady=(5, 5))
        self.player_frame.grid_configure(row=2, column=0, rowspan=1, sticky="we")
        self.lufs_wrapper.grid_configure(row=3, column=0, rowspan=1, sticky="nsew")
        self._apply_meter_layout()
        # 凍結期過後做一次乾淨收尾（此時幾何已穩定，各回饋會一次收斂、不再互觸）
        if getattr(self, "_relayout_job", None):
            try:
                self.after_cancel(self._relayout_job)
            except Exception:
                pass
        self._relayout_job = self.after(200, self._finish_relayout)

    def _finish_relayout(self):
        """版面切換的最終收尾：解除凍結，在已穩定的幾何上做一次乾淨配置（不再有回饋迴圈）。"""
        self._relayout_job = None
        self._layout_settling = False
        # CTk 捲動框配適一次（強制重設一次寬度）
        try:
            sf = self.lufs_wrapper
            canvas = sf._parent_canvas
            self._sf_last_w = None
            canvas.itemconfigure(sf._create_window_id, width=canvas.winfo_width())
            canvas.configure(scrollregion=canvas.bbox("all"))
        except Exception:
            pass
        # 左側樹欄寬各做一次
        for ws in getattr(self, "workspaces", []):
            self._apply_tree_column_width(ws)
        # 波形依最終尺寸重畫一次
        try:
            self._redraw_waveforms()
        except Exception:
            pass

    def draw_waveform_with_playhead(self):
        if hasattr(self, 'current_audio') and self.current_audio:
            cur_entry = next((e for e in self.audio_files
                              if e["path"] == getattr(self, "current_file_path", None)), None)
            self.draw_waveform(self.current_audio, cur_entry)

        if hasattr(self, 'playback_duration') and self.playback_duration > 0:
            progress = self.pause_position / self.playback_duration
            canvas_width = self.waveform_canvas.winfo_width()
            x = int(progress * canvas_width)
            y0, y1 = self._playhead_yrange()
            self.waveform_canvas.create_line(
                x, y0, x, y1,
                fill="#00E5FF", width=2, tags="playhead"
            )

    def _apply_lufs_to_selection(self, val):
        """把目標 LUFS 寫入目前選取（或主檔）的每個檔案並更新表格。"""
        selected = self.file_table.selection()
        paths_to_update = list(selected)
        if not paths_to_update and hasattr(self, 'current_file_path') and self.current_file_path:
            paths_to_update = [self.current_file_path]
        for path in paths_to_update:
            entry = next((item for item in self.audio_files if item["path"] == path), None)
            if entry:
                entry["target_lufs"] = float(val)
                if self.file_table.exists(path):
                    self.file_table.set(path, "目標 LUFS", f"{val:.1f} LUFS")
                    self._sync_true_peak_cells(self.file_table, path, entry)
        self._schedule_autosave()
        self._schedule_wave_draw()  # 目標 LUFS 改變 → 波形即時依新增益重畫

    def _on_lufs_slider(self, val):
        """LUFS 滑桿拖曳：每一格只更新「大數字」（最輕量，與批次 dB 滑桿一致）；
        資訊卡、寫入檔案與表格（多選時很重）全部去抖動到停手後才做，讓拖曳順暢不卡。"""
        val = float(val)
        # 一次連續拖曳只推一筆 undo 快照（在動到值之前），讓 Cmd+Z 能整段還原
        if not getattr(self, "_lufs_drag_active", False):
            self._lufs_drag_active = True
            self._push_lufs_undo()
        self._ensure_ab_target()
        self.lufs_entry_var.set(f"{val:.1f}")
        self._pending_lufs_val = val
        if getattr(self, "_lufs_apply_job", None):
            try:
                self.after_cancel(self._lufs_apply_job)
            except Exception:
                pass
        self._lufs_apply_job = self.after(50, self._flush_lufs_apply)
        # 停手 400ms 後才解除「本次拖曳」，下次拖曳會再推一筆新的 undo
        if getattr(self, "_lufs_drag_end_job", None):
            try:
                self.after_cancel(self._lufs_drag_end_job)
            except Exception:
                pass
        self._lufs_drag_end_job = self.after(400, self._end_lufs_drag)

    def _end_lufs_drag(self):
        self._lufs_drag_active = False
        self._lufs_drag_end_job = None

    def _flush_lufs_apply(self):
        self._lufs_apply_job = None
        v = getattr(self, "_pending_lufs_val", None)
        if v is not None:
            self.update_info_cards()
            self._apply_lufs_to_selection(v)

    def update_target_lufs(self, val, from_selection=False):
        if not self._updating_lufs:
            self._updating_lufs = True
            try:
                self.lufs_entry_var.set(f"{float(val):.1f}")
                self.target_lufs_var.set(float(val))
            finally:
                self._updating_lufs = False
        self.update_info_cards()

        if from_selection:
            return
        self._apply_lufs_to_selection(val)

    def update_info_cards(self):
        if hasattr(self, 'original_lufs_val') and self.original_lufs_val is not None:
            self.lbl_info_current.configure(text=f"{self.original_lufs_val:.1f}")
            target = self.target_lufs_var.get()
            self.lbl_info_target.configure(text=f"{target:.1f}")
            gain = target - self.original_lufs_val
            sign = "+" if gain > 0 else ""
            self.lbl_info_gain.configure(text=f"{sign}{gain:.1f}")
        else:
            self.lbl_info_current.configure(text="--")
            self.lbl_info_target.configure(text="--")
            self.lbl_info_gain.configure(text="--")

    def format_time(self, seconds):
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"

    def play_original(self):
        if not self.current_audio: return

        sd.stop()
        self.is_playing = False

        # 從頭開始播放（非從暫停處續播）→ Peak 表重新歸零即時累積，不要沿用上一次播放留下的峰值。
        if self.pause_position == 0:
            self.reset_peaks()

        current_ab = self.ab_listen_var.get()
        current_target = self.target_lufs_var.get()

        if not (hasattr(self, 'cached_audio_path') and self.cached_audio_path == getattr(self, 'current_file_path', None) and
                hasattr(self, 'cached_ab_state') and self.cached_ab_state == current_ab and
                hasattr(self, 'cached_target_lufs') and self.cached_target_lufs == current_target and
                hasattr(self, 'playback_data')):

            audio_to_play = self.current_audio
            try:
                samples = np.array(audio_to_play.get_array_of_samples())
                if audio_to_play.channels > 1:
                    samples = samples.reshape((-1, audio_to_play.channels))

                max_val = float(2 ** (8 * audio_to_play.sample_width - 1))
                samples_float = samples.astype(np.float32) / max_val

                if current_ab and self.original_lufs_val is not None:
                    gain = current_target - self.original_lufs_val
                    linear_gain = 10 ** (gain / 20.0)
                    samples_float *= linear_gain

                # 只有增益後真的會超過 0 dBFS 時才軟限幅，避免對未破表訊號做不必要的 tanh 失真
                # （與匯出鏈一致；A/B 試聽聽到的就會等於匯出結果）。
                peak = float(np.max(np.abs(samples_float))) if samples_float.size else 0.0
                if peak > 1.0:
                    samples_float = self.apply_soft_clipper(samples_float)
                self.playback_data = np.clip(samples_float, -1.0, 1.0)
                self.playback_sr = audio_to_play.frame_rate
                self.playback_duration = len(self.playback_data) / self.playback_sr

                self.cached_audio_path = getattr(self, 'current_file_path', None)
                self.cached_ab_state = current_ab
                self.cached_target_lufs = current_target
            except Exception as e:
                print(f"Playback data preparation error: {e}")
                return

        try:
            self.scrub_slider.configure(to=self.playback_duration)

            start_time = self.pause_position
            start_idx = int(start_time * self.playback_sr)

            if start_idx >= len(self.playback_data):
                start_idx = 0
                self.scrub_var.set(0)
                start_time = 0
                self.pause_position = 0

            # 迴圈播放不管從哪個時間點開始都要無縫：把餵給 sd.play 的陣列旋轉成
            # 『從目前位置接到結尾、再接回真正的開頭』，對這份旋轉後的陣列原生
            # loop=True 繞一圈，聽起來就等同『真正播到底→無縫接回 0』——不必等這輪播完
            # 再 stop/重新 sd.play，那才是造成從中段續播時循環會卡一下的真正原因。
            # （update_meters 換算真實播放時間時本來就是用 current_time % playback_duration
            # 取真正的軌道位置，跟音效卡裡實際旋轉過的陣列天然對得上，不需要另外換算。）
            self._native_loop_active = self.loop_var.get() and self.playback_duration > 0
            if self._native_loop_active and start_idx > 0:
                loop_buf = np.concatenate([self.playback_data[start_idx:], self.playback_data[:start_idx]])
            else:
                loop_buf = self.playback_data[start_idx:]
            sd.play(loop_buf, samplerate=self.playback_sr,
                    device=self.get_selected_device(), loop=self._native_loop_active)
            self.playback_start_sys_time = time.time() - start_time
            self.is_playing = True

            self.play_btn.configure(text="⏸", command=self.pause_playback)

            self._update_meter_id = getattr(self, '_update_meter_id', 0) + 1
            self.update_meters(self._update_meter_id)

        except Exception as e:
            print(f"Playback error: {e}")

    def fade_meters_to_zero(self, current_l=None, current_r=None):
        if self.is_playing: return

        if current_l is None:
            current_l = getattr(self, '_meter_val_l', 0)
            current_r = getattr(self, '_meter_val_r', 0)

        next_l = current_l * 0.75
        next_r = current_r * 0.75

        self._meter_val_l = next_l
        self._meter_val_r = next_r

        if hasattr(self, 'level_prog_L') and isinstance(self.level_prog_L, tk.Canvas):
            self.draw_meter_canvas(self.level_prog_L, next_l / 4)
            self.draw_meter_canvas(self.level_prog_R, next_r / 4)

        if next_l > 0.001 or next_r > 0.001:
            self.after(40, self.fade_meters_to_zero, next_l, next_r)
        else:
            self._meter_val_l = 0
            self._meter_val_r = 0

    def toggle_play_pause(self):
        if self.focus_get() == self.dir_tree:
            selected = self.dir_tree.selection()
            if selected:
                item = selected[0]
                path = self.tree_item_paths.get(item)
                if path and os.path.isfile(path):
                    self.preview_audio_file(path)
                    return

        if self.is_playing:
            self.pause_playback()
        elif self._just_paused:
            # 第三次按空白鍵：不是續播暫停位置，而是從頭重新播放。
            self._just_paused = False
            self.pause_position = 0
            self.scrub_var.set(0)
            self.play_original()
        else:
            self.play_original()

    def preview_audio_file(self, path):
        if self.is_playing or getattr(self, 'preview_playing', False):
            sd.stop()
            self.is_playing = False
            self.preview_playing = False
            if getattr(self, 'preview_path', None) == path:
                return

        try:
            audio = AudioSegment.from_file(path)
            samples = np.array(audio.get_array_of_samples())
            if audio.channels > 1:
                samples = samples.reshape((-1, audio.channels))
            max_val = float(2 ** (8 * audio.sample_width - 1))
            samples = samples.astype(np.float32) / max_val

            self.preview_samples_ref = samples
            sd.play(samples, samplerate=audio.frame_rate, device=self.get_selected_device())
            self.preview_playing = True
            self.preview_path = path
        except Exception as e:
            print(f"Preview error: {e}")

    def pause_playback(self):
        sd.stop()
        self.is_playing = False
        self.pause_position = time.time() - self.playback_start_sys_time
        self._just_paused = True
        self.fade_meters_to_zero()
        self.play_btn.configure(text="▶", command=self.play_original)

    def stop_playback(self):
        sd.stop()
        self.is_playing = False
        self.pause_position = 0
        self._just_paused = False
        self.scrub_var.set(0)
        dur = self.playback_duration if hasattr(self, 'playback_duration') else 0
        self.lbl_time.configure(text=f"00:00 / {self.format_time(dur)}")
        self.fade_meters_to_zero()
        self.play_btn.configure(text="▶", command=self.play_original)
        self.waveform_canvas.delete("playhead")

    def seek_forward(self):
        if not self.current_audio: return
        current = time.time() - self.playback_start_sys_time if self.is_playing else self.pause_position
        new_time = min(current + 5.0, self.current_audio.duration_seconds)
        self.pause_position = new_time
        self._just_paused = False
        self.scrub_var.set(new_time)
        if self.is_playing:
            self.jump_to(new_time)
        else:
            self.update_playhead_idle()

    def seek_backward(self):
        if not self.current_audio: return
        current = time.time() - self.playback_start_sys_time if self.is_playing else self.pause_position
        new_time = max(0, current - 5.0)
        self.pause_position = new_time
        self._just_paused = False
        self.scrub_var.set(new_time)
        if self.is_playing:
            self.jump_to(new_time)
        else:
            self.update_playhead_idle()

    def update_playhead_idle(self):
        dur = self.current_audio.duration_seconds if self.current_audio else 0
        self.lbl_time.configure(text=f"{self.format_time(self.pause_position)} / {self.format_time(dur)}")
        self.waveform_canvas.delete("playhead")
        if dur > 0:
            track_w = getattr(self, "_active_track_width", None) or self.waveform_canvas.winfo_width()
            x = int((self.pause_position / dur) * track_w)
            y0, y1 = self._playhead_yrange()
            self.waveform_canvas.create_line(x, y0, x, y1, fill="#00E5FF", width=2, tags="playhead")

    def _seek_current_track(self, event):
        """在目前主軌內依水平位置 seek（不切換播放對象）。"""
        if not self.current_audio: return
        # 以『目前主軌的實際像素寬』為基準（多選時每軌依時長等比縮放，寬度各不相同）
        track_w = getattr(self, "_active_track_width", None) or self.waveform_canvas.winfo_width()
        if track_w <= 1: return
        ratio = max(0.0, min(1.0, event.x / track_w))
        new_time = ratio * self.current_audio.duration_seconds
        self.pause_position = new_time
        self.scrub_var.set(new_time)
        if self.is_playing:
            self.jump_to(new_time)
        else:
            self.update_playhead_idle()

    def _on_waveform_scroll(self, event):
        """多選軌數超過可視高度時，滑鼠滾輪在波形區上下捲動查看其餘軌道。"""
        delta = getattr(event, "delta", 0)
        if delta == 0:
            num = getattr(event, "num", 0)
            delta = 120 if num == 4 else (-120 if num == 5 else 0)
        if delta:
            self.waveform_canvas.yview_scroll(-1 if delta > 0 else 1, "units")
        return "break"

    def on_waveform_click(self, event):
        if not self.current_audio: return
        self._seek_current_track(event)

    def on_waveform_drag(self, event):
        self._seek_current_track(event)

    def on_waveform_release(self, event):
        pass

    def _set_active_multi_track(self, entry, seek_ratio=0.0):
        """把指定的檔案設為目前可播放的主檔，播放桿/音量表/LUFS 控制都跟著切過去
        （Edit Window 內切換音軌時使用）。"""
        was_playing = self.is_playing
        sd.stop()
        self.is_playing = False

        self.current_file_path = entry["path"]
        self.current_audio = self._render_edited_audio(entry)
        self.playback_duration = self.current_audio.duration_seconds
        self.original_lufs_val = entry["lufs"] if isinstance(entry["lufs"], float) else None

        target_val = entry.get("target_lufs")
        if target_val is None:
            target_val = entry["lufs"] if isinstance(entry["lufs"], float) else -16.0
        self.target_lufs_var.set(target_val)
        self.update_target_lufs(target_val, from_selection=True)

        # 批次 ±Gain 滑桿/框格也跟著切到新主軌『目前已套用的總增益』（見 _refresh_gain_display）。
        self._refresh_gain_display()

        # 切檔等同重選 → 重置播放快取，讓 play_original 以新檔重建播放資料
        self._just_paused = False
        self.pause_position = max(0.0, min(1.0, seek_ratio)) * self.playback_duration
        try:
            self.scrub_slider.configure(to=self.playback_duration if self.playback_duration > 0 else 1)
        except Exception:
            pass
        self.scrub_var.set(self.pause_position)

        # 標題列顯示新的主檔名稱（保留「已選 N 個」）
        n = len([s for s in self.file_table.selection() if not self.file_table.tag_has("folder", s)])
        fname = os.path.basename(entry["path"])
        if n > 1:
            self.lbl_active_file.configure(text=f"{fname}　（已選 {n} 個）")
        else:
            self.lbl_active_file.configure(text=fname)
        self._schedule_wave_draw()

        if was_playing:
            self.play_original()  # 接續播放：以新檔從 seek 位置開始
        else:
            self.update_playhead_idle()

    def jump_to(self, new_time):
        sd.stop()
        start_idx = int(new_time * self.playback_sr)
        if hasattr(self, 'playback_data') and start_idx < len(self.playback_data):
            # 同 play_original：把陣列旋轉成『從 seek 點接到結尾、再接回真正開頭』，
            # 這樣不管從哪個時間點 seek，原生 loop=True 都能無縫繞回真正的開頭，
            # 不受 start_idx 是否為 0 影響。
            self._native_loop_active = self.loop_var.get() and self.playback_duration > 0
            if self._native_loop_active and start_idx > 0:
                loop_buf = np.concatenate([self.playback_data[start_idx:], self.playback_data[:start_idx]])
            else:
                loop_buf = self.playback_data[start_idx:]
            sd.play(loop_buf, samplerate=self.playback_sr,
                    device=self.get_selected_device(), loop=self._native_loop_active)
            self.playback_start_sys_time = time.time() - new_time

    def on_ab_toggle(self):
        if self.is_playing:
            current_pos = time.time() - self.playback_start_sys_time
            self.pause_position = current_pos
            sd.stop()
            self.play_original()

    def _ensure_ab_target(self):
        """調整 dB / LUFS 參數時，自動把上方「原始/目標」旋鈕切到『目標』，
        讓使用者調完當下直接播放就能聽到調整後的響度（更直覺）。"""
        try:
            if not self.ab_listen_var.get():
                self.ab_listen_var.set(True)
                # CTkSwitch 綁定同一個變數，set() 後外觀會跟著切到「目標」；
                # 若正在播放則即時改以目標響度續播。
                self.on_ab_toggle()
        except Exception:
            pass

    def toggle_loop(self):
        # 背景色／hover 色永遠跟其他播放控制鍵一樣（不整顆換色），只有圖示本身顏色隨開關狀態變化，
        # 這樣風格才會真的跟 ⏮▶⏹⏭ 一致，不會因為開啟循環就突然變成一顆風格不同的按鈕。
        # 播放中途才打開循環：這一輪播放不會是原生無縫循環（那要從頭播放才能套用），會照舊
        # 播完這一輪、在真正播到底時重新從頭播一次；從那之後每一輪才會是無縫循環。
        self.loop_var.set(not self.loop_var.get())
        self.btn_loop.configure(text_color=COLOR_CYAN if self.loop_var.get() else "white")

    def on_scrub(self, val):
        if self.current_audio:
            dur = self.current_audio.duration_seconds
            self.lbl_time.configure(text=f"{self.format_time(val)} / {self.format_time(dur)}")
            self.pause_position = float(val)
            self._just_paused = False
            if self.is_playing:
                self.jump_to(val)
            else:
                self.update_playhead_idle()

    def draw_meter_canvas(self, canvas, rms):
        canvas.delete("all")
        height = 150
        width = 28

        scales = [0, -6, -12, -18, -24, -30]
        m = 8  # 上下內縮，讓 0 與 -30 的刻度線不貼邊，可與置中的標籤對齊
        for v in scales:
            y = int(round(m + (abs(v) / 30.0) * (height - 2 * m)))
            canvas.create_line(0, y, width, y, fill="#1E1E1E", width=1)

        val = min(1.0, rms * 4)
        fill_height = int(height * val)

        if fill_height > 0:
            cyan_limit = int(height * 0.6)
            canvas.create_rectangle(0, height, width, max(height - min(fill_height, cyan_limit), 0), fill="#00E5FF", outline="")

            yellow_limit = int(height * 0.8)
            if fill_height > cyan_limit:
                canvas.create_rectangle(0, height - cyan_limit, width, max(height - min(fill_height, yellow_limit), 0), fill="#FFD700", outline="")

            if fill_height > yellow_limit:
                canvas.create_rectangle(0, height - yellow_limit, width, max(height - fill_height, 0), fill="#FF3B30", outline="")

    def update_meters(self, update_id=None):
        if not self.is_playing: return
        if update_id is not None and getattr(self, '_update_meter_id', None) != update_id:
            return

        current_time = time.time() - self.playback_start_sys_time
        idx = int(current_time * self.playback_sr)

        if idx >= len(self.playback_data):
            if self.loop_var.get() and getattr(self, "_native_loop_active", False) and self.playback_duration > 0:
                # sd.play(..., loop=True) 已經在音訊層無縫繞回開頭播放，這裡只要讓自己算的
                # 『已播放時間』跟著往回繞（可能不只繞一輪，例如視窗忙線délay很久才輪到這次
                # 檢查），UI（scrub bar／播放頭／peak meter 取樣區段）才會跟真正在響的聲音
                # 同步——絕對不能再呼叫 sd.play/sd.stop，那才是造成迴圈接點卡一下的真正原因。
                while idx >= len(self.playback_data):
                    self.playback_start_sys_time += self.playback_duration
                    current_time = time.time() - self.playback_start_sys_time
                    idx = int(current_time * self.playback_sr)
                self.pause_position = current_time
                self.scrub_var.set(current_time)
            elif self.loop_var.get():
                # 循環開啟但目前這段串流不是從頭播（例如從暫停處續播）而沒有原生 loop——
                # 照舊重新從頭播一次，播完這一次之後 start_idx 就會是 0，下一輪起才會是
                # 前面那條無縫路徑。
                self.pause_position = 0
                self.scrub_var.set(0)
                self.play_original()
                return
            else:
                self.stop_playback()
                return

        self.scrub_var.set(current_time)
        self.lbl_time.configure(text=f"{self.format_time(current_time)} / {self.format_time(self.playback_duration)}")

        self.waveform_canvas.delete("playhead")
        if self.playback_duration > 0:
            track_w = getattr(self, "_active_track_width", None) or self.waveform_canvas.winfo_width()
            playhead_x = int((current_time / self.playback_duration) * track_w)
            y0, y1 = self._playhead_yrange()
            self.waveform_canvas.create_line(playhead_x, y0, playhead_x, y1, fill="#00E5FF", width=2, tags="playhead")

        chunk_size = int(self.playback_sr * 0.05)
        chunk = self.playback_data[idx:idx+chunk_size]

        if len(chunk) > 0:
            if chunk.ndim == 1:
                rms = np.sqrt(np.mean(chunk**2)) if np.mean(chunk**2) > 0 else 0
                rms_l = rms_r = rms
            else:
                rms_l = np.sqrt(np.mean(chunk[:, 0]**2)) if np.mean(chunk[:, 0]**2) > 0 else 0
                rms_r = np.sqrt(np.mean(chunk[:, 1]**2)) if np.mean(chunk[:, 1]**2) > 0 else 0

            self._meter_val_l = min(1.0, rms_l * 4)
            self._meter_val_r = min(1.0, rms_r * 4)
            self.draw_meter_canvas(self.level_prog_L, rms_l)
            self.draw_meter_canvas(self.level_prog_R, rms_r)

            peak_db_l = 20 * np.log10(rms_l * 4 + 1e-10)
            peak_db_r = 20 * np.log10(rms_r * 4 + 1e-10)

            if peak_db_l > self.max_peak_L: self.max_peak_L = peak_db_l
            if peak_db_r > self.max_peak_R: self.max_peak_R = peak_db_r

            for peak_val, lbl in [(self.max_peak_L, self.lbl_peak_L), (self.max_peak_R, self.lbl_peak_R)]:
                if peak_val > -6: text_color = COLOR_RED
                elif peak_val > -12: text_color = "#FFD700"
                else: text_color = COLOR_CYAN
                disp_val = max(-99.9, peak_val)
                lbl.configure(text=f"{disp_val:5.1f}", text_color=text_color)

        self.after(50, lambda: self.update_meters(update_id))

    # ─────────────────────────────────────────────────────────
    # 目標 LUFS 直接輸入 / 重設
    # ─────────────────────────────────────────────────────────

    def _on_lufs_entry_commit(self, event=None):
        """Enter / FocusOut：解析輸入值，推 undo，套用到選取檔案。"""
        if self._updating_lufs:
            return
        try:
            raw = self.lufs_entry_var.get().replace(" LUFS", "").strip()
            val = float(raw)
            val = max(-40.0, min(-1.0, val))
        except ValueError:
            val = self.target_lufs_var.get()

        self._push_lufs_undo()
        self._ensure_ab_target()
        self.target_lufs_var.set(val)
        self.update_target_lufs(val)

    def _on_lufs_entry_return(self, event=None):
        """按 Enter：提交數值後主動移開焦點（見 _blur_param_entry），讓空白鍵播放立刻恢復作用。"""
        self._on_lufs_entry_commit(event)
        self._blur_param_entry()
        return "break"

    def _blur_param_entry(self):
        """把鍵盤焦點從批次參數輸入框移開，回到主視窗。
        CTkEntry 按 Enter 預設不會自動失焦；焦點若滯留在輸入框，下一次按空白鍵播放
        會被 _focus_in_text_entry() 判定成『還在打字』而擋下，使用者會覺得『打完按 Enter 就不能播放』。
        只在明確的 Return/KP_Enter 提交時呼叫，不能掛在 FocusOut，否則會在使用者切到下一個
        輸入框（例如 LUFS 打完換打批次 dB）時把焦點搶回來，導致打不進下一個欄位。"""
        try:
            self.focus_set()
        except Exception:
            pass

    def _push_lufs_undo(self):
        """將目前選取檔案的 target_lufs 快照推入 undo stack。"""
        selected = self.file_table.selection()
        paths = list(selected) if selected else (
            [self.current_file_path] if self.current_file_path else []
        )
        if not paths:
            return
        snapshot = [(p, next((e["target_lufs"] for e in self.audio_files if e["path"] == p), None))
                    for p in paths]
        self._undo_stack.append(("lufs_change", snapshot))
        if len(self._undo_stack) > 50:
            self._undo_stack = self._undo_stack[-50:]

    # ─────────────────────────────────────────────────────────
    # 批次 ±Gain
    # ─────────────────────────────────────────────────────────

    def _scroll_dir(self, event):
        """滑鼠滾輪事件 → 回傳 +1（上/增加）或 -1（下/減少），同時相容 macOS 與 X11。"""
        num = getattr(event, "num", None)
        if num == 4:
            return 1
        if num == 5:
            return -1
        return 1 if getattr(event, "delta", 0) > 0 else -1

    def _on_lufs_scroll(self, event):
        """滑鼠滾輪在目標 LUFS 數值上、上下滑動微調（每格 0.1，與拖曳滑桿一樣即時套用到選取檔案）。"""
        # 一連串滾動只推一筆 undo（在第一格動值之前），停手 500ms 後解除
        if not getattr(self, "_lufs_scroll_active", False):
            self._lufs_scroll_active = True
            self._push_lufs_undo()
        v = round(max(-40.0, min(-1.0, self.target_lufs_var.get() + 0.1 * self._scroll_dir(event))), 1)
        self._ensure_ab_target()
        self.target_lufs_var.set(v)
        self.update_target_lufs(v)
        if getattr(self, "_lufs_scroll_end_job", None):
            try:
                self.after_cancel(self._lufs_scroll_end_job)
            except Exception:
                pass
        self._lufs_scroll_end_job = self.after(500, lambda: setattr(self, "_lufs_scroll_active", False))
        return "break"

    def _on_gain_scroll(self, event):
        """滑鼠滾輪在批次 ±Gain 數值上微調（每格 0.1，夾在 ±20 dB）→ 即時套用到選取檔案。"""
        v = round(max(-20.0, min(20.0, self.gain_adj_var.get() + 0.1 * self._scroll_dir(event))), 1)
        self._ensure_ab_target()
        self.gain_adj_var.set(v)
        self.gain_entry_var.set(f"{v:.1f}")
        self._ensure_gain_baseline(v)
        self._apply_gain_offset(v)
        return "break"

    def _lufs_entry_drag_delta(self, delta):
        """按住 Target 數值垂直拖曳（見 _bind_drag_scrub）：跟滾輪微調共用同一套
        『連續操作只推一筆 undo』節流機制（_lufs_scroll_active／_lufs_scroll_end_job）。"""
        if not getattr(self, "_lufs_scroll_active", False):
            self._lufs_scroll_active = True
            self._push_lufs_undo()
        v = round(max(-40.0, min(-1.0, self.target_lufs_var.get() + delta)), 1)
        self._ensure_ab_target()
        self.target_lufs_var.set(v)
        self.update_target_lufs(v)
        if getattr(self, "_lufs_scroll_end_job", None):
            try:
                self.after_cancel(self._lufs_scroll_end_job)
            except Exception:
                pass
        self._lufs_scroll_end_job = self.after(500, lambda: setattr(self, "_lufs_scroll_active", False))

    def _gain_entry_drag_delta(self, delta):
        """按住 Gain 數值垂直拖曳：跟批次 ±Gain 滑桿共用同一套 baseline／apply 邏輯。"""
        v = round(max(-20.0, min(20.0, self.gain_adj_var.get() + delta)), 1)
        self._ensure_ab_target()
        self.gain_adj_var.set(v)
        self.gain_entry_var.set(f"{v:.1f}")
        self._ensure_gain_baseline(v)
        self._apply_gain_offset(v)

    def _on_gain_slider(self, val):
        """批次 ±Gain 滑桿拖曳：即時把選取檔案的目標 LUFS 平移（相對 baseline，不需按套用）；
        經過 0 附近時吸附歸零（阻尼感），方便快速歸零並固定在 0。重活去抖動讓拖曳順暢。"""
        val = float(val)
        self._ensure_ab_target()
        # 0 附近阻尼：±0.5 dB 內吸附到 0（拖過去會明顯「卡」一下並固定在 0，方便快速歸零）
        if abs(val) < 0.5:
            val = 0.0
            if abs(self.gain_adj_var.get()) > 1e-9:
                self.gain_adj_var.set(0.0)
        self.gain_entry_var.set(f"{val:.1f}")
        self._ensure_gain_baseline(val)
        self._pending_gain_val = val
        if getattr(self, "_gain_apply_job", None):
            try:
                self.after_cancel(self._gain_apply_job)
            except Exception:
                pass
        self._gain_apply_job = self.after(40, self._flush_gain_apply)

    def _flush_gain_apply(self):
        self._gain_apply_job = None
        self._apply_gain_offset(getattr(self, "_pending_gain_val", 0.0))

    def _on_gain_entry_commit(self, event=None):
        """批次 ±Gain 直接輸入 → 夾在 ±20 dB、同步滑桿並即時套用。"""
        try:
            v = float(self.gain_entry_var.get().replace("dB", "").strip())
        except (ValueError, AttributeError):
            v = self.gain_adj_var.get()
        v = max(-20.0, min(20.0, v))
        self._ensure_ab_target()
        self.gain_adj_var.set(v)
        self.gain_entry_var.set(f"{v:.1f}")
        self._ensure_gain_baseline(v)
        self._apply_gain_offset(v)

    def _on_gain_entry_return(self, event=None):
        """按 Enter：提交數值後主動移開焦點（見 _blur_param_entry），讓空白鍵播放立刻恢復作用。"""
        self._on_gain_entry_commit(event)
        self._blur_param_entry()
        return "break"

    def _refresh_gain_display(self):
        """重新計算並顯示目前選取檔案『目前已套用的總增益』(目標 LUFS − 原始 LUFS)，
        同時把這個值記為『歇息基準』(_gain_display_at_rest)，供之後拖曳批次 ±Gain 時
        計算『這次拖曳的相對位移』用（見 _capture_gain_baseline）。這樣選取檔案（或切換
        多選中的主軌、套用/重設批次 Gain 後）時，滑桿/框格顯示的是已套用的實際總增益，
        不會每次都歸零，之前調過的批次 dB（例如 -3dB）換選別的音檔再點回來時記錄還在。
        多選時若各檔案的總增益不一致，框格顯示「--」避免誤導成統一數字（滑桿位置跟主檔走）。"""
        if not hasattr(self, "gain_adj_var"):
            return
        file_sel = [s for s in self.file_table.selection() if not self.file_table.tag_has("folder", s)]
        if not file_sel and getattr(self, "current_file_path", None):
            file_sel = [self.current_file_path]
        by_path = {it["path"]: it for it in self.audio_files}
        gains = []
        for p in file_sel:
            e = by_path.get(p)
            if e and isinstance(e.get("target_lufs"), float) and isinstance(e.get("lufs"), float):
                gains.append(round(e["target_lufs"] - e["lufs"], 1))
        main_entry = by_path.get(getattr(self, "current_file_path", None))
        main_gain = 0.0
        if main_entry and isinstance(main_entry.get("target_lufs"), float) and isinstance(main_entry.get("lufs"), float):
            main_gain = round(main_entry["target_lufs"] - main_entry["lufs"], 1)
        main_gain = max(-20.0, min(20.0, main_gain))
        uniform = bool(gains) and len(gains) == len(file_sel) and all(abs(g - gains[0]) < 0.05 for g in gains)
        self.gain_adj_var.set(main_gain)
        self.gain_entry_var.set(f"{main_gain:.1f}" if uniform else "--")
        self._gain_display_at_rest = main_gain
        self._gain_display_uniform = uniform

    def _capture_gain_baseline(self):
        """以目前選取（或主檔）的目標 LUFS 當作批次平移的基準，並推一筆 undo（可 Cmd+Z 還原）。
        基準會先扣掉目前的『歇息值』(_gain_display_at_rest)：滑桿現在預設顯示的是檔案已套用的
        總增益（可能不是 0，例如先前調過 -3dB），若不扣掉，從這個非 0 的既有值繼續拖曳，會把既
        有增益重複疊加進去（見 _apply_gain_offset 的 base+offset 公式）。"""
        sel = [p for p in self.file_table.selection() if not self.file_table.tag_has("folder", p)]
        if not sel and getattr(self, "current_file_path", None):
            sel = [self.current_file_path]
        rest = getattr(self, "_gain_display_at_rest", 0.0)
        self._gain_baseline = {}
        snapshot = []
        for p in sel:
            e = next((it for it in self.audio_files if it["path"] == p), None)
            if e:
                base = e["target_lufs"] if isinstance(e.get("target_lufs"), float) else None
                self._gain_baseline[p] = (base - rest) if base is not None else None
                snapshot.append((p, base))
        if snapshot:
            self._undo_stack.append(("lufs_change", snapshot))
            if len(self._undo_stack) > 50:
                self._undo_stack = self._undo_stack[-50:]

    def _ensure_gain_baseline(self, offset):
        """批次值偏離『歇息基準』的瞬間鎖定目前目標值為 baseline；回到歇息基準時解除。
        如此拖曳量到的是『這次拖曳的相對位移』（不會累加/不會把既有總增益重複疊加），
        且不受先前用 LUFS 滑桿改過的值影響。"""
        rest = getattr(self, "_gain_display_at_rest", 0.0)
        if abs(offset - rest) < 1e-9:
            self._gain_active = False
            return
        if not getattr(self, "_gain_active", False):
            self._capture_gain_baseline()
            self._gain_active = True

    def _apply_gain_offset(self, offset):
        """把選取檔案的目標 LUFS 設成 baseline + offset（即時批次平移）。"""
        baseline = getattr(self, "_gain_baseline", None)
        if not baseline:
            return
        for path, base in baseline.items():
            if not isinstance(base, float):
                continue
            entry = next((e for e in self.audio_files if e["path"] == path), None)
            if entry:
                new_val = max(-40.0, min(-1.0, round(base + offset, 1)))
                entry["target_lufs"] = new_val
                if self.file_table.exists(path):
                    self.file_table.set(path, "目標 LUFS", f"{new_val:.1f} LUFS")
                    self._sync_true_peak_cells(self.file_table, path, entry)
        cur = next((e for e in self.audio_files if e["path"] == getattr(self, "current_file_path", None)), None)
        if cur and isinstance(cur.get("target_lufs"), float):
            # 同步滑桿位置與框格數字（原本只有 target_lufs_var/滑桿會動，lufs_entry_var/框格文字
            # 沒被同步更新，導致滑桿移動了但框格內數字沒跟著變）。
            self.update_target_lufs(cur["target_lufs"], from_selection=True)
            if len(baseline) > 1:
                # 多選時批次 ±Gain 是「各自相對位移」，調整後每個檔案的目標 LUFS 各不相同，
                # 框格顯示單一數字會誤導成大家都一樣 → 改顯示「--」。
                self.lufs_entry_var.set("--")
        self._schedule_autosave()
        self._schedule_wave_draw()  # 批次 ±Gain 改變 → 波形即時依新增益重畫

    # ─────────────────────────────────────────────────────────
    # 全選（Cmd+A）
    # ─────────────────────────────────────────────────────────

    def _handle_select_all_shortcut(self, event=None):
        if self._focus_in_text_entry():
            return None
        focused = self.focus_get()
        widget = getattr(event, "widget", None)

        for ws in self.workspaces:
            if focused == ws.dir_tree or widget == ws.dir_tree:
                return self._select_all_tree(ws.dir_tree)
            if focused == ws.file_table or widget == ws.file_table:
                return self._select_all_files_for_table(ws.file_table)

        return self._select_all()

    def _select_all_files(self, event=None):
        """中間工作區表格的 Cmd/Ctrl+A：選取該表格內所有檔案節點。
        直接綁在表格 widget 上、回傳 "break" 攔截，確保不被 ttk.Treeview 的 class 綁定吃掉。"""
        table = event.widget if (event is not None and hasattr(event, "widget")) else self.file_table
        return self._select_all_files_for_table(table)

    def _select_all_files_for_table(self, table):
        try:
            items = self._iter_file_iids(table)
        except Exception:
            items = []
        if items:
            table.selection_set(items)
            table.focus_set()
        return "break"

    def _select_all(self):
        focused = self.focus_get()
        for ws in self.workspaces:
            if focused == ws.dir_tree:
                all_items = self._get_all_tree_items(ws.dir_tree)
                if all_items:
                    ws.dir_tree.selection_set(all_items)
                return "break"
        items = self._iter_file_iids()
        if items:
            self.file_table.selection_set(items)
            self.file_table.focus_set()
        return "break"

    def _get_all_tree_items(self, tree, parent=""):
        items = list(tree.get_children(parent))
        for item in list(items):
            items.extend(self._get_all_tree_items(tree, item))
        return items

    def _select_all_tree(self, tree):
        """左側資料夾樹的 Cmd/Ctrl+A：選取整棵樹所有節點，回傳 'break' 攔截。"""
        items = self._get_all_tree_items(tree)
        if items:
            tree.selection_set(items)
            tree.focus_set()
        return "break"

    # ─────────────────────────────────────────────────────────
    # Undo（Cmd+Z）
    # ─────────────────────────────────────────────────────────

    def _undo(self):
        if not self._undo_stack:
            return
        action_type, payload = self._undo_stack.pop()
        if action_type == "remove_files":
            self._undo_remove_files(payload)
            return
        snapshot = payload
        for path, old_target in snapshot:
            entry = next((e for e in self.audio_files if e["path"] == path), None)
            if entry and old_target is not None:
                entry["target_lufs"] = old_target
                if self.file_table.exists(path):
                    self.file_table.set(path, "目標 LUFS", f"{old_target:.1f} LUFS")
                    self._sync_true_peak_cells(self.file_table, path, entry)
        if self.current_file_path:
            cur = next((e for e in self.audio_files if e["path"] == self.current_file_path), None)
            if cur and isinstance(cur.get("target_lufs"), float):
                self.target_lufs_var.set(cur["target_lufs"])
                self.update_target_lufs(cur["target_lufs"], from_selection=True)
        self._schedule_autosave()

    def _undo_remove_files(self, payload):
        """復原 remove_selected_files 移除的檔案：把 entry 資料與表格列都還原回原本的工作區。"""
        ws, removed_entries = payload
        # 用 is 比對而非 in／==：Workspace 是 @dataclass，預設會逐欄位比較，
        # 若剛好跟另一個工作區欄位值相同會誤判成「還在」，只有物件身分才可靠。
        if not any(w is ws for w in self.workspaces):
            return  # 該工作區本身已經被關掉，沒地方可以還原了
        table = ws.file_table
        for entry in removed_entries:
            path = entry["path"]
            if any(f["path"] == path for f in ws.audio_files):
                continue  # 同路徑檔案已存在（例如又重新拖入過一次），避免重複
            ws.audio_files.append(entry)
            if table is None:
                continue
            orig_tp_disp, target_tp_disp = self._true_peak_displays(entry)
            lufs = entry.get("lufs")
            target_lufs = entry.get("target_lufs")
            lufs_disp = f"{lufs:.1f} LUFS" if isinstance(lufs, float) else (lufs or "--")
            target_disp = f"{target_lufs:.1f} LUFS" if isinstance(target_lufs, float) else "--"
            self._insert_file_row_into(table, path, entry.get("export", True),
                                       entry.get("duration", "--:--"), entry.get("status", "🟢 就緒"),
                                       lufs_disp, target_disp, orig_tp_disp, target_tp_disp)
            self._sync_folder_check(table, path)
        if table is not None:
            self._prune_empty_folder_nodes(table)
            self._update_empty_hint(ws)
        self.check_export_ready()
        self._schedule_autosave()

    def check_export_ready(self):
        if getattr(self, "_exporting", False):
            return  # 匯出中按鈕由匯出流程接管（進度/取消），不在此覆寫
        ws = self.workspaces[self.active_ws_idx]
        if ws.audio_files and self.export_folder:
            # 按鈕上直接顯示「這次會匯出幾個」（就緒且勾選），勾選/分析狀態變動即時更新
            n = self._ready_export_count(ws)
            self.btn_export.configure(state="normal", text_color="white",
                                      text=(f"↗ 匯出 ({n})" if n else "↗ 匯出音檔"))
        else:
            self.btn_export.configure(state="disabled", text_color="gray", text="↗ 匯出音檔")

    def _update_export_path_label(self):
        """顯示完整輸出路徑（不再截斷，避免路徑名稱被吃掉）；有路徑時可點擊在 Finder 開啟。"""
        try:
            if self.export_folder:
                self.lbl_export_path.configure(text="📂 " + self.export_folder, cursor="pointinghand")
            else:
                self.lbl_export_path.configure(text="輸出:/尚未設定", cursor="arrow")
        except Exception:
            pass

    def _open_export_folder(self, event=None):
        """在 Finder 開啟輸出資料夾（點路徑標籤觸發）。"""
        if self.export_folder and os.path.isdir(self.export_folder):
            try:
                subprocess.Popen(["open", self.export_folder])
            except Exception:
                pass

    def select_export_folder(self):
        folder_path = filedialog.askdirectory(title="選擇輸出資料夾")
        if folder_path:
            self.export_folder = folder_path
            self._update_export_path_label()
            self.check_export_ready()
            self._schedule_autosave()

    def start_export_thread(self):
        if not self.export_folder: return
        if getattr(self, "_exporting", False): return  # 已在匯出中

        # 有多個工作區時，匯出只處理「目前這個工作區」勾選的檔案，不再跳出工作區選擇視窗。
        ws = self.workspaces[self.active_ws_idx]
        ready_count = self._ready_export_count(ws)
        if ready_count == 0:
            # 以前這裡靜默 return → 按鈕亮著、按了卻毫無反應。改成講清楚原因。
            messagebox.showinfo(
                "沒有可匯出的檔案",
                "目前工作區沒有任何『🟢 就緒且已勾選 ✅』的檔案。\n\n"
                "可能原因：\n"
                "• 檔案還在分析中（🟡 載入中）\n"
                "• 分析失敗或檔案離線（🔴）\n"
                "• 左側勾選欄全部被取消（⬜）",
                parent=self)
            return

        fmt = self.format_menu.get()
        sr  = self.sr_menu.get()
        br  = self.bit_menu.get()

        # 匯出前一律用同一個確認視窗，同時告知即將輸出的格式，不會再因為格式是
        # 「Original」就多跳一個預設按「否」的警告視窗把匯出擋掉。
        fmt_desc = "維持原始格式（不轉檔，僅調整響度）" if fmt == "Original" else fmt
        go_on = messagebox.askyesno(
            "確認匯出",
            f"確定匯出 {ready_count} 顆音檔嗎？\n\n輸出格式：{fmt_desc}",
            parent=self)
        if not go_on:
            return

        custom_name = self._sanitize_export_folder_name(self.folder_name_entry.get())
        export_jobs = self._build_export_jobs([ws], custom_name)
        if not export_jobs:
            return

        export_folder = self.export_folder
        # 匯出期間按鈕變成「取消」＋顯示進度（第 N/共 M 個）；_exporting 期間 check_export_ready 不覆寫按鈕
        self._exporting = True
        self._export_cancel = False
        total = sum(len(j["entries"]) for j in export_jobs)
        self.btn_export.configure(state="normal", text=f"✕ 取消 (0/{total})",
                                  command=self._cancel_export)
        threading.Thread(target=self.export_process,
                         args=(fmt, export_jobs, export_folder, sr, br),
                         daemon=True).start()

    def _cancel_export(self):
        """使用者按下「取消匯出」：設旗標，匯出執行緒在當前檔案處理完後停止（不會留半成品，
        因為每個檔都是先寫 tmp 再 os.replace）。"""
        self._export_cancel = True
        try:
            self.btn_export.configure(state="disabled", text="⏳ 正在停止…")
        except Exception:
            pass

    def _update_export_progress(self, done, total):
        """（主執行緒）更新匯出進度到按鈕文字。取消中就不再覆寫「正在停止…」。"""
        if getattr(self, "_exporting", False) and not getattr(self, "_export_cancel", False):
            try:
                self.btn_export.configure(text=f"✕ 取消 ({done}/{total})")
            except Exception:
                pass

    def _sanitize_export_folder_name(self, custom_name):
        custom_name = (custom_name or "").strip()
        if custom_name:
            for ch in '/\\:<>"|?*':
                custom_name = custom_name.replace(ch, "_")
            custom_name = custom_name.strip(". ")
        return custom_name

    def _build_export_jobs(self, workspaces, custom_name):
        """在主執行緒凍結匯出資料，避免背景 thread 直接讀 Tk widget/tree state。"""
        # Edit Window 尚未關閉時，最新的 Region/Fade 還在視窗自己的物件模型裡；先同步回
        # entry，並在下方把 dict 複製進匯出 job，背景執行緒才會拿到真正最新的非破壞性編輯。
        self._sync_open_edit_window_entries()

        multi = len(workspaces) > 1
        jobs = []
        for ws in workspaces:
            if multi:
                ws_suffix = "_" + ws.name.replace(" ", "_")
                folder_base = (custom_name + ws_suffix) if custom_name else ws.name.replace(" ", "_")
            else:
                folder_base = custom_name if custom_name else ws.name.replace(" ", "_")

            entries = []
            for entry in ws.audio_files:
                if entry["status"] != "🟢 就緒" or entry["audio"] is None:
                    continue
                if not entry.get("export", True):
                    continue
                saved_regions = entry.get("edit_regions")
                entries.append({
                    "name": entry["name"],
                    "path": entry["path"],
                    "audio": entry["audio"],
                    "target_lufs": entry.get("target_lufs"),
                    "lufs": entry.get("lufs"),
                    "source_bit_depth": entry.get("source_bit_depth"),
                    "edit_regions": (
                        None if saved_regions is None
                        else [dict(d) for d in saved_regions]
                    ),
                    "subpath": self._export_subpath_for(ws, entry["path"]),
                })
            if entries:
                jobs.append({"folder_base": folder_base, "entries": entries})
        return jobs

    def _export_subpath_for(self, ws, file_path):
        """回傳此檔在輸出資料夾底下應放的『相對子資料夾』：
        保留當初 Import 進來的最上層資料夾名稱（例如 BaseGame）及其內部結構；
        找不到對應的匯入根資料夾時，退回用檔案母資料夾名稱當作一層。"""
        try:
            ap = os.path.abspath(file_path)
        except Exception:
            return ""
        roots = []
        if ws.dir_tree is not None:
            for iid in ws.dir_tree.get_children(""):
                p = ws.tree_item_paths.get(iid)
                if p and os.path.isdir(p):
                    roots.append(os.path.abspath(p))
        # 取最深（最長）的匹配根，避免巢狀匯入時對應到外層
        best = None
        for r in roots:
            try:
                if os.path.commonpath([r, ap]) == r and (best is None or len(r) > len(best)):
                    best = r
            except ValueError:
                continue
        if best is not None:
            root_name = os.path.basename(best) or best
            rel_dir = os.path.dirname(os.path.relpath(ap, best))
            return os.path.join(root_name, rel_dir) if rel_dir and rel_dir != "." else root_name
        # 後備：用母資料夾名稱當作一層
        return os.path.basename(os.path.dirname(ap)) or ""

    def export_process(self, fmt, export_jobs, export_folder, sr="Original", br="Original"):
        successes = 0
        failures = []          # (檔名, 失敗原因)
        used_paths = set()     # 本次匯出已用過的輸出路徑，避免不同來源同名檔互相覆蓋
        done = 0
        total = sum(len(j["entries"]) for j in export_jobs)
        try:
            for job in export_jobs:
                if getattr(self, "_export_cancel", False):
                    break
                target_dir = os.path.join(export_folder, job["folder_base"])
                try:
                    os.makedirs(target_dir, exist_ok=True)
                except Exception as e:
                    # 輸出資料夾無法建立（碟被卸載/權限不足）→ 此工作區所有待匯出檔記為失敗，續下一個
                    for entry in job["entries"]:
                        failures.append((entry["name"], f"無法建立輸出資料夾：{e}"))
                    continue

                for entry in job["entries"]:
                    if getattr(self, "_export_cancel", False):
                        break
                    done += 1
                    self._enqueue_ui(self._update_export_progress, done, total)
                    save_path = None
                    save_key = None
                    tmp_out = None
                    try:
                        # ── Step 1: 套用 LUFS 增益（必要時才軟限幅）+ 實測收斂 + 安全轉回整數 ──
                        target_lufs = entry.get("target_lufs")
                        measured_lufs = entry.get("lufs")
                        if not isinstance(measured_lufs, (int, float)):
                            failures.append((entry["name"], "尚未完成 LUFS 分析，略過匯出"))
                            continue
                        if not isinstance(target_lufs, (int, float)):
                            target_lufs = measured_lufs
                        gain_db = target_lufs - measured_lufs
                        linear_gain = 10 ** (gain_db / 20.0)

                        # Edit Window 裡剪過/貼過/加過淡入淡出的檔案 → 先依非破壞性編輯記錄
                        # 重新組出真正要匯出的音訊，再照原本流程套 LUFS 增益。沒編輯過就是原始檔。
                        base_audio = self._render_edited_audio(entry)
                        samples = np.array(base_audio.get_array_of_samples())
                        max_val = float(2 ** (8 * base_audio.sample_width - 1))

                        samples_float = (samples.astype(np.float64) / max_val) * linear_gain
                        # 只有增益後真的會超過 0 dBFS 時才軟限幅，避免對未破表訊號做不必要的 tanh 失真
                        # （增益=0 的預設情況下 → 完全線性、不改音色）
                        peak = float(np.max(np.abs(samples_float))) if samples_float.size else 0.0
                        if peak > 1.0:
                            samples_float = self.apply_soft_clipper(samples_float)
                        samples_float = np.clip(samples_float, -1.0, 1.0)

                        # 純線性增益在數學上會精準命中目標，但軟限幅是非線性壓縮，會讓「實際響度」
                        # 偏離線性算出來的理論值（曾實測 export→重新匯入再量，誤差最多到 1 LUFS）。
                        # 這裡量多少校正多少：把處理過的樣本『實際』餵回同一顆量測器重新量，
                        # 跟目標還差多少就再疊一次修正增益，最多 6 輪收斂到 0.3 LU 內（使用者要求 0.5 內；
                        # 極端案例如「本來就接近滿幅還要求再推更響」，tanh 壓縮會讓每輪殘差以近似等比收斂，
                        # 實測 3 輪不夠、6 輪足夠壓到 0.5 內，同時仍是可接受的額外運算量）。
                        try:
                            _meter = pyln.Meter(base_audio.frame_rate, block_size=0.400)
                        except Exception:
                            _meter = None

                        def _measure_processed(flat):
                            arr = flat
                            ch = base_audio.channels
                            if ch > 1:
                                arr = arr.reshape((-1, ch))
                                if ch > 5:
                                    arr = arr.mean(axis=1)  # >5 聲道 pyloudnorm 量不了，降混單聲道近似供收斂參考
                            n = arr.shape[0]
                            if n / base_audio.frame_rate < 0.4:
                                pad = int(np.ceil(0.4 * base_audio.frame_rate)) - n
                                arr = np.pad(arr, (0, pad), mode="constant") if arr.ndim == 1 else \
                                      np.pad(arr, ((0, pad), (0, 0)), mode="constant")
                            return _meter.integrated_loudness(arr)

                        if _meter is not None:
                            for _ in range(6):
                                try:
                                    actual_lufs = _measure_processed(samples_float)
                                except Exception:
                                    break
                                if not np.isfinite(actual_lufs):
                                    break
                                residual = target_lufs - actual_lufs
                                if abs(residual) <= 0.3:
                                    break
                                samples_float = samples_float * (10 ** (residual / 20.0))
                                peak = float(np.max(np.abs(samples_float))) if samples_float.size else 0.0
                                if peak > 1.0:
                                    samples_float = self.apply_soft_clipper(samples_float)
                                samples_float = np.clip(samples_float, -1.0, 1.0)
                        # 轉回整數：round + clip 到 dtype 合法範圍，避免 +1.0×max_val 溢位回繞成
                        # -滿幅（正峰瞬跳負滿幅＝爆音 click）。
                        clipped_samples_int = np.clip(
                            np.rint(samples_float * max_val), -max_val, max_val - 1
                        ).astype(samples.dtype)
                        output_audio = base_audio._spawn(clipped_samples_int.tobytes())

                        # ── Step 2: 決定輸出副檔名與路徑 ──
                        original_ext = os.path.splitext(entry["name"])[1].lower()
                        save_ext = original_ext if fmt == "Original" else "." + fmt.lower()
                        save_name = os.path.splitext(entry["name"])[0] + save_ext
                        # 保留當初 Import 進來的最上層資料夾名稱（例如 BaseGame）為一層子資料夾
                        sub = entry.get("subpath", "")
                        out_dir = os.path.join(target_dir, sub) if sub else target_dir
                        os.makedirs(out_dir, exist_ok=True)
                        save_path = os.path.join(out_dir, save_name)
                        # 不同來源同名檔落在同一輸出夾 → 自動加 _1/_2，避免無聲覆蓋（僅就本次匯出去重，
                        # 不影響重新匯出時覆寫舊輸出的既有行為）。
                        save_key = _output_path_key(save_path)
                        if save_key in used_paths:
                            stem, ext = os.path.splitext(save_path)
                            n = 1
                            while _output_path_key(f"{stem}_{n}{ext}") in used_paths:
                                n += 1
                            save_path = f"{stem}_{n}{ext}"
                            save_key = _output_path_key(save_path)
                        used_paths.add(save_key)

                        fmt_key = original_ext.lstrip(".") if fmt.lower() == "original" else fmt.lower()

                        # 使用者在「位元深度」選單明確指定（非 Original）時，先把實際樣本轉成該深度。
                        # ffmpeg（經由底下的暫存 wav）與沒有 ffmpeg 時的 pydub fallback 都是依樣本本身
                        # 的精度輸出，這點對 FLAC 尤其重要：FLAC 只有單一 codec 名稱（沒有像 wav/aiff
                        # 那樣的『pcm_s16le/pcm_s24le』深度別名可選），一定要讓餵進去的樣本本身就是目標
                        # 深度，輸出才會真的是那個深度，不能只靠改 codec 名稱。
                        chosen_bits = None
                        if fmt_key in LOSSLESS_FORMATS and br != "Original":
                            try:
                                chosen_bits = int(br)
                            except (TypeError, ValueError):
                                chosen_bits = None
                            if chosen_bits not in (8, 16, 24, 32):
                                chosen_bits = None
                        if chosen_bits is not None:
                            output_audio = output_audio.set_sample_width(chosen_bits // 8)

                        use_ffmpeg = bool(FFMPEG_BIN)
                        if use_ffmpeg:
                            # ── Step 3a: FFmpeg 路徑 → 存暫存 WAV → FFmpeg 轉換 ──
                            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav")
                            os.close(tmp_fd)
                            try:
                                output_audio.export(tmp_path, format="wav")
                                if chosen_bits is not None:
                                    source_bits = chosen_bits
                                else:
                                    source_bits = entry.get("source_bit_depth")
                                    try:
                                        source_bits = int(source_bits) if source_bits is not None else None
                                    except (TypeError, ValueError):
                                        source_bits = None
                                codec = _pcm_codec_for(fmt_key, output_audio.sample_width, source_bits)
                                container = CONTAINER_MAP.get(fmt_key, fmt_key)
                                if AVAILABLE_ENCODERS and codec not in AVAILABLE_ENCODERS:
                                    used_paths.discard(save_key)
                                    failures.append((entry["name"], f"此 FFmpeg 缺少 {codec} 編碼器"))
                                    continue

                                cmd = [FFMPEG_BIN, "-y", "-i", tmp_path]
                                if sr != "Original":
                                    cmd += ["-ar", str(sr)]
                                if fmt_key in LOSSY_FORMATS and br != "Original":
                                    cmd += ["-b:a", f"{br}k"]
                                if codec == "vorbis":
                                    # FFmpeg 原生 vorbis encoder 只支援 2 聲道；libvorbis 不在時自動轉 stereo。
                                    cmd += ["-ac", "2"]
                                cmd += ["-codec:a", codec]
                                if codec == "vorbis":
                                    cmd += ["-strict", "-2"]   # 原生 vorbis（libvorbis 不存在時的退路）需要實驗性旗標
                                tmp_out = _make_temp_output_path(save_path)
                                cmd += ["-f", container, tmp_out]
                                result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)

                                ok = (result.returncode == 0 and os.path.exists(tmp_out)
                                      and os.path.getsize(tmp_out) > 0)
                                if ok:
                                    os.replace(tmp_out, save_path)
                                    tmp_out = None
                                    successes += 1
                                else:
                                    used_paths.discard(save_key)
                                    err_lines = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()
                                    reason = err_lines[-1] if err_lines else f"ffmpeg 退出碼 {result.returncode}"
                                    failures.append((entry["name"], reason))
                            finally:
                                try:
                                    os.remove(tmp_path)
                                except Exception:
                                    pass
                                if tmp_out:
                                    try:
                                        os.remove(tmp_out)
                                    except Exception:
                                        pass
                        else:
                            # ── Step 3b: Fallback → pydub 直接匯出 ──
                            fmt_tag = save_ext.replace(".", "").lower()
                            fmt_tag = {"aif": "aiff", "m4a": "ipod", "aac": "adts", "wma": "asf"}.get(fmt_tag, fmt_tag)
                            if sr != "Original":
                                output_audio = output_audio.set_frame_rate(int(sr))
                            tmp_out = _make_temp_output_path(save_path)
                            output_audio.export(tmp_out, format=fmt_tag)
                            if os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 0:
                                os.replace(tmp_out, save_path)
                                tmp_out = None
                                successes += 1
                            else:
                                used_paths.discard(save_key)
                                failures.append((entry["name"], "輸出檔未產生（pydub/ffmpeg 失敗）"))

                    except Exception as e:
                        traceback.print_exc()
                        if save_key:
                            used_paths.discard(save_key)
                        if tmp_out:
                            try:
                                os.remove(tmp_out)
                            except Exception:
                                pass
                        failures.append((entry["name"], str(e)))
        finally:
            # 不論成功/失敗/取消/例外，都在主執行緒收尾：解鎖按鈕並據實回報，避免按鈕永久卡在「匯出中」
            self._enqueue_ui(self._finish_export, successes, list(failures),
                             bool(getattr(self, "_export_cancel", False)))

    def _finish_export(self, successes, failures, cancelled=False):
        """匯出收尾（於主執行緒執行）：依成功/失敗/取消更新按鈕，有失敗則彈窗列出，不再一律報成功。"""
        self._exporting = False
        try:
            self.btn_export.configure(state="normal", command=self.start_export_thread)
            if cancelled:
                self.btn_export.configure(text=f"⛔ 已取消（完成 {successes}）", text_color="#FF9F0A")
            elif failures and successes == 0:
                self.btn_export.configure(text="⚠ 匯出失敗", text_color=COLOR_RED)
            elif failures:
                self.btn_export.configure(text=f"⚠ 部分完成（失敗 {len(failures)}）", text_color="#FF9F0A")
            else:
                self.btn_export.configure(text="✅ 匯出完成", text_color="#00E5FF")
            # 幾秒後還原成一般狀態（check_export_ready 會帶回就緒計數文字）
            self.after(3500, lambda: (self.btn_export.configure(text_color="white"),
                                      self.check_export_ready()))
        except Exception:
            pass

        if failures:
            detail = "\n".join(f"• {n}：{r}" for n, r in failures[:15])
            more = f"\n…還有 {len(failures) - 15} 個" if len(failures) > 15 else ""
            if successes == 0:
                messagebox.showerror(
                    "匯出失敗",
                    f"全部 {len(failures)} 個檔案都沒有成功匯出：\n\n{detail}{more}",
                    parent=self)
            else:
                messagebox.showwarning(
                    "部分檔案匯出失敗",
                    f"成功 {successes} 個、失敗 {len(failures)} 個：\n\n{detail}{more}",
                    parent=self)


class EditWindow:
    """Cmd+4／Windows 選單「Edit Windows」開啟：Logic Pro Edit 模式風格的多軌剪輯視窗。
    每個選取的音檔各佔一條有底色的軌道；可框選範圍剪下/複製/貼上/刪除、拖拉片段左右邊緣
    修剪長度、拖拉軌道左上/右上角設定淡入/淡出長度、上下拖曲線點調整曲度、拖曳搬移片段、
    Cmd+E 在播放頭分割。所有編輯都是非破壞性的：存在 entry['edit_regions']，預覽播放跟真正
    匯出都是即時依這份記錄重新組出音訊，不會動到來源檔案。
    """

    TRACK_H = 92
    RULER_H = 26
    TRACK_HEADER_W = 156
    TRACK_BUTTON_W = 56
    TRACK_BUTTON_H = 25
    HANDLE_SIZE = 12
    TRIM_EDGE_PX = 6  # 片段左右邊緣可拖曳修剪長度的熱區寬度（避開左上/右上淡入淡出把手）
    MIN_REGION_LEN = 0.05  # 修剪後最短保留長度（秒），避免拖成長度 0 的退化片段
    CURVE_HANDLE_RADIUS = 5
    DRAG_THRESHOLD_PX = 3
    ACTIVE_REGION_COLOR = "#FFD60A"
    AUTOMATION_MIN_DB = -24.0
    AUTOMATION_MAX_DB = 12.0
    AUTOMATION_NODE_RADIUS = 5
    MIN_PX_PER_SEC = 8
    MAX_PX_PER_SEC = 800
    MIN_AMP_ZOOM = 0.5
    MAX_AMP_ZOOM = 6.0
    MARQUEE_ZONE = 0.65  # 片段內縱向比例：上面是搬移熱區，下面（含這條線）是框選熱區（仿 Logic Marquee）
    TRANSPORT_READY = "ready"
    TRANSPORT_PLAYING = "playing"
    TRANSPORT_PAUSED_BY_SPACE = "paused_by_space"

    def __init__(self, app):
        self.app = app
        self.win = None
        self.tracks = []           # [{"entry":, "color":, "regions": [EditRegion,...]}]
        self.selection = None      # (track_idx, t0, t1)
        self.active_region = None  # 單一 Region 選取（黃框）；與時間範圍 selection 互斥
        self.playhead = 0.0
        self.playhead_track = 0
        self.px_per_sec = 80.0
        self.wave_amp_zoom = 1.0  # 波形振幅（垂直）縮放，仿 Logic Pro 的 Waveform Zoom
        self.snap_zero = False  # 修剪／分割是否自動貼齊波形零交越點，仿 Snap Edits to Zero Crossings
        self.show_automation = False  # 按 A 切換：顯示/編輯 Region 中間的音量自動化節點
        self._zero_cross_cache = {}
        self._cross_source_peak_cache = {}  # 跨檔案來源 Region 的波形峰值快取，見 _peaks_for_source
        self.cycle_range = None    # (t0, t1)，仿 Logic Pro Cycle Range，跨所有軌、與 track 無關
        self.cycle_enabled = False
        self._active_cycle_loop = False  # 目前這一輪 sd.play 是否真的用 cycle_range 無縫循環中
        self.clipboard = None      # [EditRegion,...]（相對時間，貼上時平移到播放頭）
        self.undo_stack = []
        self.redo_stack = []
        self.is_playing = False
        self.transport_state = self.TRANSPORT_READY
        self._closing = False
        self._global_bindings = []
        # 每次播放／暫停／停止都換一個 generation，讓舊的 after(_tick) callback 自動失效；
        # 否則快速「播放→暫停→重播」時，上一輪 tick 可能復活並和新一輪同時更新播放頭。
        self._play_generation = 0
        self._drag = None
        self._build_ui()

    # ---------- 視窗與資料 ----------

    def _build_ui(self):
        self.win = ctk.CTkToplevel(self.app)
        self.win.title("Edit Window")
        self.win.geometry("980x520")
        self.win.configure(fg_color=COLOR_BG)
        self.win.protocol("WM_DELETE_WINDOW", self.on_close)
        self.win.bind("<Destroy>", self._on_window_destroy, add="+")

        toolbar = ctk.CTkFrame(self.win, fg_color="#232326", height=40)
        toolbar.pack(side="top", fill="x")

        def _btn(text, cmd, w=34):
            # 點完工具列按鈕後把鍵盤焦點搶回 canvas，空白鍵/Enter 這些快捷鍵才能穩定生效
            # （CTkButton 自己的滑鼠按下處理常式會把事件攔下，不會冒泡到 win，光靠 win 上的
            # 全域 <Button-1> 保險綁定攔不到，所以直接在每個按鈕自己的 command 裡搶焦點）。
            def _wrapped():
                cmd()
                self.canvas.focus_set()
            b = ctk.CTkButton(toolbar, text=text, width=w, height=28, font=("Arial", 13),
                              fg_color="#3A3A3C", hover_color="#4A4A4C", command=_wrapped)
            b.pack(side="left", padx=3, pady=6)
            return b

        self.btn_play = _btn("▶", self.toggle_play)
        _btn("⏹", self.stop)
        ctk.CTkFrame(toolbar, width=1, height=24, fg_color="#3A3A3C").pack(side="left", padx=6)
        _btn("✂︎ 剪下", self.cmd_cut, w=64)
        _btn("⧉ 複製", self.cmd_copy, w=64)
        _btn("📋 貼上", self.cmd_paste, w=64)
        _btn("🗑 刪除", self.cmd_delete, w=64)
        _btn("✂︎E 分割", self.cmd_split, w=74)
        _btn("🔗 合併", self.cmd_join, w=64)
        ctk.CTkFrame(toolbar, width=1, height=24, fg_color="#3A3A3C").pack(side="left", padx=6)
        _btn("↶", self.cmd_undo, w=34)
        _btn("↷", self.cmd_redo, w=34)
        ctk.CTkFrame(toolbar, width=1, height=24, fg_color="#3A3A3C").pack(side="left", padx=6)
        _btn("－", lambda: self.zoom(0.7), w=30)
        _btn("＋", lambda: self.zoom(1.4), w=30)
        ctk.CTkFrame(toolbar, width=1, height=24, fg_color="#3A3A3C").pack(side="left", padx=6)
        _btn("∿－", lambda: self.zoom_amp(0.8), w=38)
        _btn("∿＋", lambda: self.zoom_amp(1.25), w=38)

        # 第二列工具列：Snap Zero／Flex／Automation 這幾個模式切換鍵，以及 Target／Gain
        # （跟主畫面右側面板同一套語意，操作對象是「目前作用軌」，即最近一次點過的軌道，
        # 見 self.playhead_track／_current_ew_entry），讓使用者不用切回主畫面就能調整目前
        # 這條軌對應音檔的目標 LUFS／增益；改動即時同步回主畫面表格與面板。
        # 這些原本跟第一列工具列擠在一起：第一列全部塞滿量過寬度達 1290px，遠超過視窗固定
        # 寬度 980px，超出的按鈕會被裁在視窗外面點不到——這裡分成兩列就是為了徹底解決這個問題，
        # 而不是每次新增按鈕都要重新計算還有沒有塞得下。
        toolbar2 = ctk.CTkFrame(self.win, fg_color="#1C1C1E", height=34)
        toolbar2.pack(side="top", fill="x")

        def _btn2(text, cmd, w=34):
            def _wrapped():
                cmd()
                self.canvas.focus_set()
            b = ctk.CTkButton(toolbar2, text=text, width=w, height=24, font=("Arial", 12),
                              fg_color="#3A3A3C", hover_color="#4A4A4C", command=_wrapped)
            b.pack(side="left", padx=3, pady=4)
            return b

        self.btn_snap_zero = _btn2("0️⃣ Snap Zero", self.cmd_toggle_snap_zero, w=104)
        self._refresh_snap_zero_btn()
        _btn2("🎛 Flex", self.cmd_open_flex_dialog, w=64)
        self.btn_automation = _btn2("A ~ Automation", self.cmd_toggle_automation, w=118)
        self._refresh_automation_btn()
        ctk.CTkFrame(toolbar2, width=1, height=20, fg_color="#3A3A3C").pack(side="left", padx=6)

        ctk.CTkLabel(toolbar2, text="Target", font=("Arial", 11), text_color="#8E8E93").pack(side="left", padx=(10, 4), pady=4)
        self.ew_target_var = tk.StringVar(value="--")
        self.ew_target_entry = ctk.CTkEntry(
            toolbar2, textvariable=self.ew_target_var, width=64, height=24,
            font=("Roboto", 12, "bold"), text_color=COLOR_CYAN, fg_color="#1A1A1D",
            border_color="#3A3A3C", justify="center",
        )
        self.ew_target_entry.pack(side="left", pady=4)
        self.ew_target_entry.bind("<Return>", self._on_ew_target_return)
        self.ew_target_entry.bind("<KP_Enter>", self._on_ew_target_return)
        self.ew_target_entry.bind("<FocusOut>", self._on_ew_target_commit)
        self.ew_target_entry.bind("<MouseWheel>", self._on_ew_target_scroll)
        self.ew_target_entry.bind("<Button-4>", self._on_ew_target_scroll)
        self.ew_target_entry.bind("<Button-5>", self._on_ew_target_scroll)
        _bind_drag_scrub(self.ew_target_entry, self._on_ew_target_drag)
        ctk.CTkLabel(toolbar2, text="LUFS", font=("Arial", 11), text_color="#8E8E93").pack(side="left", padx=(4, 16), pady=4)

        ctk.CTkLabel(toolbar2, text="Gain", font=("Arial", 11), text_color="#8E8E93").pack(side="left", padx=(0, 4), pady=4)
        self.ew_gain_var = tk.StringVar(value="--")
        self.ew_gain_entry = ctk.CTkEntry(
            toolbar2, textvariable=self.ew_gain_var, width=56, height=24,
            font=("Roboto", 12, "bold"), text_color=COLOR_CYAN, fg_color="#1A1A1D",
            border_color="#3A3A3C", justify="center",
        )
        self.ew_gain_entry.pack(side="left", pady=4)
        self.ew_gain_entry.bind("<Return>", self._on_ew_gain_return)
        self.ew_gain_entry.bind("<KP_Enter>", self._on_ew_gain_return)
        self.ew_gain_entry.bind("<FocusOut>", self._on_ew_gain_commit)
        self.ew_gain_entry.bind("<MouseWheel>", self._on_ew_gain_scroll)
        self.ew_gain_entry.bind("<Button-4>", self._on_ew_gain_scroll)
        self.ew_gain_entry.bind("<Button-5>", self._on_ew_gain_scroll)
        _bind_drag_scrub(self.ew_gain_entry, self._on_ew_gain_drag)
        ctk.CTkLabel(toolbar2, text="dB", font=("Arial", 11), text_color="#8E8E93").pack(side="left", padx=(4, 12), pady=4)

        self.lbl_ew_active_file = ctk.CTkLabel(toolbar2, text="", font=("Arial", 11), text_color="#636366")
        self.lbl_ew_active_file.pack(side="left", padx=(4, 0), pady=4)

        body = ctk.CTkFrame(self.win, fg_color=COLOR_BG)
        body.pack(side="top", fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        # 固定在左側的 Track Header；S/M 不跟時間軸一起水平捲動。
        self.track_header_canvas = tk.Canvas(
            body, width=self.TRACK_HEADER_W, bg="#1C1C1E",
            highlightthickness=0, takefocus=0,
        )
        self.track_header_canvas.grid(row=0, column=0, sticky="ns")

        self.canvas = tk.Canvas(body, bg="#141416", highlightthickness=0)
        self.canvas.grid(row=0, column=1, sticky="nsew")
        hbar = ttk.Scrollbar(body, orient="horizontal", command=self.canvas.xview)
        hbar.grid(row=1, column=1, sticky="ew")
        # 補齊 Track Header 下方、水平捲軸左側的小角落，避免露出預設 frame 色。
        tk.Frame(body, width=self.TRACK_HEADER_W, bg="#1C1C1E").grid(
            row=1, column=0, sticky="nsew",
        )
        self._edit_vbar = ttk.Scrollbar(body, orient="vertical", command=self._editor_yview)
        self._edit_vbar.grid(row=0, column=2, sticky="ns")
        self.canvas.configure(
            xscrollcommand=hbar.set,
            yscrollcommand=self._on_timeline_yscroll,
        )

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<Option-ButtonPress-1>", self._on_press_option)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<Motion>", self._on_hover)
        self.track_header_canvas.bind("<Button-1>", self._on_track_header_click)
        for widget in (self.canvas, self.track_header_canvas):
            widget.bind("<MouseWheel>", self._on_editor_mousewheel)
            widget.bind("<Button-4>", self._on_editor_mousewheel)
            widget.bind("<Button-5>", self._on_editor_mousewheel)

        # 工具列按鈕（剪下/複製/淡入…）用滑鼠點過一次後會把 Tk 鍵盤焦點吃走，之後空白鍵/
        # Enter 這類快捷鍵可能就送不到下面 <space>/<Return> 這些綁在 win 上的處理常式。
        # 保險作法：這個視窗裡任何一次滑鼠點擊，之後都把焦點搶回 canvas，快捷鍵才會穩定有效。
        self.win.bind("<Button-1>", lambda e: self.canvas.focus_set(), add="+")

        for seq, fn in [
            ("<Command-x>", self.cmd_cut), ("<Command-c>", self.cmd_copy),
            ("<Command-v>", self.cmd_paste), ("<Command-e>", self.cmd_split),
            ("<Command-u>", self.cmd_toggle_cycle),
            ("<Command-z>", self.cmd_undo), ("<Command-Shift-Z>", self.cmd_redo),
            ("<Command-s>", self.app._save_project), ("<Control-s>", self.app._save_project),
            ("<BackSpace>", self.cmd_delete), ("<Delete>", self.cmd_delete),
            ("<space>", self.toggle_play),
            ("<Return>", self.restart_from_head),
            ("<KP_Enter>", self.restart_from_head),
            ("<Left>", lambda: self._nudge_playhead(-1.0)),
            ("<Right>", lambda: self._nudge_playhead(1.0)),
            ("<Shift-Left>", lambda: self._nudge_playhead(-5.0)),
            ("<Shift-Right>", lambda: self._nudge_playhead(5.0)),
            ("<Tab>", self.cmd_select_next_region),
            ("<Shift-Tab>", self.cmd_select_prev_region),
            ("<a>", self.cmd_toggle_automation),
            ("<A>", self.cmd_toggle_automation),
        ]:
            self.win.bind(seq, lambda e, f=fn: (f(), "break")[-1])
            # 跟主視窗 Cmd+A/Cmd+E 用同一招保險：macOS/Tk 有時候把按鍵事件送到 root 而不是
            # 這個 Toplevel，單靠 self.win.bind 接不到；用 bind_all 補一層全域保險，但要先確認
            # 目前鍵盤焦點真的在這個視窗裡，否則 Edit Window 開著但沒作用中時，主視窗按同樣的
            # 鍵（例如空白鍵播放預覽）會被這裡誤攔截。
            funcid = self.win.bind_all(
                seq,
                lambda e, f=fn: (f(), "break")[-1] if self._is_frontmost() else None,
                add="+",
            )
            if funcid:
                self._global_bindings.append((seq, funcid))

    def _unbind_global_shortcuts(self):
        """只解除這個 Edit Window 自己註冊的 bind_all，避免反覆 Cmd+4 後累積舊回呼。"""
        bindings, self._global_bindings = self._global_bindings, []
        for seq, funcid in bindings:
            try:
                # bind_all 會把 Tcl callback 登記在 Tk root（app）而非 Toplevel；必須由
                # 同一個 root 解除，才能同步移除 root._tclCommands，避免 App destroy 時重刪。
                unbind_one = getattr(self.app, "_unbind", None)
                if callable(unbind_one):
                    # Python 3.13 tkinter：內建可只移除指定 funcid。
                    unbind_one(("bind", "all", seq), funcid)
                else:
                    # Python 3.10 tkinter 的 unbind_all 只能整組清除，會誤刪其他視窗的 handler；
                    # 依 Tcl script 中的 funcid 精準濾掉這一行，再同步刪除 root command。
                    script = str(self.app.tk.call("bind", "all", seq))
                    prefix = f'if {{"[{funcid} '
                    keep = "\n".join(
                        line for line in script.split("\n")
                        if line.strip() and not line.startswith(prefix)
                    )
                    self.app.tk.call("bind", "all", seq, keep if keep.strip() else "")
                    self.app.deletecommand(funcid)
            except Exception:
                pass

    def _on_window_destroy(self, event):
        """即使 root 直接銷毀 Toplevel，也釋放全域快捷鍵閉包與 stale reference。"""
        if getattr(event, "widget", None) != self.win:
            return
        self._unbind_global_shortcuts()
        if getattr(self.app, "_edit_window", None) is self:
            self.app._edit_window = None

    def _is_frontmost(self):
        try:
            focused = self.win.focus_displayof()
            return focused is not None and focused.winfo_toplevel() == self.win
        except Exception:
            return False

    def _editor_yview(self, *args):
        """垂直捲軸的共用入口：Timeline 是主視圖，Track Header 跟隨同一位置。"""
        self.canvas.yview(*args)
        try:
            first, _ = self.canvas.yview()
            self.track_header_canvas.yview_moveto(first)
        except Exception:
            pass

    def _on_timeline_yscroll(self, first, last):
        """Timeline 的 yscrollcommand：更新捲軸並同步固定的 Track Header。"""
        self._edit_vbar.set(first, last)
        try:
            self.track_header_canvas.yview_moveto(float(first))
        except Exception:
            pass

    def _on_editor_mousewheel(self, event):
        """只在 Edit Window 兩個 Canvas 內處理垂直滾輪，不污染主視窗的全域滾輪。"""
        if getattr(event, "num", None) == 4:
            units = -1
        elif getattr(event, "num", None) == 5:
            units = 1
        else:
            delta = getattr(event, "delta", 0)
            if not delta:
                return None
            units = -1 if delta > 0 else 1
        self.canvas.yview_scroll(units, "units")
        return "break"

    def load_entries(self, entries):
        """(重新)載入要編輯的音檔清單，各自還原既有的 edit_regions（沒有就整段一軌）。"""
        if self.is_playing:
            self.pause(by_space=False)
        else:
            self._play_generation += 1
            self._set_transport_state(self.TRANSPORT_READY)
        self.playhead = 0.0
        self.tracks = []
        for i, entry in enumerate(entries):
            audio = entry.get("audio")
            if audio is None:
                continue
            saved = entry.get("edit_regions")
            if saved is not None and isinstance(saved, list):
                try:
                    regions = [EditRegion.from_dict(d) for d in saved]
                except Exception:
                    regions = [EditRegion(entry["path"], 0.0, audio.duration_seconds, 0.0)]
            else:
                regions = [EditRegion(entry["path"], 0.0, audio.duration_seconds, 0.0)]
            self.tracks.append({
                "entry": entry,
                "color": EDIT_TRACK_COLORS[i % len(EDIT_TRACK_COLORS)],
                "regions": regions,
                # 監聽狀態只屬於這次 Edit Window 預覽；不進 Region undo，也不影響逐檔匯出。
                "muted": False,
                "soloed": False,
            })
        self.selection = None
        self.active_region = None
        self.playhead_track = 0
        self.undo_stack = []
        self.redo_stack = []
        names = "、".join(os.path.basename(t["entry"]["path"]) for t in self.tracks[:4])
        if len(self.tracks) > 4:
            names += f" 等 {len(self.tracks)} 個"
        self.win.title(f"Edit Window — {names}" if names else "Edit Window")
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()
        self.canvas.focus_set()
        self.redraw()

    # ---------- Target LUFS／Gain（跟主畫面右側面板同步） ----------

    def _current_ew_entry(self):
        """目前作用軌（最近一次點過的軌道，見 self.playhead_track）對應的 entry／路徑；
        沒有任何軌道時回傳 (None, None)。"""
        ti = self.playhead_track
        if ti is None or not (0 <= ti < len(self.tracks)):
            return None, None
        entry = self.tracks[ti].get("entry")
        if not entry:
            return None, None
        return entry, entry.get("path")

    def _refresh_gain_target_display(self):
        """依目前作用軌重新顯示 Target／Gain 數值；使用者正在輸入框裡打字時不要覆蓋。"""
        if not hasattr(self, "ew_target_entry"):
            return
        try:
            focused = self.win.focus_get()
        except Exception:
            focused = None
        if focused in (self.ew_target_entry, self.ew_gain_entry):
            return
        entry, path = self._current_ew_entry()
        if entry is None:
            self.ew_target_var.set("--")
            self.ew_gain_var.set("--")
            self.lbl_ew_active_file.configure(text="")
            return
        target = entry.get("target_lufs")
        lufs = entry.get("lufs")
        self.ew_target_var.set(f"{target:.1f}" if isinstance(target, float) else "--")
        if isinstance(target, float) and isinstance(lufs, float):
            self.ew_gain_var.set(f"{target - lufs:.1f}")
        else:
            self.ew_gain_var.set("--")
        self.lbl_ew_active_file.configure(text=os.path.basename(path) if path else "")

    def _apply_target_lufs_to_current(self, new_val, push_undo=True):
        """把新的目標 LUFS 寫入目前作用軌對應的 entry，並同步回主畫面（表格欄位、
        True Peak 疊圖、右側面板數值／波形，如果剛好是主畫面目前顯示的那個檔案）、
        觸發 autosave。push_undo=False 給滾輪/拖曳這類『連續小步調整』用——這類操作
        改由呼叫端（見 _ew_apply_value_burst）自己在整段連續操作開始時只推一筆 undo，
        這裡就不要每一步都各推一筆，否則拖一下滑鼠可能會疊出幾十筆 undo。"""
        entry, path = self._current_ew_entry()
        if entry is None or path is None:
            return
        app = self.app
        old_val = entry.get("target_lufs")
        entry["target_lufs"] = new_val
        if push_undo and isinstance(old_val, float):
            app._undo_stack.append(("lufs_change", [(path, old_val)]))
            if len(app._undo_stack) > 50:
                app._undo_stack = app._undo_stack[-50:]
        try:
            if app.file_table.exists(path):
                app.file_table.set(path, "目標 LUFS", f"{new_val:.1f} LUFS")
                app._sync_true_peak_cells(app.file_table, path, entry)
        except Exception:
            pass
        if path == getattr(app, "current_file_path", None):
            app.update_target_lufs(new_val, from_selection=True)
            app._refresh_gain_display()
            app._schedule_wave_draw()
        app._schedule_autosave()
        self._refresh_gain_target_display()
        # entry["target_lufs"] 變了會改變 _draw_region 算出的波形增益（見 _wave_gain_factor），
        # 這裡要重畫 Edit Window 自己的畫布，波形高度才會即時跟著 Gain/Target 調整放大/縮小。
        self.redraw()

    def _ew_apply_value_burst(self, new_val):
        """滾輪微調或拖曳數值欄位這類『連續小步調整』只推一筆 undo（仿主畫面 LUFS／Gain
        滾輪同一套節流方式：見 _on_lufs_scroll／_ensure_gain_baseline），停手 500ms 後
        才解除，下一次調整（或下一次拖曳）會再推一筆新的，不會每個 tick 都各留一筆。"""
        if not getattr(self, "_ew_lufs_burst_active", False):
            self._ew_lufs_burst_active = True
            entry, path = self._current_ew_entry()
            if entry is not None and isinstance(entry.get("target_lufs"), float):
                self.app._undo_stack.append(("lufs_change", [(path, entry["target_lufs"])]))
                if len(self.app._undo_stack) > 50:
                    self.app._undo_stack = self.app._undo_stack[-50:]
        self._apply_target_lufs_to_current(new_val, push_undo=False)
        job = getattr(self, "_ew_lufs_burst_end_job", None)
        if job:
            try:
                self.win.after_cancel(job)
            except Exception:
                pass
        self._ew_lufs_burst_end_job = self.win.after(500, lambda: setattr(self, "_ew_lufs_burst_active", False))

    def _on_ew_target_commit(self, event=None):
        entry, _ = self._current_ew_entry()
        if entry is None:
            return
        try:
            val = float(self.ew_target_var.get().replace("LUFS", "").strip())
        except (ValueError, AttributeError):
            val = entry.get("target_lufs", -16.0)
        val = round(max(-40.0, min(-1.0, val)), 1)
        self._apply_target_lufs_to_current(val)

    def _on_ew_target_return(self, event=None):
        self._on_ew_target_commit(event)
        self.canvas.focus_set()
        return "break"

    def _on_ew_target_scroll(self, event):
        entry, _ = self._current_ew_entry()
        if entry is None:
            return "break"
        cur = entry.get("target_lufs")
        cur = cur if isinstance(cur, float) else -16.0
        d = self.app._scroll_dir(event)
        self._ew_apply_value_burst(round(max(-40.0, min(-1.0, cur + 0.1 * d)), 1))
        return "break"

    def _on_ew_target_drag(self, delta):
        entry, _ = self._current_ew_entry()
        if entry is None:
            return
        cur = entry.get("target_lufs")
        cur = cur if isinstance(cur, float) else -16.0
        self._ew_apply_value_burst(round(max(-40.0, min(-1.0, cur + delta)), 1))

    def _on_ew_gain_commit(self, event=None):
        entry, _ = self._current_ew_entry()
        if entry is None:
            return
        lufs = entry.get("lufs")
        if not isinstance(lufs, float):
            return
        try:
            gain = float(self.ew_gain_var.get().replace("dB", "").strip())
        except (ValueError, AttributeError):
            target = entry.get("target_lufs")
            gain = (target - lufs) if isinstance(target, float) else 0.0
        gain = max(-20.0, min(20.0, gain))
        self._apply_target_lufs_to_current(round(max(-40.0, min(-1.0, lufs + gain)), 1))

    def _on_ew_gain_return(self, event=None):
        self._on_ew_gain_commit(event)
        self.canvas.focus_set()
        return "break"

    def _on_ew_gain_scroll(self, event):
        entry, _ = self._current_ew_entry()
        if entry is None:
            return "break"
        lufs = entry.get("lufs")
        if not isinstance(lufs, float):
            return "break"
        target = entry.get("target_lufs")
        cur_gain = (target - lufs) if isinstance(target, float) else 0.0
        d = self.app._scroll_dir(event)
        new_gain = max(-20.0, min(20.0, round(cur_gain + 0.1 * d, 1)))
        self._ew_apply_value_burst(round(max(-40.0, min(-1.0, lufs + new_gain)), 1))
        return "break"

    def _on_ew_gain_drag(self, delta):
        entry, _ = self._current_ew_entry()
        if entry is None:
            return
        lufs = entry.get("lufs")
        if not isinstance(lufs, float):
            return
        target = entry.get("target_lufs")
        cur_gain = (target - lufs) if isinstance(target, float) else 0.0
        new_gain = max(-20.0, min(20.0, cur_gain + delta))
        self._ew_apply_value_burst(round(max(-40.0, min(-1.0, lufs + new_gain)), 1))

    # ---------- undo/redo ----------

    def _snapshot(self):
        return [[r.clone() for r in t["regions"]] for t in self.tracks]

    def _push_undo(self):
        self.undo_stack.append(self._snapshot())
        if len(self.undo_stack) > 50:
            self.undo_stack = self.undo_stack[-50:]
        self.redo_stack = []

    def _restore(self, snap):
        for t, regions in zip(self.tracks, snap):
            t["regions"] = [r.clone() for r in regions]
        self.selection = None
        # restore 會整批 clone Region，原本保存的 object identity 已經失效。
        self.active_region = None
        self.redraw()

    def cmd_undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(self._snapshot())
        snap = self.undo_stack.pop()
        self._restore(snap)

    def cmd_redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(self._snapshot())
        snap = self.redo_stack.pop()
        self._restore(snap)

    # ---------- 幾何／繪製 ----------

    def _lane_top(self, idx):
        return self.RULER_H + idx * self.TRACK_H

    def total_duration(self):
        best = 0.0
        for t in self.tracks:
            for r in t["regions"]:
                best = max(best, r.track_offset + r.playback_length)
        return best

    def _track_is_audible(self, track, any_solo=None):
        if any_solo is None:
            any_solo = any(t.get("soloed", False) for t in self.tracks)
        return (
            not track.get("muted", False)
            and (not any_solo or track.get("soloed", False))
        )

    @staticmethod
    def _dim_color(color, factor=0.38):
        """把 #RRGGBB 軌色變暗；Mute／被其他 Solo 排除時仍保留原軌色辨識。"""
        try:
            raw = color.lstrip("#")
            if len(raw) != 6:
                return "#263039"
            rgb = [int(raw[i:i+2], 16) for i in (0, 2, 4)]
            return "#" + "".join(f"{max(0, min(255, round(v * factor))):02X}" for v in rgb)
        except Exception:
            return "#263039"

    def _header_button_bounds(self, track_idx, which):
        top = self._lane_top(track_idx)
        x0 = 10 if which == "solo" else 74
        y0 = top + 48
        return (
            x0, y0,
            x0 + self.TRACK_BUTTON_W,
            y0 + self.TRACK_BUTTON_H,
        )

    def _draw_track_headers(self, height, any_solo):
        header = getattr(self, "track_header_canvas", None)
        if header is None:
            return
        header.delete("all")
        header.configure(scrollregion=(0, 0, self.TRACK_HEADER_W, height))
        header.create_rectangle(
            0, 0, self.TRACK_HEADER_W, self.RULER_H,
            fill="#232326", outline="#3A3A3C",
        )
        header.create_text(
            9, self.RULER_H / 2, anchor="w", text="TRACKS",
            fill="#8E8E93", font=("Arial", 9, "bold"),
        )

        for idx, track in enumerate(self.tracks):
            top = self._lane_top(idx)
            bottom = top + self.TRACK_H
            audible = self._track_is_audible(track, any_solo)
            row_fill = "#202023" if audible else "#141416"
            header.create_rectangle(
                0, top, self.TRACK_HEADER_W, bottom,
                fill=row_fill, outline="#343438", width=1,
            )
            if idx == self.playhead_track:
                header.create_rectangle(
                    0, top, 3, bottom, fill=COLOR_CYAN, outline="",
                )

            fname = os.path.basename(track["entry"]["path"])
            display_name = fname if len(fname) <= 20 else fname[:17] + "…"
            header.create_text(
                10, top + 10, anchor="nw", text=display_name,
                fill="#F2F2F7" if audible else "#6E6E73",
                font=("Arial", 10, "bold"),
            )
            header.create_text(
                self.TRACK_HEADER_W - 9, top + 10, anchor="ne",
                text=str(idx + 1), fill="#636366", font=("Arial", 9),
            )

            for which, label, active_key, active_color in (
                ("solo", "SOLO", "soloed", "#FFD60A"),
                ("mute", "MUTE", "muted", "#FF9F0A"),
            ):
                x0, y0, x1, y1 = self._header_button_bounds(idx, which)
                active = bool(track.get(active_key, False))
                header.create_rectangle(
                    x0, y0, x1, y1,
                    fill=active_color if active else "#3A3A3C",
                    outline="#5A5A5E" if not active else active_color,
                    width=1,
                )
                header.create_text(
                    (x0 + x1) / 2, (y0 + y1) / 2,
                    text=label,
                    fill="#111113" if active else "#D1D1D6",
                    font=("Arial", 9, "bold"),
                )

    def redraw(self):
        c = self.canvas
        c.delete("all")
        if self.active_region is not None and self._find_region_track(self.active_region) is None:
            self.active_region = None
        # PhotoImage 沒有其他 Python 參照就會被回收、畫布上顯示會變空白，所以每次重畫都要
        # 留著這次用到的淡入/淡出疊圖參照（見 _make_fade_image／_draw_region）。
        self._fade_imgs = []
        n = len(self.tracks)
        dur = max(self.total_duration(), 1.0)
        width = max(int(dur * self.px_per_sec) + 80, c.winfo_width() or 800)
        height = self.RULER_H + self.TRACK_H * max(n, 1)
        c.configure(scrollregion=(0, 0, width, height))
        any_solo = any(t.get("soloed", False) for t in self.tracks)
        self._draw_track_headers(height, any_solo)

        # 時間尺：每秒一條格線＋文字
        sec = 0
        while sec * self.px_per_sec <= width:
            x = sec * self.px_per_sec
            c.create_line(x, 0, x, height, fill="#232328")
            if sec % max(1, int(60 / max(self.px_per_sec, 1))) == 0:
                c.create_text(x + 3, 4, anchor="nw", text=self.format_t(sec),
                             fill="#8E8E93", font=("Arial", 9))
            sec += 1

        for idx, t in enumerate(self.tracks):
            top = self._lane_top(idx)
            bottom = top + self.TRACK_H
            audible = self._track_is_audible(t, any_solo)
            c.create_rectangle(
                0, top, width, bottom,
                fill="#101012" if audible else "#09090B",
                outline="#232328", width=1,
            )
            regions = t["regions"]
            dragged = self._drag.get("region") if (
                self._drag and self._drag.get("mode") in ("move", "trim") and self._drag.get("track") == idx
            ) else None
            if dragged is not None and dragged in regions:
                # 拖曳中（尚未放開）的那顆 Region 一定畫在最後＝疊在最上層，仿真實 DAW：
                # 拖過去蓋住別的 Region 時，正在拖的那顆才是視覺上「贏」的那個，不會被蓋到
                # 它底下——放開後真正結算覆蓋範圍見 _resolve_track_overlaps。
                ordered = [r for r in regions if r is not dragged]
                ordered.append(dragged)
            else:
                ordered = regions
            for r in ordered:
                self._draw_region(t, idx, r, top, bottom, audible=audible)

        if self.selection:
            ti, t0, t1 = self.selection
            if 0 <= ti < n:
                top = self._lane_top(ti)
                c.create_rectangle(t0*self.px_per_sec, top, t1*self.px_per_sec, top+self.TRACK_H,
                                   outline=COLOR_CYAN, width=2, dash=(3, 2))

        # Cycle Range（仿 Logic Pro）：跟哪個軌道無關，時間尺上畫一條橫bar＋貫穿全軌的虛線
        # 標出範圍邊界；開啟中用青色（跟播放頭／主畫面 Loop 按鈕同一套顏色語言），只是設定
        # 好但還沒開啟循環時用暗灰色，讓使用者知道範圍還記得、隨時可以 Cmd+U 再打開。
        if self.cycle_range:
            ct0, ct1 = self.cycle_range
            cx0, cx1 = ct0 * self.px_per_sec, ct1 * self.px_per_sec
            cyc_color = COLOR_CYAN if self.cycle_enabled else "#5A5A5E"
            c.create_rectangle(cx0, 0, cx1, self.RULER_H, fill=cyc_color, outline="")
            c.create_line(cx0, 0, cx0, height, fill=cyc_color, dash=(4, 2))
            c.create_line(cx1, 0, cx1, height, fill=cyc_color, dash=(4, 2))

        # 播放頭：貫穿整個視窗（跨所有軌），仿 Logic Pro 的 Tracks Editor，時間尺上再加一個小旗標
        px = self.playhead * self.px_per_sec
        c.create_line(px, 0, px, height, fill=COLOR_CYAN, width=2, tags="playhead")
        c.create_polygon(px, 0, px + 7, 0, px, 9, fill=COLOR_CYAN, outline="", tags="playhead")
        try:
            self.track_header_canvas.yview_moveto(self.canvas.yview()[0])
        except Exception:
            pass
        self._refresh_gain_target_display()

    def _draw_region(self, t, idx, r, top, bottom, audible=True):
        c = self.canvas
        x0 = r.track_offset * self.px_per_sec
        x1 = (r.track_offset + r.playback_length) * self.px_per_sec  # Flex Time 拉伸後的實際寬度
        pad = 3
        region_color = t["color"] if audible else self._dim_color(t["color"])
        c.create_rectangle(x0, top+pad, x1, bottom-pad, fill=region_color, outline="#0A0A0C", width=1)
        # 熱區分界提示線：上面拖曳＝搬移片段，下面（含這條線往下）拖曳＝框選範圍
        zone_y = top + pad + (bottom - top - 2*pad) * self.MARQUEE_ZONE
        c.create_line(x0, zone_y, x1, zone_y, fill="#0A0A0C", width=1, dash=(2, 2))

        # Flex Time／Flex Pitch 生效中的小徽章，讓人一眼看出這段套用了變速/變調
        if abs(r.time_stretch_ratio - 1.0) > 1e-6 or abs(r.pitch_semitones) > 1e-6:
            c.create_text(x1 - 4, bottom - pad - 2, anchor="se", text="🎛",
                         font=("Arial", 11), fill="white")

        # 波形（用 peak cache；來源檔跟這軌自己的檔案不同時——例如跨軌複製貼上、或 Join
        # 合併出來的混音檔——改用 _peaks_for_source 臨時算一份、按 source_path 快取）。
        entry_for_peaks = t["entry"] if r.source_path == t["entry"]["path"] else None
        peaks = None
        src_dur = None
        if entry_for_peaks is not None:
            peaks = self.app._peek_cached_peaks(entry_for_peaks)
            if peaks is None:
                self.app._queue_peak_decode(entry_for_peaks)
            src_dur = t["entry"]["audio"].duration_seconds
        else:
            peaks = self._peaks_for_source(r.source_path)
            src_dur = self._source_duration(r.source_path)
        if peaks is not None and len(peaks) > 1 and r.length > 0 and src_dur:
            s_ratio = r.src_start / src_dur if src_dur > 0 else 0
            e_ratio = r.src_end / src_dur if src_dur > 0 else 1
            s_i = max(0, int(s_ratio * len(peaks)))
            e_i = min(len(peaks), max(s_i+1, int(e_ratio * len(peaks))))
            seg = peaks[s_i:e_i]
            w = max(1, int(x1 - x0))
            if len(seg) > 0:
                idxs = np.linspace(0, len(seg)-1, w).astype(int)
                resized = seg[idxs]
                # entry_for_peaks 不為 None 時（region 來源就是這條軌自己的檔案）才套用
                # Gain／Target 換算出的線性增益，跟主畫面 draw_waveform 用同一套
                # _wave_gain_factor，調整 Gain/Target 時波形高度會跟著即時變大/變小；
                # 跨軌來源（Join、跨軌複製）沒有單一對應的 entry，維持原始比例。
                gain_factor = self.app._wave_gain_factor(entry_for_peaks) if entry_for_peaks is not None else 1.0
                amp = (self.TRACK_H - 2*pad) / 2 * 0.8 * self.wave_amp_zoom * gain_factor
                max_half = (bottom - pad) - (top + bottom) / 2  # 波形放大也不能畫出所在軌道的範圍
                cy = (top + bottom) / 2
                for i, peak in enumerate(resized):
                    lh = min(min(float(peak), 1.0) * amp, max_half)
                    c.create_line(
                        x0+i, cy-lh, x0+i, cy+lh,
                        fill="#CFE9FF" if audible else "#5A6770",
                    )

        # 淡入/淡出：仿 Logic Pro——衰減掉的楔形區域蓋一層白色半透明疊圖（角落最濃、
        # 靠近增益曲線那條斜線漸漸透明消失），斜邊再描一條亮白線標出增益曲線本身。
        # Tk 畫布原生填色沒有真的 alpha 混合（stipple 是稀疏網點，在深色底上看起來反而像變黑），
        # 這裡改用 PIL 算出真正逐像素半透明的 RGBA 圖，用 create_image 疊上去才會是真的「白色透明」。
        ph = bottom - pad - (top + pad)
        if r.fade_in > 0:
            fw = min(r.fade_in * self.px_per_sec, x1 - x0)
            img = self._make_fade_image(
                int(round(fw)), int(round(ph)),
                is_fade_in=True, curve=r.fade_in_curve,
            )
            if img is not None:
                self._fade_imgs.append(img)
                c.create_image(x0, top+pad, anchor="nw", image=img)
            points = self._fade_curve_points(
                x0, x0 + fw, top + pad, bottom - pad,
                r.fade_in_curve, is_fade_in=True,
            )
            c.create_line(*points, fill="#FFFFFF", width=2, smooth=True)
        if r.fade_out > 0:
            fw = min(r.fade_out * self.px_per_sec, x1 - x0)
            img = self._make_fade_image(
                int(round(fw)), int(round(ph)),
                is_fade_in=False, curve=r.fade_out_curve,
            )
            if img is not None:
                self._fade_imgs.append(img)
                c.create_image(x1-fw, top+pad, anchor="nw", image=img)
            points = self._fade_curve_points(
                x1 - fw, x1, top + pad, bottom - pad,
                r.fade_out_curve, is_fade_in=False,
            )
            c.create_line(*points, fill="#FFFFFF", width=2, smooth=True)

        # 淡入/淡出把手（左上/右上小三角）
        hs = self.HANDLE_SIZE
        c.create_polygon(x0, top+pad, x0+hs, top+pad, x0, top+pad+hs, fill=COLOR_CYAN, outline="")
        c.create_polygon(x1, top+pad, x1-hs, top+pad, x1, top+pad+hs, fill=COLOR_CYAN, outline="")

        # 修剪熱區提示：左右邊緣一條細直條（避開上面的淡入/淡出把手），拖曳可修剪片段長度
        trim_zone_top = top + pad + hs + 4
        if bottom - pad - trim_zone_top > 4:
            c.create_rectangle(x0, trim_zone_top, x0 + 3, bottom - pad,
                               fill="#FFFFFF", outline="", stipple="gray50")
            c.create_rectangle(x1 - 3, trim_zone_top, x1, bottom - pad,
                               fill="#FFFFFF", outline="", stipple="gray50")

        # Automation：按 A 顯示時，畫出這段 Region 的音量自動化曲線與節點（仿 Logic Pro）。
        if self.show_automation:
            nodes = sorted(r.gain_nodes, key=lambda p: p[0]) if r.gain_nodes else [[0.0, 0.0]]
            pts = []
            for t_sec, db in nodes:
                nx = x0 + t_sec * self.px_per_sec
                ny = self._automation_db_to_y(top, bottom, db)
                pts.extend([nx, ny])
            if len(nodes) == 1:
                pts.extend([x1, pts[1]])
            c.create_line(*pts, fill="#FFD60A", width=2, tags="automation")
            for t_sec, db in nodes:
                nx = x0 + t_sec * self.px_per_sec
                ny = self._automation_db_to_y(top, bottom, db)
                rad = self.AUTOMATION_NODE_RADIUS
                c.create_oval(nx-rad, ny-rad, nx+rad, ny+rad,
                             fill="#FFD60A", outline="#151517", width=1, tags="automation")

        # 曲度圓點只顯示在目前作用中的 Region，避免多片段時畫面過度擁擠。
        if r is self.active_region:
            for which in ("in", "out"):
                pos = self._fade_curve_handle_position(idx, r, which)
                if pos is None:
                    continue
                hx, hy = pos
                radius = self.CURVE_HANDLE_RADIUS
                c.create_oval(
                    hx-radius, hy-radius, hx+radius, hy+radius,
                    fill=self.ACTIVE_REGION_COLOR, outline="#151517", width=1,
                )

            # 放在所有波形、Fade 疊圖與控制點之後畫，黃框才不會被後續圖層蓋掉。
            c.create_rectangle(
                x0, top+pad, x1, bottom-pad,
                outline=self.ACTIVE_REGION_COLOR, width=3,
            )

    def _fade_curve_points(self, x0, x1, top, bottom, curve, is_fade_in):
        count = max(16, min(80, int(abs(x1 - x0) / 4) + 1))
        u = np.linspace(0.0, 1.0, count)
        progress = u if is_fade_in else 1.0 - u
        gain = _fade_curve_gain(progress, curve)
        xs = x0 + u * (x1 - x0)
        ys = bottom - gain * (bottom - top)
        return [coord for pair in zip(xs, ys) for coord in pair]

    def _make_fade_image(self, w, h, is_fade_in, curve=0.0, max_alpha=130, ss=3):
        """算出淡入/淡出的白色半透明疊圖（RGBA，真的逐像素 alpha，不是 stipple 網點）。
        疊圖只覆蓋「被衰減掉」的那個三角楔形：楔形最尖的角落（音量最接近 0）alpha 最高，
        沿著楔形往增益曲線那條斜邊漸漸淡出到 0，看起來像一層柔和的白霧蓋在波形上，
        跟 Logic Pro 的淡化視覺同一個概念。ss=supersample 倍數，讓斜邊平滑不鋸齒。"""
        if w < 1 or h < 1:
            return None
        W, H = max(1, w * ss), max(1, h * ss)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        u = xx / max(1, W - 1)
        progress = u if is_fade_in else 1.0 - u
        gain = _fade_curve_gain(progress, curve)
        curve_y = (1.0 - gain) * max(1, H - 1)
        in_wedge = yy <= curve_y
        grad = 1.0 - gain
        alpha = np.where(in_wedge, grad * max_alpha, 0.0).astype(np.uint8)
        rgba = np.zeros((H, W, 4), dtype=np.uint8)
        rgba[..., 0:3] = 255
        rgba[..., 3] = alpha
        big = Image.fromarray(rgba, mode="RGBA")
        small = big.resize((w, h), Image.LANCZOS)
        return ImageTk.PhotoImage(small)

    def format_t(self, sec):
        m, s = divmod(int(sec), 60)
        return f"{m:02d}:{s:02d}"

    def zoom(self, factor):
        self.px_per_sec = max(self.MIN_PX_PER_SEC, min(self.MAX_PX_PER_SEC, self.px_per_sec * factor))
        self.redraw()

    def zoom_amp(self, factor):
        """仿 Logic Pro 的 Waveform Zoom：只放大/縮小波形振幅顯示，不影響實際音量。"""
        self.wave_amp_zoom = max(self.MIN_AMP_ZOOM, min(self.MAX_AMP_ZOOM, self.wave_amp_zoom * factor))
        self.redraw()

    def cmd_toggle_snap_zero(self):
        """仿 Logic Pro 的 Snap Edits to Zero Crossings：開啟時修剪／分割會自動貼齊波形
        振幅為 0 的取樣點，避免剪輯處出現爆音。"""
        self.snap_zero = not self.snap_zero
        self._refresh_snap_zero_btn()

    def _refresh_snap_zero_btn(self):
        btn = getattr(self, "btn_snap_zero", None)
        if btn is None:
            return
        btn.configure(fg_color=COLOR_CYAN if self.snap_zero else "#3A3A3C",
                     text_color="black" if self.snap_zero else "white")

    def cmd_toggle_automation(self):
        """按 A：仿 Logic Pro 的 Show/Hide Automation，切換是否顯示/編輯 Region 中間的
        音量自動化節點（見 EditRegion.gain_nodes／_envelope_gain_curve）。"""
        self.show_automation = not self.show_automation
        self._refresh_automation_btn()
        self.redraw()

    def _refresh_automation_btn(self):
        btn = getattr(self, "btn_automation", None)
        if btn is None:
            return
        btn.configure(fg_color=COLOR_CYAN if self.show_automation else "#3A3A3C",
                     text_color="black" if self.show_automation else "white")

    def _automation_db_to_y(self, top, bottom, db):
        pad = 3
        frac = (db - self.AUTOMATION_MIN_DB) / (self.AUTOMATION_MAX_DB - self.AUTOMATION_MIN_DB)
        frac = max(0.0, min(1.0, frac))
        return (bottom - pad) - frac * (bottom - pad - (top + pad))

    def _automation_y_to_db(self, top, bottom, y):
        pad = 3
        span = (bottom - pad) - (top + pad)
        frac = ((bottom - pad) - y) / span if span > 0 else 0.0
        frac = max(0.0, min(1.0, frac))
        return self.AUTOMATION_MIN_DB + frac * (self.AUTOMATION_MAX_DB - self.AUTOMATION_MIN_DB)

    def _nearest_zero_crossing(self, source_path, src_time, window_sec=0.008):
        """在來源音訊 src_time 附近一個小視窗內找振幅正負號翻轉的取樣點（零交越），
        回傳最接近 src_time 的那一個對應的時間；找不到就回傳 None（不貼齊）。"""
        samples, sr, ch = self.app._decode_source_samples(source_path, self._zero_cross_cache)
        if samples.size == 0 or sr <= 0:
            return None
        mono = samples if samples.ndim == 1 else samples.mean(axis=1)
        center = int(round(src_time * sr))
        half = max(1, int(window_sec * sr))
        lo = max(0, center - half)
        hi = min(len(mono), center + half)
        if hi - lo < 2:
            return None
        window = mono[lo:hi]
        signs = np.sign(window)
        signs[signs == 0] = 1
        crossings = np.where(np.diff(signs) != 0)[0]
        if crossings.size == 0:
            return None
        idxs = lo + crossings
        best = idxs[int(np.argmin(np.abs(idxs - center)))]
        return float(best) / sr

    # ---------- hit-testing / 滑鼠互動 ----------

    def _toggle_track_monitor(self, track_idx, key):
        """切換單軌 Solo/Mute；播放中會從同一播放位置立即重建預覽混音。"""
        if not (0 <= track_idx < len(self.tracks)) or key not in ("soloed", "muted"):
            return
        was_playing = self.transport_state == self.TRANSPORT_PLAYING
        if was_playing:
            self._capture_playhead_now()
            self._play_generation += 1
            try:
                sd.stop()
            except Exception:
                pass
            self._set_transport_state(self.TRANSPORT_READY)

        track = self.tracks[track_idx]
        track[key] = not bool(track.get(key, False))
        self.playhead_track = track_idx
        self.redraw()

        if was_playing:
            # play() 會由目前 playhead 以新的 audible tracks 重新 render。
            self.play()

    def _on_track_header_click(self, event):
        header = self.track_header_canvas
        x = header.canvasx(event.x)
        y = header.canvasy(event.y)
        track_idx = self._track_at_y(y)
        if track_idx is None:
            self.canvas.focus_set()
            return "break"

        for which, key in (("solo", "soloed"), ("mute", "muted")):
            x0, y0, x1, y1 = self._header_button_bounds(track_idx, which)
            if x0 <= x <= x1 and y0 <= y <= y1:
                self._toggle_track_monitor(track_idx, key)
                self.canvas.focus_set()
                return "break"

        # 點軌名區：切換目前作用軌，並選取該軌上所有 Region（仿 Logic Pro 點軌道標頭）；
        # 用既有的時間範圍選取機制表示「全選」，剪下/複製/刪除等操作可以直接沿用。
        self.playhead_track = track_idx
        regions = self.tracks[track_idx]["regions"]
        if regions:
            track_end = max(r.track_offset + max(r.length, r.playback_length) for r in regions)
            self.active_region = None
            self.selection = (track_idx, 0.0, track_end)
        self.redraw()
        self.canvas.focus_set()
        return "break"

    def _track_at_y(self, y):
        if not self.tracks:
            return None
        idx = int((y - self.RULER_H) // self.TRACK_H)
        if 0 <= idx < len(self.tracks):
            return idx
        return None

    def _region_at(self, track_idx, x_time):
        # Canvas 後畫的 Region 在上層，hit-test 也反向掃描才會點到視覺上真正位於最上面的那段。
        for r in reversed(self.tracks[track_idx]["regions"]):
            if r.track_offset <= x_time < r.track_offset + r.playback_length:
                return r
        return None

    def cmd_select_next_region(self):
        self._select_region_relative(1)

    def cmd_select_prev_region(self):
        self._select_region_relative(-1)

    def _select_region_relative(self, direction):
        """Tab／Shift+Tab：仿 Logic Pro Edit > Select > Previous/Next Region，
        依 track_offset 順序選取目前這條軌的上一個/下一個 Region。"""
        ti = self._find_region_track(self.active_region)
        if ti is None:
            ti = self.playhead_track
        if ti is None or not (0 <= ti < len(self.tracks)):
            return
        regions = sorted(self.tracks[ti]["regions"], key=lambda r: r.track_offset)
        if not regions:
            return
        if self.active_region in regions:
            idx = (regions.index(self.active_region) + direction) % len(regions)
        else:
            idx = 0 if direction > 0 else len(regions) - 1
        target = regions[idx]
        self.active_region = target
        self.selection = None
        self.playhead_track = ti
        self.playhead = target.track_offset
        self._scroll_region_into_view(target)
        self.redraw()

    def _scroll_region_into_view(self, region):
        try:
            x0 = region.track_offset * self.px_per_sec
            x1 = (region.track_offset + region.playback_length) * self.px_per_sec
            sr = self.canvas.cget("scrollregion").split()
            total_w = float(sr[2]) if len(sr) == 4 else 0.0
            if total_w <= 0:
                return
            lo, hi = self.canvas.xview()
            cur_lo_px, cur_hi_px = lo * total_w, hi * total_w
            if x0 < cur_lo_px or x1 > cur_hi_px:
                self.canvas.xview_moveto(max(0.0, min(1.0, (x0 - 20) / total_w)))
        except Exception:
            pass

    def _find_region_track(self, region):
        if region is None:
            return None
        for ti, track in enumerate(self.tracks):
            if any(r is region for r in track["regions"]):
                return ti
        return None

    def _fade_curve_handle_position(self, track_idx, region, which):
        fade_len = region.fade_in if which == "in" else region.fade_out
        if fade_len <= 0:
            return None
        x0 = region.track_offset * self.px_per_sec
        x1 = (region.track_offset + region.playback_length) * self.px_per_sec
        fade_w = min(fade_len * self.px_per_sec, x1 - x0)
        if fade_w < 2:
            return None
        x = x0 + fade_w * 0.5 if which == "in" else x1 - fade_w * 0.5
        pad = 3
        top = self._lane_top(track_idx) + pad
        bottom = self._lane_top(track_idx) + self.TRACK_H - pad
        curve = region.fade_in_curve if which == "in" else region.fade_out_curve
        gain_mid = _fade_curve_gain(0.5, curve)
        return x, bottom - gain_mid * (bottom - top)

    def _curve_handle_at(self, track_idx, x_px, y_px):
        region = self.active_region
        if region is None or self._find_region_track(region) != track_idx:
            return None
        hit_radius = self.CURVE_HANDLE_RADIUS + 4
        for which in ("in", "out"):
            pos = self._fade_curve_handle_position(track_idx, region, which)
            if pos is None:
                continue
            hx, hy = pos
            if (x_px - hx) ** 2 + (y_px - hy) ** 2 <= hit_radius ** 2:
                return region, which, hx, hy
        return None

    def _automation_node_at(self, track_idx, x_px, y_px):
        """回傳滑鼠附近已存在的 Automation 節點 (region, node_index)，找不到就回傳 None。"""
        top = self._lane_top(track_idx)
        bottom = top + self.TRACK_H
        rad = self.AUTOMATION_NODE_RADIUS + 3
        for r in reversed(self.tracks[track_idx]["regions"]):
            x0 = r.track_offset * self.px_per_sec
            for i, (t_sec, db) in enumerate(r.gain_nodes):
                nx = x0 + t_sec * self.px_per_sec
                ny = self._automation_db_to_y(top, bottom, db)
                if (x_px - nx) ** 2 + (y_px - ny) ** 2 <= rad ** 2:
                    return r, i
        return None

    def _automation_line_at(self, track_idx, x_px, y_px, tol_px=6):
        """回傳滑鼠附近、屬於某個 Region 自動化曲線上的時間點 (region, t_sec)，
        用來判斷「點在曲線上」該新增節點；找不到就回傳 None。"""
        top = self._lane_top(track_idx)
        bottom = top + self.TRACK_H
        for r in reversed(self.tracks[track_idx]["regions"]):
            x0 = r.track_offset * self.px_per_sec
            x1 = (r.track_offset + r.playback_length) * self.px_per_sec
            if not (x0 - tol_px <= x_px <= x1 + tol_px):
                continue
            t_sec = max(0.0, min(r.playback_length, (x_px - x0) / self.px_per_sec))
            nodes = sorted(r.gain_nodes, key=lambda p: p[0]) if r.gain_nodes else [[0.0, 0.0]]
            if len(nodes) == 1:
                db_at = nodes[0][1]
            else:
                times = [p[0] for p in nodes]
                dbs = [p[1] for p in nodes]
                db_at = float(np.interp(t_sec, times, dbs))
            line_y = self._automation_db_to_y(top, bottom, db_at)
            if abs(y_px - line_y) <= tol_px:
                return r, t_sec
        return None

    def _handle_at(self, track_idx, x_px, y_px):
        top = self._lane_top(track_idx)
        for r in reversed(self.tracks[track_idx]["regions"]):
            x0 = r.track_offset * self.px_per_sec
            x1 = (r.track_offset + r.playback_length) * self.px_per_sec
            if x0 <= x_px <= x0 + self.HANDLE_SIZE + 4 and top+3 <= y_px <= top+3+self.HANDLE_SIZE+4:
                return (r, "in")
            if x1 - self.HANDLE_SIZE - 4 <= x_px <= x1 and top+3 <= y_px <= top+3+self.HANDLE_SIZE+4:
                return (r, "out")
        return None

    def _trim_edge_at(self, track_idx, x_px, y_px):
        """片段左右邊緣的修剪熱區：避開左上/右上的淡入/淡出把手（那塊優先給 _handle_at），
        熱區從淡入淡出把手下緣一路延伸到片段底部，仿一般 DAW 拖片段邊緣＝修剪長度。"""
        top = self._lane_top(track_idx)
        zone_top = top + 3 + self.HANDLE_SIZE + 4
        zone_bottom = top + self.TRACK_H - 3
        if y_px < zone_top or y_px > zone_bottom:
            return None
        for r in reversed(self.tracks[track_idx]["regions"]):
            x0 = r.track_offset * self.px_per_sec
            x1 = (r.track_offset + r.playback_length) * self.px_per_sec
            if x0 <= x_px <= x0 + self.TRIM_EDGE_PX:
                return (r, "left")
            if x1 - self.TRIM_EDGE_PX <= x_px <= x1:
                return (r, "right")
        return None

    def _source_duration(self, path):
        """回傳來源音檔總長度（秒），用來限制修剪右緣最多能露出多少音訊；
        優先重用中央清單已載入的 AudioSegment，沒有才直接讀檔，讀不到就回傳 None（不限制）。"""
        for e in self.app.audio_files:
            if e["path"] == path and e.get("audio") is not None:
                return e["audio"].duration_seconds
        try:
            return AudioSegment.from_file(path).duration_seconds
        except Exception:
            return None

    def _peaks_for_source(self, source_path):
        """幫跨檔案來源的 Region（跨軌複製貼上、或 Join 產生的混音檔）算一份波形峰值，
        按 source_path 快取——不像 _get_cached_peaks 是綁在某個 entry 上。"""
        cache = self._cross_source_peak_cache
        cached = cache.get(source_path)
        if cached is not None:
            return cached
        samples, sr, ch = self.app._decode_source_samples(source_path, self._zero_cross_cache)
        if samples.size == 0:
            return None
        mono = samples if samples.ndim == 1 else np.abs(samples).max(axis=1)
        n = len(mono)
        res = self.app._WAVE_CACHE_RES
        chunk = max(1, n // min(res, n))
        usable = (n // chunk) * chunk
        if usable <= 0:
            return None
        peaks = np.abs(mono[:usable]).reshape(-1, chunk).max(axis=1).astype(np.float32)
        cache[source_path] = peaks
        return peaks

    def _on_press(self, event):
        """仿 Logic Pro 的 Marquee 分區：片段上半部直接按住拖曳＝搬移（免 ⌘、可跨軌）；
        片段下半部或空白處拖曳＝框選範圍（給剪下/複製/淡入淡出用）。"""
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)

        # 時間尺（最上面那一條）：跟一般 DAW 一樣可以直接按住拖曳播放中線來 seek。
        if y < self.RULER_H:
            was_playing = self.is_playing
            if was_playing:
                self.pause(by_space=False)
            else:
                # 暫停後若使用者手動 seek，下一次 Space 應從新位置播放，而不是仍套用
                # 「第三次 Space 從頭播放」的舊狀態。
                self._set_transport_state(self.TRANSPORT_READY)
            self.playhead = max(0.0, x / self.px_per_sec)
            self._drag = {"mode": "playhead", "was_playing": was_playing}
            self.redraw()
            return

        ti = self._track_at_y(y)
        if ti is None:
            self.active_region = None
            self.selection = None
            self.redraw()
            return
        self.playhead_track = ti

        # Automation 顯示中：優先處理節點拖曳／在曲線上點擊新增節點，仿 Logic Pro
        # 「顯示 Automation 時，這一區主要用來編輯自動化」的操作習慣。
        if self.show_automation:
            hit = self._automation_node_at(ti, x, y)
            if hit:
                r, node_idx = hit
                self.active_region = r
                self.selection = None
                self._push_undo()
                self._drag = {
                    "mode": "automation_drag", "track": ti, "region": r, "node_idx": node_idx,
                }
                self.redraw()
                return
            hit = self._automation_line_at(ti, x, y)
            if hit:
                r, t_sec = hit
                self.active_region = r
                self.selection = None
                self._push_undo()
                nodes = sorted(r.gain_nodes, key=lambda p: p[0]) if r.gain_nodes else [[0.0, 0.0]]
                if len(nodes) == 1 and not r.gain_nodes:
                    db_at = 0.0
                else:
                    times = [p[0] for p in nodes]
                    dbs = [p[1] for p in nodes]
                    db_at = float(np.interp(t_sec, times, dbs))
                r.gain_nodes.append([t_sec, db_at])
                r.gain_nodes.sort(key=lambda p: p[0])
                node_idx = next(i for i, p in enumerate(r.gain_nodes) if p[0] == t_sec and p[1] == db_at)
                self._drag = {
                    "mode": "automation_drag", "track": ti, "region": r, "node_idx": node_idx,
                }
                self.redraw()
                return

        # 黃色曲度點位於 Fade 內部，必須比角落的長度把手更早 hit-test。
        curve_handle = self._curve_handle_at(ti, x, y)
        if curve_handle:
            r, which, _, hy = curve_handle
            self.active_region = r
            self.selection = None
            self._drag = {
                "mode": "fade_curve_pending", "track": ti, "region": r, "which": which,
                "start_x": x, "start_y": y, "grab_dy": y - hy,
            }
            self.redraw()
            return

        handle = self._handle_at(ti, x, y)
        if handle:
            r, which = handle
            self.active_region = r
            self.selection = None
            self._drag = {
                "mode": "fade_pending", "track": ti, "region": r, "which": which,
                "start_x": x, "start_y": y,
                "orig_fade_in": r.fade_in, "orig_fade_out": r.fade_out,
            }
            self.redraw()
            return

        trim_hit = self._trim_edge_at(ti, x, y)
        if trim_hit:
            r, side = trim_hit
            self.active_region = r
            self.selection = None
            self._drag = {
                "mode": "trim_pending", "track": ti, "region": r, "side": side,
                "start_x": x, "start_y": y,
                "orig_src_start": r.src_start, "orig_src_end": r.src_end,
                "orig_track_offset": r.track_offset,
                "src_dur": self._source_duration(r.source_path),
            }
            self.redraw()
            return

        t_time = max(0.0, x / self.px_per_sec)
        region = self._region_at(ti, t_time)
        lane_top = self._lane_top(ti)
        rel_y = (y - lane_top) / self.TRACK_H
        self.active_region = region
        self.selection = None
        if region is not None and rel_y < self.MARQUEE_ZONE:
            # 先視為單擊選取；真的移動超過門檻後才進 move 並建立 undo。
            self._drag = {
                "mode": "move_pending", "track": ti, "region": region,
                "start_x": x, "start_y": y, "orig_offset": region.track_offset,
            }
        else:
            # 下半部與空白仍保留 Marquee；同樣要超過門檻才成為範圍選取，
            # 因此單擊 Region 的任何位置都可以穩定留下黃框。
            self._drag = {
                "mode": "select_pending", "track": ti, "start_t": t_time,
                "start_x": x, "start_y": y,
            }
            self.playhead = t_time
            if not self.is_playing:
                self._set_transport_state(self.TRANSPORT_READY)
        self.redraw()

    def _on_press_option(self, event):
        """Option 拖曳＝直接複製出一份區域再開始搬移（仿 Logic Pro 的 Option-drag 複製）。
        只有按在片段的搬移熱區（上半部）才複製；時間尺／Fade把手／修剪熱區／框選區
        仍照一般規則走，交給 _on_press 處理，避免行為打架。"""
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        if y < self.RULER_H:
            return self._on_press(event)
        ti = self._track_at_y(y)
        if ti is None:
            return self._on_press(event)
        if self._curve_handle_at(ti, x, y) or self._handle_at(ti, x, y) or self._trim_edge_at(ti, x, y):
            return self._on_press(event)
        t_time = max(0.0, x / self.px_per_sec)
        region = self._region_at(ti, t_time)
        lane_top = self._lane_top(ti)
        rel_y = (y - lane_top) / self.TRACK_H
        if region is None or rel_y >= self.MARQUEE_ZONE:
            return self._on_press(event)  # 不是搬移熱區 → 照一般規則走（例如框選）

        self._push_undo()
        clone = region.clone()
        self.tracks[ti]["regions"].append(clone)
        self.active_region = clone
        self.selection = None
        self.playhead_track = ti
        self._drag = {
            "mode": "move", "track": ti, "region": clone,
            "start_x": x, "start_y": y, "orig_offset": clone.track_offset,
        }
        self.redraw()

    def _on_hover(self, event):
        """依時間尺、Fade 曲度點與 Fade 長度把手顯示對應游標。"""
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        if y < self.RULER_H:
            cursor = "sb_h_double_arrow"
        else:
            ti = self._track_at_y(y)
            if self.show_automation and ti is not None and (
                    self._automation_node_at(ti, x, y) or self._automation_line_at(ti, x, y)):
                cursor = "fleur"
            elif ti is not None and self._curve_handle_at(ti, x, y):
                cursor = "sb_v_double_arrow"
            elif ti is not None and (self._handle_at(ti, x, y) or self._trim_edge_at(ti, x, y)):
                cursor = "sb_h_double_arrow"
            else:
                cursor = ""
        self.canvas.configure(cursor=cursor)

    def _snap_candidates(self, exclude_region=None):
        """磁性吸附候選時間點：0 秒、播放頭、以及所有軌道（含跨軌）裡其他 region 的起訖點。"""
        times = {0.0, self.playhead}
        for t in self.tracks:
            for r in t["regions"]:
                if r is exclude_region:
                    continue
                times.add(r.track_offset)
                times.add(r.track_offset + r.playback_length)
        return times

    def _snap_time(self, t, exclude_region=None):
        threshold = 10 / self.px_per_sec  # 10px 容許誤差換算成時間，隨縮放等比縮放
        best, best_dist = t, threshold
        for cand in self._snap_candidates(exclude_region):
            d = abs(cand - t)
            if d < best_dist:
                best, best_dist = cand, d
        return best

    def _on_drag(self, event):
        if not self._drag:
            return
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        mode = self._drag["mode"]

        if mode.endswith("_pending"):
            moved = math.hypot(
                x - self._drag["start_x"],
                y - self._drag["start_y"],
            )
            if moved < self.DRAG_THRESHOLD_PX:
                return
            if mode == "select_pending":
                self.active_region = None
                self.selection = (
                    self._drag["track"],
                    self._drag["start_t"],
                    self._drag["start_t"],
                )
                self._drag["mode"] = "select"
            elif mode == "move_pending":
                self._push_undo()
                self._drag["mode"] = "move"
            elif mode == "fade_pending":
                self._push_undo()
                self._drag["mode"] = "fade"
            elif mode == "fade_curve_pending":
                self._push_undo()
                self._drag["mode"] = "fade_curve"
            elif mode == "trim_pending":
                self._push_undo()
                self._drag["mode"] = "trim"
            mode = self._drag["mode"]

        if mode == "playhead":
            self.playhead = max(0.0, x / self.px_per_sec)
            self.redraw()
        elif mode == "select":
            t_time = max(0.0, self._snap_time(x / self.px_per_sec))
            ti = self._drag["track"]
            t0, t1 = sorted([self._drag["start_t"], t_time])
            self.selection = (ti, t0, t1)
            self.redraw()
        elif mode == "move":
            r = self._drag["region"]
            dx = (x - self._drag["start_x"]) / self.px_per_sec
            new_start = self._snap_time(max(0.0, self._drag["orig_offset"] + dx), exclude_region=r)
            r.track_offset = max(0.0, new_start)

            # 拖曳跨軌搬移：滑鼠目前所在的軌道跟片段原本所在的軌道不同 → 把片段從舊軌道的
            # region 清單搬到新軌道（顏色跟著新軌道走，因為顏色是軌道屬性、不是片段自己記的）。
            cur_ti = self._drag["track"]
            target_ti = self._track_at_y(y)
            if target_ti is not None and target_ti != cur_ti:
                old_regions = self.tracks[cur_ti]["regions"]
                if r in old_regions:
                    old_regions.remove(r)
                self.tracks[target_ti]["regions"].append(r)
                self._drag["track"] = target_ti
            self.redraw()
        elif mode == "fade":
            r = self._drag["region"]
            dx = (x - self._drag["start_x"]) / self.px_per_sec
            if self._drag["which"] == "in":
                r.fade_in = max(0.0, min(r.length, self._drag["orig_fade_in"] + dx))
            else:
                r.fade_out = max(0.0, min(r.length, self._drag["orig_fade_out"] - dx))
            self.redraw()
        elif mode == "fade_curve":
            r = self._drag["region"]
            ti = self._find_region_track(r)
            if ti is None:
                return
            pad = 3
            top = self._lane_top(ti) + pad
            bottom = self._lane_top(ti) + self.TRACK_H - pad
            effective_y = y - self._drag.get("grab_dy", 0.0)
            gain_mid = (bottom - effective_y) / max(1.0, bottom - top)
            # curve ∈ [-1,1] 對應中點增益 sigmoid(-2)..sigmoid(2)；
            # 反算後控制點會精準跟著滑鼠，不會只有近似的「靈敏度」。
            low = 1.0 / (1.0 + math.exp(2.0))
            high = 1.0 / (1.0 + math.exp(-2.0))
            gain_mid = max(low, min(high, gain_mid))
            curve = _clamp_fade_curve(
                0.5 * math.log(gain_mid / (1.0 - gain_mid))
            )
            if self._drag["which"] == "in":
                r.fade_in_curve = curve
            else:
                r.fade_out_curve = curve
            self.redraw()
        elif mode == "trim":
            r = self._drag["region"]
            # 拖曳距離量到的是「時間軸／播放」秒差；Flex Time 拉伸時來源音訊秒差不是 1:1，
            # 要先除以拉伸倍率換算回來源秒差，兩者才會對得上（ratio=1 時就是原本的算法）。
            ratio = max(0.01, r.time_stretch_ratio)
            dx_track_mouse = (x - self._drag["start_x"]) / self.px_per_sec
            dx = dx_track_mouse / ratio
            orig_src_start = self._drag["orig_src_start"]
            orig_src_end = self._drag["orig_src_end"]
            orig_len = orig_src_end - orig_src_start
            if self._drag["side"] == "left":
                orig_track_offset = self._drag["orig_track_offset"]
                lo = max(-orig_track_offset / ratio, -orig_src_start)
                hi = orig_len - self.MIN_REGION_LEN
                dxc = max(lo, min(hi, dx))
                if self.snap_zero:
                    snapped = self._nearest_zero_crossing(r.source_path, orig_src_start + dxc)
                    if snapped is not None:
                        dxc = max(lo, min(hi, snapped - orig_src_start))
                r.src_start = orig_src_start + dxc
                r.track_offset = orig_track_offset + dxc * ratio
            else:
                src_dur = self._drag.get("src_dur")
                lo = self.MIN_REGION_LEN - orig_len
                hi = (src_dur - orig_src_end) if src_dur is not None else 1e9
                dxc = max(lo, min(hi, dx))
                if self.snap_zero:
                    snapped = self._nearest_zero_crossing(r.source_path, orig_src_end + dxc)
                    if snapped is not None:
                        dxc = max(lo, min(hi, snapped - orig_src_end))
                r.src_end = orig_src_end + dxc
            # 不論修剪哪一邊，長度都可能變短，兩側的 Fade 都要跟著夾回新長度（播放長度）以內。
            r.fade_in = min(r.fade_in, r.playback_length)
            r.fade_out = min(r.fade_out, r.playback_length)
            self.redraw()
            # 仿 Logic Pro 修剪時游標旁的提示文字：目前長度（播放長度）＋這次修剪掉多少。
            trimmed = orig_len * ratio - r.playback_length
            self._draw_trim_help_tag(x, y, f"{r.playback_length:.2f}s  ({trimmed:+.2f}s)")
        elif mode == "automation_drag":
            r = self._drag["region"]
            ti = self._drag["track"]
            node_idx = self._drag["node_idx"]
            if node_idx >= len(r.gain_nodes):
                return
            top = self._lane_top(ti)
            bottom = top + self.TRACK_H
            x0 = r.track_offset * self.px_per_sec
            t_sec = max(0.0, min(r.playback_length, (x - x0) / self.px_per_sec))
            db = max(self.AUTOMATION_MIN_DB, min(self.AUTOMATION_MAX_DB,
                                                 self._automation_y_to_db(top, bottom, y)))
            r.gain_nodes[node_idx] = [t_sec, db]
            self.redraw()

    def _draw_trim_help_tag(self, x_px, y_px, text):
        c = self.canvas
        c.delete("trim_help_tag")
        text_id = c.create_text(x_px + 12, y_px - 14, text=text, anchor="nw", fill="#101012",
                                font=("Arial", 10, "bold"), tags="trim_help_tag")
        bbox = c.bbox(text_id)
        if bbox:
            rect_id = c.create_rectangle(bbox[0]-6, bbox[1]-4, bbox[2]+6, bbox[3]+4,
                                         fill=self.ACTIVE_REGION_COLOR, outline="", tags="trim_help_tag")
            c.tag_raise(text_id, rect_id)

    def _on_release(self, event):
        self.canvas.delete("trim_help_tag")
        if self._drag and self._drag["mode"] == "select":
            if self.selection is not None:
                _, t0, t1 = self.selection
            else:
                t0 = t1 = 0.0
            if abs(t1 - t0) < 1e-9:
                self.selection = None
                self.redraw()
        elif self._drag and self._drag["mode"] == "playhead" and self._drag.get("was_playing"):
            self.play()  # 拖曳時原本正在播放 → 放開後從新的位置接續播放
        elif self._drag and self._drag["mode"] == "automation_drag":
            self._drag["region"].gain_nodes.sort(key=lambda p: p[0])  # 拖完才重排，避免拖曳中 index 位移
            self.redraw()
        elif self._drag and self._drag["mode"] in ("move", "trim"):
            # 搬移/修剪放開的瞬間才結算同軌重疊（仿 Logic Pro：拖到哪、蓋到哪），
            # 不在拖曳過程中即時計算，避免每個 mouse-move 都重建 Region 清單。
            if self._resolve_track_overlaps(self._drag["track"], self._drag["region"]):
                self.redraw()
        self._drag = None

    def _on_double_click(self, event):
        """Automation 顯示時，雙擊節點＝刪除該節點（仿一般 DAW 的自動化節點刪除手勢）。"""
        if not self.show_automation:
            return
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        ti = self._track_at_y(y)
        if ti is None:
            return
        hit = self._automation_node_at(ti, x, y)
        if not hit:
            return
        r, node_idx = hit
        self._push_undo()
        del r.gain_nodes[node_idx]
        self.redraw()

    # ---------- 剪輯操作 ----------

    def _active_track_regions(self):
        if self.selection:
            ti = self.selection[0]
        elif self.active_region is not None:
            ti = self._find_region_track(self.active_region)
        else:
            ti = self.playhead_track
        if ti is None or ti >= len(self.tracks):
            return None
        return self.tracks[ti]

    def _slice_region(self, region, keep_start, keep_end, track_offset=None):
        """依軌道時間切出 Region 的一段，只有保留原外緣時才繼承該側 Fade/Curve。"""
        r_start = region.track_offset
        r_end = r_start + region.length
        lo = max(r_start, min(r_end, keep_start))
        hi = max(lo, min(r_end, keep_end))
        length = hi - lo
        keeps_left_edge = abs(lo - r_start) < 1e-7
        keeps_right_edge = abs(hi - r_end) < 1e-7
        return EditRegion(
            region.source_path,
            region.src_start + (lo - r_start),
            region.src_start + (hi - r_start),
            lo if track_offset is None else track_offset,
            fade_in=min(region.fade_in, length) if keeps_left_edge else 0.0,
            fade_out=min(region.fade_out, length) if keeps_right_edge else 0.0,
            fade_in_curve=region.fade_in_curve if keeps_left_edge else 0.0,
            fade_out_curve=region.fade_out_curve if keeps_right_edge else 0.0,
        )

    def _trim_region_playback(self, region, keep_start, keep_end):
        """依「播放時間」（時間軸秒數，考量 Flex Time 拉伸後）保留 Region 的一段，
        來源時間（src_start/src_end）依拉伸倍率換算回去。用於覆蓋式重疊修剪
        （見 _resolve_track_overlaps），跟 _slice_region 的差異是那個只認來源秒數的
        length，對有拉伯的 Region 會切錯位置。保留範圍太短就回傳 None（整段被吃掉）。"""
        r_start = region.track_offset
        r_end = r_start + region.playback_length
        lo = max(r_start, min(r_end, keep_start))
        hi = max(lo, min(r_end, keep_end))
        playback_len = hi - lo
        if playback_len <= self.MIN_REGION_LEN:
            return None
        ratio = max(0.01, region.time_stretch_ratio)
        keeps_left_edge = abs(lo - r_start) < 1e-7
        keeps_right_edge = abs(hi - r_end) < 1e-7
        new_src_start = region.src_start + (lo - r_start) / ratio
        new_src_end = region.src_start + (hi - r_start) / ratio
        return EditRegion(
            region.source_path, new_src_start, new_src_end, lo,
            fade_in=min(region.fade_in, playback_len) if keeps_left_edge else 0.0,
            fade_out=min(region.fade_out, playback_len) if keeps_right_edge else 0.0,
            fade_in_curve=region.fade_in_curve if keeps_left_edge else 0.0,
            fade_out_curve=region.fade_out_curve if keeps_right_edge else 0.0,
            time_stretch_ratio=region.time_stretch_ratio,
            pitch_semitones=region.pitch_semitones,
            gain_nodes=region.gain_nodes if (keeps_left_edge and keeps_right_edge) else None,
        )

    def _resolve_track_overlaps(self, track_idx, moved):
        """仿 Logic Pro：同軌 Region 拖到跟別的 Region 重疊時，拖曳中的那個直接蓋過去，
        被蓋到的 Region 讓出重疊區——整段被蓋住就刪除，重疊在中間就從中間挖洞切成
        前後兩段，重疊在一側就修剪掉那一側。只在拖曳/修剪『放開』的那一刻結算一次
        （見 _on_release），不在拖曳過程中即時計算，避免每個 mouse-move 都重建清單。"""
        if track_idx is None or not (0 <= track_idx < len(self.tracks)):
            return False
        track = self.tracks[track_idx]
        m_start = moved.track_offset
        m_end = m_start + moved.playback_length
        new_regions = []
        changed = False
        for r in track["regions"]:
            if r is moved:
                new_regions.append(r)
                continue
            r_start = r.track_offset
            r_end = r_start + r.playback_length
            if r_end <= m_start + 1e-7 or r_start >= m_end - 1e-7:
                new_regions.append(r)
                continue
            changed = True
            if r_start >= m_start - 1e-7 and r_end <= m_end + 1e-7:
                continue  # 整段都被蓋住 → 移除
            if r_start < m_start and r_end > m_end:
                # 蓋在中間 → 挖洞，切成保留下來的前後兩段
                left = self._trim_region_playback(r, r_start, m_start)
                right = self._trim_region_playback(r, m_end, r_end)
                if left is not None:
                    new_regions.append(left)
                if right is not None:
                    new_regions.append(right)
                continue
            if r_start < m_start:
                trimmed = self._trim_region_playback(r, r_start, m_start)
            else:
                trimmed = self._trim_region_playback(r, m_end, r_end)
            if trimmed is not None:
                new_regions.append(trimmed)
        if changed:
            track["regions"] = new_regions
            if self.active_region is not None and self._find_region_track(self.active_region) is None:
                self.active_region = None
        return changed

    def _ripple_delete(self, track, t0, t1):
        """把 [t0,t1) 從這條軌道的時間軸上移除，後面的內容整個往前貼齊（ripple）。"""
        dur = t1 - t0
        if dur <= 0:
            return
        new_regions = []
        for r in track["regions"]:
            r_start = r.track_offset
            r_end = r.track_offset + r.length
            if r_end <= t0:
                new_regions.append(r)
            elif r_start >= t1:
                r.track_offset -= dur
                new_regions.append(r)
            elif r_start >= t0 and r_end <= t1:
                continue  # 整段都在被刪範圍內 → 移除
            elif r_start < t0 < r_end <= t1:
                # 左邊留、右邊被刪：縮短尾端
                new_regions.append(self._slice_region(r, r_start, t0))
            elif t0 <= r_start < t1 < r_end:
                # 右邊留、左邊被刪：往前推齊到 t0，來源起點跟著往後移
                new_regions.append(self._slice_region(r, t1, r_end, track_offset=t0))
            elif r_start < t0 and r_end > t1:
                # 選取範圍完全在這個 region 內部 → 切成前後兩段
                left = self._slice_region(r, r_start, t0)
                right = self._slice_region(r, t1, r_end, track_offset=t0)
                new_regions.append(left)
                new_regions.append(right)
            else:
                new_regions.append(r)
        new_regions.sort(key=lambda r: r.track_offset)
        track["regions"] = [r for r in new_regions if r.length > 1e-4]
        if self.active_region is not None and self._find_region_track(self.active_region) is None:
            self.active_region = None

    def cmd_copy(self):
        if not self.selection:
            ti = self._find_region_track(self.active_region)
            if ti is None or self.active_region.length <= 1e-4:
                return
            copied = self.active_region.clone()
            copied.track_offset = 0.0
            self.clipboard = [copied]
            return
        ti, t0, t1 = self.selection
        track = self.tracks[ti]
        clip = []
        for r in track["regions"]:
            r_start, r_end = r.track_offset, r.track_offset + r.length
            lo, hi = max(r_start, t0), min(r_end, t1)
            if hi <= lo:
                continue
            copied = self._slice_region(r, lo, hi, track_offset=lo - t0)
            if copied.length > 1e-4:
                clip.append(copied)
        if clip:
            self.clipboard = clip

    def cmd_cut(self):
        if not self.selection:
            ti = self._find_region_track(self.active_region)
            if ti is None or self.active_region.length <= 1e-4:
                return
            region = self.active_region
            copied = region.clone()
            copied.track_offset = 0.0
            self._push_undo()
            self.tracks[ti]["regions"].remove(region)
            self.clipboard = [copied]
            self.active_region = None
            self.selection = None
            self.playhead_track = ti
            self.redraw()
            return
        self.cmd_copy()
        self._push_undo()
        ti, t0, t1 = self.selection
        self._ripple_delete(self.tracks[ti], t0, t1)
        self.selection = None
        self.redraw()

    def cmd_delete(self):
        if not self.selection:
            ti = self._find_region_track(self.active_region)
            if ti is None or self.active_region.length <= 1e-4:
                return
            region = self.active_region
            self._push_undo()
            self.tracks[ti]["regions"].remove(region)
            self.active_region = None
            self.selection = None
            self.playhead_track = ti
            self.redraw()
            return
        self._push_undo()
        ti, t0, t1 = self.selection
        self._ripple_delete(self.tracks[ti], t0, t1)
        self.selection = None
        self.redraw()

    def cmd_paste(self):
        if not self.clipboard:
            return
        if self.selection:
            ti = self.selection[0]
        elif self.active_region is not None:
            ti = self._find_region_track(self.active_region)
        else:
            ti = self.playhead_track
        if ti is None or ti >= len(self.tracks):
            return
        self._push_undo()
        track = self.tracks[ti]
        ins_at = self.playhead
        clip_dur = max((c.track_offset + c.length) for c in self.clipboard)
        # 先把 ins_at 之後的內容往右推出空間（ripple insert）
        shifted_regions = []
        for r in track["regions"]:
            r_end = r.track_offset + r.length
            if r.track_offset >= ins_at:
                r.track_offset += clip_dur
                shifted_regions.append(r)
            elif r.track_offset < ins_at < r_end:
                # 播放頭切在 region 中間 → 先分割再推
                left = self._slice_region(r, r.track_offset, ins_at)
                right = self._slice_region(r, ins_at, r_end, track_offset=ins_at + clip_dur)
                if left.length > 1e-4:
                    shifted_regions.append(left)
                if right.length > 1e-4:
                    shifted_regions.append(right)
            else:
                shifted_regions.append(r)
        track["regions"] = shifted_regions
        pasted = []
        for c in self.clipboard:
            new_region = c.clone()
            new_region.track_offset = ins_at + c.track_offset
            track["regions"].append(new_region)
            pasted.append(new_region)
        track["regions"].sort(key=lambda r: r.track_offset)
        self.selection = None
        self.active_region = pasted[0] if pasted else None
        self.redraw()

    def cmd_split(self):
        ti = self.playhead_track
        if ti is None or ti >= len(self.tracks):
            return
        t = self.playhead
        track = self.tracks[ti]
        for r in list(track["regions"]):
            if r.track_offset < t < r.track_offset + r.length:
                self._push_undo()
                r_end = r.track_offset + r.length
                split_t = t
                if self.snap_zero:
                    src_time = r.src_start + (t - r.track_offset)
                    snapped = self._nearest_zero_crossing(r.source_path, src_time)
                    if snapped is not None:
                        candidate = r.track_offset + (snapped - r.src_start)
                        if r.track_offset < candidate < r_end:
                            split_t = candidate
                left = self._slice_region(r, r.track_offset, split_t)
                right = self._slice_region(r, split_t, r_end)
                track["regions"].remove(r)
                track["regions"].extend([left, right])
                track["regions"].sort(key=lambda rr: rr.track_offset)
                if self.active_region is r:
                    self.active_region = right
                self.redraw()
                return

    def cmd_join(self):
        """合併目前時間範圍選取內、同一軌上的多個 Region 成一個（仿 Logic Pro 的 Join）：
        把選取範圍內這幾段的混音結果轉存成一個新的音訊檔（存在 ~/.audio_master_joins/，
        跟你的原始素材完全分開），取代原本那幾段。至少要選到同一軌上 2 個 Region 才會動作。"""
        if not self.selection:
            return
        ti, t0, t1 = self.selection
        if ti is None or not (0 <= ti < len(self.tracks)):
            return
        track = self.tracks[ti]
        targets = [r for r in track["regions"] if r.track_offset < t1 and r.track_offset + r.length > t0]
        if len(targets) < 2:
            return

        span_start = min(r.track_offset for r in targets)
        span_end = max(r.track_offset + r.length for r in targets)
        ref_audio = track["entry"]["audio"]
        out_sr, out_ch = ref_audio.frame_rate, ref_audio.channels
        shifted = []
        for r in targets:
            rr = r.clone()
            rr.track_offset -= span_start
            shifted.append(rr)
        mixed = self.app._render_region_list(shifted, out_sr, out_ch)

        join_dir = os.path.join(os.path.expanduser("~"), ".audio_master_joins")
        try:
            os.makedirs(join_dir, exist_ok=True)
            dest_path = os.path.join(join_dir, f"join_{uuid.uuid4().hex[:12]}.wav")
            self._write_mixdown_wav(mixed, out_sr, out_ch, dest_path)
        except Exception:
            traceback.print_exc()
            return

        new_region = EditRegion(dest_path, 0.0, span_end - span_start, span_start)
        self._push_undo()
        track["regions"] = [r for r in track["regions"] if r not in targets] + [new_region]
        track["regions"].sort(key=lambda rr: rr.track_offset)
        self.active_region = new_region
        self.selection = None
        self.redraw()

    @staticmethod
    def _write_mixdown_wav(samples_float, sr, channels, dest_path):
        """把 _render_region_list 算出來的 float(-1~1) 陣列存成 16-bit WAV。"""
        peak = float(np.max(np.abs(samples_float))) if samples_float.size else 0.0
        if peak > 1.0:
            samples_float = samples_float / peak
        ints = np.clip(np.rint(samples_float * 32767.0), -32768, 32767).astype(np.int16)
        seg = AudioSegment(ints.tobytes(), sample_width=2, frame_rate=sr, channels=channels)
        seg.export(dest_path, format="wav")

    def cmd_open_flex_dialog(self):
        """🎛 Flex：仿 Logic Pro 的 Flex Time／Flex Pitch，開一個小視窗設定目前選取
        Region 的播放速度（變速不變調）與音高（變調不變速），套用到整段 Region。
        刻意不做 Logic 逐音符的 Melodyne 式音高校正——那需要額外的單音偵測/分段編輯
        UI，跟這個工具「SFX／音效片段剪輯」的定位不合，所以只做整段套用的簡化版。"""
        region = self.active_region
        if region is None:
            return
        orig_ratio = region.time_stretch_ratio
        orig_pitch = region.pitch_semitones

        dialog = ctk.CTkToplevel(self.win)
        dialog.title("Flex Time / Flex Pitch")
        dialog.configure(fg_color=COLOR_BG)
        dialog.resizable(False, False)
        dialog.transient(self.win)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text=os.path.basename(region.source_path),
                    font=("Roboto", 13, "bold"), text_color="white").pack(padx=20, pady=(16, 2))
        ctk.CTkLabel(dialog, text="套用到整段 Region（非逐音符校正）",
                    font=("Arial", 10), text_color="#8E8E93").pack(padx=20, pady=(0, 10))

        speed_var = ctk.DoubleVar(value=100.0 / max(0.01, region.time_stretch_ratio))
        speed_lbl = ctk.CTkLabel(dialog, text=f"Flex Time（播放速度）：{speed_var.get():.0f}%",
                                 font=("Arial", 12), text_color="#D1D1D6")
        speed_lbl.pack(padx=20, pady=(4, 0))

        def on_speed(v):
            region.time_stretch_ratio = max(0.25, min(4.0, 100.0 / max(1.0, float(v))))
            speed_lbl.configure(text=f"Flex Time（播放速度）：{float(v):.0f}%")
            region.fade_in = min(region.fade_in, region.playback_length)
            region.fade_out = min(region.fade_out, region.playback_length)
            self.redraw()

        ctk.CTkSlider(dialog, from_=25, to=400, number_of_steps=375, variable=speed_var,
                     button_color=COLOR_CYAN, progress_color=COLOR_CYAN, command=on_speed,
                     width=280).pack(padx=20, pady=(4, 12))

        pitch_var = ctk.DoubleVar(value=region.pitch_semitones)
        pitch_lbl = ctk.CTkLabel(dialog, text=f"Flex Pitch（音高）：{pitch_var.get():+.1f} 半音",
                                 font=("Arial", 12), text_color="#D1D1D6")
        pitch_lbl.pack(padx=20, pady=(4, 0))

        def on_pitch(v):
            region.pitch_semitones = max(-24.0, min(24.0, float(v)))
            pitch_lbl.configure(text=f"Flex Pitch（音高）：{region.pitch_semitones:+.1f} 半音")
            self.redraw()

        ctk.CTkSlider(dialog, from_=-24, to=24, number_of_steps=480, variable=pitch_var,
                     button_color=COLOR_CYAN, progress_color=COLOR_CYAN, command=on_pitch,
                     width=280).pack(padx=20, pady=(4, 14))

        def on_reset():
            speed_var.set(100.0)
            on_speed(100.0)
            pitch_var.set(0.0)
            on_pitch(0.0)

        def on_ok():
            changed = (abs(region.time_stretch_ratio - orig_ratio) > 1e-6
                      or abs(region.pitch_semitones - orig_pitch) > 1e-6)
            if changed:
                final_ratio, final_pitch = region.time_stretch_ratio, region.pitch_semitones
                region.time_stretch_ratio, region.pitch_semitones = orig_ratio, orig_pitch
                self._push_undo()  # 記錄「調整前」狀態，Undo 記錄才會是正確的還原點
                region.time_stretch_ratio, region.pitch_semitones = final_ratio, final_pitch
                self.redraw()
            dialog.destroy()

        def on_cancel():
            region.time_stretch_ratio = orig_ratio
            region.pitch_semitones = orig_pitch
            self.redraw()
            dialog.destroy()

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=(0, 16))
        ctk.CTkButton(btn_frame, text="重設", fg_color="#3A3A3C", hover_color="#4A4A4C",
                     font=("Roboto", 12), command=on_reset).pack(side="left", padx=6)
        ctk.CTkButton(btn_frame, text="套用", fg_color=COLOR_CYAN, text_color="black",
                     hover_color="#00C8E0", font=("Roboto", 13, "bold"), command=on_ok).pack(side="left", padx=6)
        ctk.CTkButton(btn_frame, text="取消", fg_color="#3A3A3C", hover_color="#4A4A4C",
                     font=("Roboto", 13), command=on_cancel).pack(side="left", padx=6)
        dialog.protocol("WM_DELETE_WINDOW", on_cancel)

    def cmd_fade_in(self):
        region, ti = self._selection_or_edge_region(edge="in")
        if region is None:
            return
        if self.selection:
            _, t0, t1 = self.selection
            new_fade = max(0.0, min(region.length, t1 - region.track_offset))
        else:
            new_fade = region.fade_in if region.fade_in > 0 else min(region.length, 0.3)
        if abs(new_fade - region.fade_in) < 1e-9:
            return
        self._push_undo()
        if region.fade_in <= 0 and new_fade > 0:
            region.fade_in_curve = 0.0
        region.fade_in = new_fade
        self.redraw()

    def cmd_fade_out(self):
        region, ti = self._selection_or_edge_region(edge="out")
        if region is None:
            return
        if self.selection:
            _, t0, t1 = self.selection
            new_fade = max(0.0, min(region.length, (region.track_offset + region.length) - t0))
        else:
            new_fade = region.fade_out if region.fade_out > 0 else min(region.length, 0.3)
        if abs(new_fade - region.fade_out) < 1e-9:
            return
        self._push_undo()
        if region.fade_out <= 0 and new_fade > 0:
            region.fade_out_curve = 0.0
        region.fade_out = new_fade
        self.redraw()

    def _selection_or_edge_region(self, edge):
        track = self._active_track_regions()
        if track is None or not track["regions"]:
            return None, None
        if self.selection:
            ti, t0, t1 = self.selection
            anchor = t0 if edge == "in" else t1
            for r in reversed(track["regions"]):
                if r.track_offset <= anchor <= r.track_offset + r.length:
                    return r, ti
        active_ti = self._find_region_track(self.active_region)
        if active_ti is not None:
            return self.active_region, active_ti
        regions = sorted(track["regions"], key=lambda r: r.track_offset)
        fallback_ti = self.selection[0] if self.selection else self.playhead_track
        return (regions[0] if edge == "in" else regions[-1]), fallback_ti

    def cmd_toggle_cycle(self):
        """Cmd+U：仿 Logic Pro 的 Cycle Range，切換「只循環目前設定的時間區間」。
        開啟時用目前的時間範圍選取（self.selection，忽略是哪個軌道）當作循環區間；
        區間會記住，之後不改選取直接再按 Cmd+U 也能重新開啟同一段，不必重選一次。"""
        if self.cycle_enabled:
            self.cycle_enabled = False
            if self.is_playing:
                self._capture_playhead_now()
                pos = self.playhead
                self.pause(by_space=False)
                self.playhead = pos
                self.play()
            else:
                self.redraw()
            return

        if self.selection:
            _, t0, t1 = self.selection
            if t1 > t0:
                self.cycle_range = (t0, t1)
        if not self.cycle_range:
            return

        self.cycle_enabled = True
        was_playing = self.is_playing
        if was_playing:
            self.pause(by_space=False)
        self.playhead = self.cycle_range[0]
        if was_playing:
            self.play()
        else:
            self._set_transport_state(self.TRANSPORT_READY)
            self.redraw()

    # ---------- 播放預覽 ----------

    def toggle_play(self):
        """Space：播放 → 暫停 → 從頭播放；之後持續以「暫停／從頭播放」交替。"""
        if self.transport_state == self.TRANSPORT_PLAYING:
            self.pause(by_space=True)
        elif self.transport_state == self.TRANSPORT_PAUSED_BY_SPACE:
            self.playhead = 0.0
            self._set_transport_state(self.TRANSPORT_READY)
            self.play()
        else:
            self.play()

    def _nudge_playhead(self, delta):
        """左右方向鍵：移動播放頭（Shift 加大步幅），播放中則從新位置接續播放。"""
        was_playing = self.is_playing
        if was_playing:
            self.pause(by_space=False)
        else:
            # 使用者主動移動播放頭後，取消「下一次 Space 要從頭」的暫停記號。
            self._set_transport_state(self.TRANSPORT_READY)
        self.playhead = max(0.0, self.playhead + delta)
        if was_playing:
            self.play()
        else:
            self.redraw()

    def _set_transport_state(self, state):
        self.transport_state = state
        self.is_playing = state == self.TRANSPORT_PLAYING
        if hasattr(self, "btn_play"):
            self.btn_play.configure(text="⏸" if self.is_playing else "▶")

    def _capture_playhead_now(self):
        """在停止 sounddevice 前，用同一個系統時鐘精確保存目前播放位置。"""
        if not self.is_playing or not hasattr(self, "_play_start_sys"):
            return
        elapsed = max(0.0, time.time() - self._play_start_sys)
        sr = max(1, int(getattr(self, "_play_sr", 1)))
        duration = max(0.0, float(getattr(self, "_play_len", 0)) / sr)
        if getattr(self, "_active_cycle_loop", False) and duration > 0:
            # Cycle Range 播放中：elapsed 是從循環單元開頭算的原始（未繞回）累積秒數，
            # 可能已經繞了不只一圈，要 % 循環長度換算回真正的絕對時間軸位置，否則暫停時
            # 播放頭會停在循環單元的『原始未繞回終點』，跟真正在響的聲音位置對不起來。
            self.playhead = self.cycle_range[0] + (elapsed % duration)
        else:
            self.playhead = min(elapsed, duration)

    def _audible_preview_regions(self):
        """依每軌 S/M 狀態取得監聽用 Region；正式逐檔匯出不使用這個過濾。"""
        any_solo = any(t.get("soloed", False) for t in self.tracks)
        return [
            region
            for track in self.tracks
            if self._track_is_audible(track, any_solo)
            for region in track["regions"]
        ]

    def play(self):
        """播放整個多軌時間軸（所有軌道依各自 track_offset 混音），不是只播單一軌。"""
        if not self.tracks:
            self._set_transport_state(self.TRANSPORT_READY)
            return
        self._play_generation += 1
        generation = self._play_generation
        try:
            all_regions = [r for t in self.tracks for r in t["regions"]]
            if not all_regions:
                self._set_transport_state(self.TRANSPORT_READY)
                return
            audible_regions = self._audible_preview_regions()
            ref_audio = self.tracks[0]["entry"]["audio"]
            out_sr = ref_audio.frame_rate
            out_ch = ref_audio.channels
            timeline_len = max(
                1,
                int(round(max(r.track_offset + r.playback_length for r in all_regions) * out_sr)),
            )
            if audible_regions:
                rendered = self.app._render_region_list(audible_regions, out_sr, out_ch)
                if len(rendered) < timeline_len:
                    pad_width = (
                        ((0, timeline_len - len(rendered)), (0, 0))
                        if rendered.ndim > 1 else
                        (0, timeline_len - len(rendered))
                    )
                    rendered = np.pad(rendered, pad_width, mode="constant")
            else:
                shape = (timeline_len, out_ch) if out_ch > 1 else (timeline_len,)
                rendered = np.zeros(shape, dtype=np.float32)
            self._active_cycle_loop = False
            if self.cycle_enabled and self.cycle_range:
                ct0, ct1 = self.cycle_range
                t0_idx = max(0, int(round(ct0 * out_sr)))
                t1_idx = min(len(rendered), int(round(ct1 * out_sr)))
                if t1_idx > t0_idx:
                    # Cycle Range 開啟時一律從區間開頭播放：把該區間的混音陣列直接交給
                    # sd.play 原生 loop=True，跟主畫面 Loop 播放同一招——不必等播完再
                    # stop/重開，繞回開頭完全無縫（見主畫面 play_original 的旋轉緩衝陣列）。
                    loop_buf = rendered[t0_idx:t1_idx]
                    self.playhead = ct0
                    sd.stop()
                    sd.play(loop_buf, samplerate=out_sr, device=self.app.get_selected_device(), loop=True)
                    self._play_len = len(loop_buf)
                    self._active_cycle_loop = True
                else:
                    self.cycle_enabled = False  # 選取範圍無效（長度為 0），視為未啟用
            if not self._active_cycle_loop:
                start_idx = int(self.playhead * out_sr)
                if start_idx >= len(rendered):
                    start_idx = 0
                    self.playhead = 0.0
                sd.stop()
                sd.play(rendered[start_idx:], samplerate=out_sr, device=self.app.get_selected_device())
                self._play_len = len(rendered)
        except Exception:
            try:
                sd.stop()
            except Exception:
                pass
            self._set_transport_state(self.TRANSPORT_READY)
            return
        self._set_transport_state(self.TRANSPORT_PLAYING)
        self._play_start_sys = time.time() - (self.playhead - (self.cycle_range[0] if self._active_cycle_loop else 0.0))
        self._play_sr = out_sr
        self._tick(generation)

    def _tick(self, generation):
        if generation != self._play_generation or not self.is_playing:
            return
        elapsed = time.time() - self._play_start_sys
        if self._active_cycle_loop and self._play_len > 0:
            loop_dur = self._play_len / self._play_sr
            self.playhead = self.cycle_range[0] + (elapsed % loop_dur)
        else:
            self.playhead = elapsed
            if elapsed * self._play_sr >= self._play_len:
                self.stop()
                return
        self.redraw()
        self.win.after(80, lambda g=generation: self._tick(g))

    def pause(self, by_space=False):
        self._capture_playhead_now()
        self._play_generation += 1
        sd.stop()
        next_state = self.TRANSPORT_PAUSED_BY_SPACE if by_space else self.TRANSPORT_READY
        self._set_transport_state(next_state)
        self.redraw()

    def stop(self):
        self._play_generation += 1
        sd.stop()
        self.playhead = 0.0
        self._set_transport_state(self.TRANSPORT_READY)
        self.redraw()

    def restart_from_head(self):
        """Enter：只把播放頭歸零；若正在播放就停止，絕不自動開始播放。"""
        self._play_generation += 1
        sd.stop()
        self.playhead = 0.0
        self._set_transport_state(self.TRANSPORT_READY)
        self.redraw()

    # ---------- 關閉：寫回非破壞性編輯記錄 ----------

    def sync_entries(self):
        """把目前視窗內的 Region/Fade 狀態同步回 entry（匯出與關閉共同使用）。"""
        for t in self.tracks:
            entry = t["entry"]
            entry["edit_regions"] = [r.to_dict() for r in t["regions"]]
            entry["duration"] = self.app._entry_duration_label(entry)
            if entry.get("_table") and entry["_table"].exists(entry["path"]):
                entry["_table"].set(entry["path"], "Duration", entry["duration"])
            # 這個檔案剛好是主畫面目前播放/顯示中的主檔 → 立刻換成剪輯後的結果，
            # 不必等使用者重新點選才生效；正在播放中就先停掉，避免繼續播已經換掉的舊資料。
            if entry["path"] == getattr(self.app, "current_file_path", None):
                if self.app.is_playing:
                    self.app.stop_playback()
                self.app.current_audio = self.app._render_edited_audio(entry)
                self.app.playback_duration = self.app.current_audio.duration_seconds
                # play_original() 的播放快取只比對檔案路徑；路徑沒變（同一個檔案剪輯前後都是
                # 它）就會誤判成『沒變過』繼續播剪輯前的舊 playback_data，導致實際播放時長／
                # Loop 迴圈點還是卡在舊長度，即使上面 playback_duration 已經是新的。強制清掉
                # 快取路徑，逼下一次按 Play 時重新從新的 current_audio 建立 playback_data。
                self.app.cached_audio_path = None
                if self.app.pause_position > self.app.playback_duration:
                    self.app.pause_position = 0
                    self.app.scrub_var.set(0)
                self.app.lbl_time.configure(
                    text=f"{self.app.format_time(self.app.pause_position)} / "
                         f"{self.app.format_time(self.app.playback_duration)}")
                try:
                    self.app.scrub_slider.configure(to=self.app.playback_duration if self.app.playback_duration > 0 else 1)
                except Exception:
                    pass

    def on_close(self):
        if self._closing:
            return
        self._closing = True
        try:
            try:
                self.pause()
            except Exception:
                self._play_generation += 1
                try:
                    sd.stop()
                except Exception:
                    pass
                self._set_transport_state(self.TRANSPORT_READY)
            try:
                self.sync_entries()
            except Exception:
                traceback.print_exc()
            try:
                self.app._schedule_autosave()
            except Exception:
                pass
            try:
                self.app._schedule_wave_draw()
            except Exception:
                pass
        finally:
            self._unbind_global_shortcuts()
            try:
                if self.win.winfo_exists():
                    self.win.destroy()
            except Exception:
                pass
            if getattr(self.app, "_edit_window", None) is self:
                self.app._edit_window = None


if __name__ == "__main__":
    app = AudioBalancerApp()
    # 雙擊 .abproj 開啟：PyInstaller 的 argv_emulation 會把 macOS 的「開啟檔案」事件轉成 sys.argv，
    # 在原本自動還原的 session 之後，再把該檔的工作區附加進來（與選單「開啟專案」行為一致）。
    _launch_path = next((a for a in sys.argv[1:] if a.lower().endswith(".abproj") and os.path.isfile(a)), None)
    if _launch_path:
        app.after(200, lambda p=_launch_path: app._open_project_path(p))
    app.mainloop()
