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
import queue
import time
from datetime import datetime
import concurrent.futures
import math
import traceback
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

# ── FFmpeg 整合（來自 音檔批次轉換工具）────────────────────────
LOSSLESS_FORMATS = {"wav", "aif", "aiff", "flac"}
LOSSY_FORMATS    = {"ogg", "m4a", "mp3", "wma", "aac", "opus"}
OUTPUT_FORMATS   = ["Original", "WAV", "AIF", "AIFF", "FLAC", "OGG", "M4A", "MP3", "WMA", "AAC", "OPUS"]
SAMPLE_RATES     = ["Original", "8000", "11025", "22050", "24000", "32000", "44100", "48000", "96000"]
BITRATES         = ["Original", "32", "48", "64", "80", "96", "112", "128", "160", "192", "224", "256", "320"]
BIT_DEPTHS       = ["Original", "16", "24", "32"]  # 無損格式(wav/aif/aiff/flac)用的位元深度選項

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


class EditRegion:
    """Edit Window 裡的一段非破壞性音訊片段：指到某個來源檔的 [src_start, src_end)，
    放在自己軌道時間軸上的 track_offset 位置，可各自套用淡入/淡出。
    source_path 不一定等於這軌本身的檔案——貼上其他軌複製的音訊時會指向別的來源檔。"""

    __slots__ = ("source_path", "src_start", "src_end", "track_offset", "fade_in", "fade_out")

    def __init__(self, source_path, src_start, src_end, track_offset, fade_in=0.0, fade_out=0.0):
        self.source_path = source_path
        self.src_start = src_start
        self.src_end = src_end
        self.track_offset = track_offset
        self.fade_in = fade_in
        self.fade_out = fade_out

    @property
    def length(self):
        return max(0.0, self.src_end - self.src_start)

    def to_dict(self):
        return {
            "source_path": self.source_path, "src_start": self.src_start, "src_end": self.src_end,
            "track_offset": self.track_offset, "fade_in": self.fade_in, "fade_out": self.fade_out,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(d["source_path"], d["src_start"], d["src_end"], d["track_offset"],
                    d.get("fade_in", 0.0), d.get("fade_out", 0.0))

    def clone(self):
        return EditRegion(self.source_path, self.src_start, self.src_end, self.track_offset,
                          self.fade_in, self.fade_out)


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

        self.title("Audio Master — LUFS Balancer + Converter")
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
        # Edit Window（Cmd+4）：同一時間只開一個，重新呼叫就是換選取內容重新載入
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
        # 換掉系統選單後，App 選單的 Quit／Cmd+Q 要導回我們自己的關閉流程（關閉前問存檔）。
        try:
            self.createcommand("tk::mac::Quit", self._on_close)
        except Exception:
            pass

    def _edit_window_open(self):
        return getattr(self, "_edit_window", None) is not None and self._edit_window.win.winfo_exists()

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
        ctk.CTkLabel(self.gain_adj_frame, text="批次", font=("Arial", 12), text_color=COLOR_TEXT_DIM).pack(side="left", padx=(0, 4))
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
        self.bit_menu.configure(state="disabled")  # 預設 Original 格式 → disable
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
        self.bind("<space>", lambda e: None if (self._focus_in_text_entry() or self._focus_blocks_space()) else self.toggle_play_pause())
        self.bind("<Left>", lambda e: None if self._focus_in_text_entry() else self.seek_backward())
        self.bind("<Right>", lambda e: None if self._focus_in_text_entry() else self.seek_forward())
        self.bind("<Up>", lambda e: None if (self._focus_in_text_entry() or self.focus_get() in (self.file_table, self.dir_tree)) else self.select_prev_file())
        self.bind("<Down>", lambda e: None if (self._focus_in_text_entry() or self.focus_get() in (self.file_table, self.dir_tree)) else self.select_next_file())
        # Delete/BackSpace 只在焦點確實落在檔案表/資料夾樹（或無特定焦點）時才刪檔，
        # 避免焦點在按鈕/選單/滑桿時誤刪當前選取的音檔。
        self.bind("<Delete>", lambda e: self.remove_selected_files() if self._delete_allowed() else None)
        self.bind("<BackSpace>", lambda e: self.remove_selected_files() if self._delete_allowed() else None)
        # 全選
        self.bind("<Command-a>", self._handle_select_all_shortcut)
        self.bind("<Command-A>", self._handle_select_all_shortcut)
        self.bind("<Control-a>", self._handle_select_all_shortcut)
        self.bind("<Control-A>", self._handle_select_all_shortcut)
        # macOS/Tk 有時會把 Cmd+A 轉成虛擬事件送給焦點 widget；用 bind_all 補上全域保險。
        for seq in ("<Command-a>", "<Command-A>", "<Control-a>", "<Control-A>", "<<SelectAll>>"):
            self.bind_all(seq, self._handle_select_all_shortcut, add="+")
        # Undo
        self.bind("<Command-z>", lambda e: None if self._focus_in_text_entry() else self._undo())
        self.bind("<Control-z>", lambda e: None if self._focus_in_text_entry() else self._undo())
        # 儲存 / 開啟整個專案
        self.bind("<Command-s>", lambda e: self._save_project())
        self.bind("<Control-s>",  lambda e: self._save_project())
        self.bind("<Command-o>", lambda e: self._open_project())
        self.bind("<Control-o>",  lambda e: self._open_project())
        # Edit Window：Cmd+4 在部分鍵盤/輸入法環境下數字鍵組合鍵會被中途攔截送不到 root
        # （跟上面 Cmd+A 一樣的雷)，所以額外用 bind_all 補一層全域保險，並且再加一個純字母的
        # 替代快捷鍵 Cmd+E（字母鍵的組合鍵在這個 app 其餘功能都很穩定，數字鍵反而容易出狀況）。
        for seq in ("<Command-4>", "<Control-4>", "<Command-e>", "<Command-E>", "<Control-e>", "<Control-E>"):
            self.bind(seq, lambda e: self._open_edit_window())
            self.bind_all(seq, lambda e: self._open_edit_window(), add="+")

        # ==================== 關閉時自動存檔 ====================
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # 讓滑鼠滾輪／觸控板在右側參數區任何位置都能捲動（子元件預設會吃掉滾輪事件）
        self._enable_wheel_scroll()

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
        ft.heading("#0", text="✅", command=lambda: self._toggle_all_exports())  # #0 = 勾選/全選
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

    def _show_workspace_export_dialog(self, exportable_workspaces):
        """彈出工作區選擇視窗，回傳選中的 Workspace 列表，或 None 表示取消。"""
        dialog = ctk.CTkToplevel(self)
        dialog.title("選擇匯出工作區")
        dialog.configure(fg_color=COLOR_BG)
        dialog.resizable(False, False)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="選擇要匯出的工作區：", font=("Roboto", 14, "bold"), text_color="white").pack(padx=20, pady=(15, 10))

        check_vars = []
        for ws in exportable_workspaces:
            var = ctk.BooleanVar(value=True)
            check_vars.append((ws, var))
            ctk.CTkCheckBox(dialog, text=f"{ws.name}  ({self._ready_export_count(ws)} 個就緒)",
                           variable=var, font=("Roboto", 13), text_color="#D1D1D6",
                           checkmark_color="black", fg_color=COLOR_CYAN, hover_color="#00C8E0").pack(anchor="w", padx=30, pady=4)

        result = []

        def on_confirm():
            for ws, var in check_vars:
                if var.get():
                    result.append(ws)
            dialog.destroy()

        def on_cancel():
            dialog.destroy()

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=15)
        ctk.CTkButton(btn_frame, text="確認匯出", fg_color=COLOR_CYAN, text_color="black", hover_color="#00C8E0",
                     font=("Roboto", 13, "bold"), command=on_confirm).pack(side="left", padx=8)
        ctk.CTkButton(btn_frame, text="取消", fg_color="#3A3A3C", hover_color="#4A4A4C",
                     font=("Roboto", 13), command=on_cancel).pack(side="left", padx=8)

        dialog.wait_window()
        return result if result else None

    # ================= 專案功能方法 =================

    def get_selected_device(self):
        dev = self.device_menu.get()
        return None if dev == "System Default" else dev

    def _on_format_changed(self, fmt):
        """格式切換時，動態切換位元率／位元深度選單：
        有損格式(mp3/aac/ogg/wma/opus/m4a) → 位元率(kbps)清單；
        無損格式(wav/aif/aiff/flac) → 位元深度(16/24/32-bit)清單；
        Original(不轉檔，維持每個檔案原本格式) → 停用，跟隨來源檔案本身，不提供覆蓋。"""
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
            self.bit_menu.configure(values=BITRATES, state="disabled")
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
        """近似 True Peak（dBTP）：逐聲道線性內插超取樣後取最大絕對值，抓出一般 sample peak
        量不到、落在取樣點『之間』的真實峰值。線性內插不是 ITU-R BS.1770 規範的精確濾波器，
        但作為監看用途足夠準，換來的是不必額外依賴 scipy 的多相濾波器。"""
        if samples_float is None or samples_float.size == 0:
            return -100.0
        chans = [samples_float] if samples_float.ndim == 1 else \
                [samples_float[:, c] for c in range(samples_float.shape[1])]
        peak = 0.0
        for ch in chans:
            n = len(ch)
            if n < 2:
                p = float(np.max(np.abs(ch))) if n else 0.0
            else:
                x_dst = np.linspace(0, n - 1, n * oversample)
                up = np.interp(x_dst, np.arange(n), ch)
                p = float(np.max(np.abs(up)))
            peak = max(peak, p)
        return 20 * math.log10(max(peak, 1e-10))

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
        total_dur = max((r.track_offset + r.length for r in regions), default=0.0)
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
                    ramp = np.linspace(0.0, 1.0, fi)
                    seg[:fi] = seg[:fi] * (ramp[:, None] if seg.ndim > 1 else ramp)
            if r.fade_out > 0:
                fo = min(n, int(round(r.fade_out * out_sr)))
                if fo > 0:
                    ramp = np.linspace(1.0, 0.0, fo)
                    seg[-fo:] = seg[-fo:] * (ramp[:, None] if seg.ndim > 1 else ramp)

            off_idx = max(0, int(round(r.track_offset * out_sr)))
            end_write = min(total_len, off_idx + n)
            write_n = end_write - off_idx
            if write_n > 0:
                out[off_idx:end_write] += seg[:write_n]

        return np.clip(out, -1.0, 1.0).astype(np.float32)

    def _entry_edit_regions(self, entry):
        """把 entry['edit_regions']（存檔用的 dict 列表）還原成 EditRegion 物件列表；
        沒有編輯紀錄時回傳 None（呼叫端應該直接用原始 entry['audio']，不必走渲染）。"""
        saved = entry.get("edit_regions")
        if not saved:
            return None
        try:
            regions = [EditRegion.from_dict(d) for d in saved]
        except Exception:
            return None
        # 只有一段、完全對應原始檔頭到尾、沒有淡入淡出 → 等同沒編輯過，不必渲染。
        if len(regions) == 1:
            r = regions[0]
            audio = entry.get("audio")
            dur = audio.duration_seconds if audio is not None else None
            if (r.source_path == entry["path"] and abs(r.src_start) < 1e-6
                    and abs(r.track_offset) < 1e-6 and r.fade_in <= 0 and r.fade_out <= 0
                    and dur is not None and abs(r.src_end - dur) < 1e-6):
                return None
        return regions

    def _render_edited_audio(self, entry):
        """若這個檔案在 Edit Window 裡有非破壞性編輯，依 edit_regions 重新組出一份新的
        AudioSegment；沒有編輯記錄時直接回傳原始 entry['audio']，行為與編輯前完全一樣。"""
        base_audio = entry["audio"]
        regions = self._entry_edit_regions(entry)
        if not regions:
            return base_audio
        rendered = self._render_region_list(regions, base_audio.frame_rate, base_audio.channels)
        max_val = float(2 ** (8 * base_audio.sample_width - 1))
        int_dtype = np.array(base_audio.get_array_of_samples()).dtype
        rendered_int = np.clip(np.rint(rendered * max_val), -max_val, max_val - 1).astype(int_dtype)
        return base_audio._spawn(rendered_int.tobytes())

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
        # 移除不可 undo，這是唯一的防線。
        if len(file_iids) > 1:
            if not messagebox.askyesno(
                    "移除檔案",
                    f"確定要從工作區移除選取的 {len(file_iids)} 個檔案？\n"
                    "（不會刪除磁碟上的原始音檔，但清單與目標設定無法復原）",
                    icon="warning", default="no", parent=self):
                return

        for iid in file_iids:
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

            dur_seconds = int(audio.duration_seconds)
            mins, secs = divmod(dur_seconds, 60)
            entry["duration"] = f"{mins:02d}:{secs:02d}"

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

                dur_seconds = int(audio.duration_seconds)
                mins, secs = divmod(dur_seconds, 60)
                entry["duration"] = f"{mins:02d}:{secs:02d}"

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
        for ws in getattr(self, "workspaces", []):
            table = ws.file_table
            if table is None:
                continue
            try:
                if table.winfo_exists():
                    self._refresh_true_peak_overlays_for_table(table, ws)
            except Exception:
                pass
        self.after(200, self._refresh_true_peak_overlays)

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

        entry = by_path.get(path)
        if entry and entry["audio"]:
            self.current_file_path = entry["path"]
            self.current_audio = entry["audio"]
            self.playback_duration = entry["audio"].duration_seconds
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

    def _open_edit_window(self):
        """Cmd+4／選單 Windows → Edit Windows：開啟（或重新載入）多軌剪輯視窗。
        以目前中央表格選取的音檔為準；沒有選取就用目前的主檔；都沒有就提示。"""
        file_sel = [s for s in self.file_table.selection() if not self.file_table.tag_has("folder", s)]
        by_path = {it["path"]: it for it in self.audio_files}
        entries = [by_path[p] for p in file_sel if p in by_path and by_path[p].get("audio") is not None]
        if not entries and getattr(self, "current_file_path", None):
            e = by_path.get(self.current_file_path)
            if e and e.get("audio") is not None:
                entries = [e]
        if not entries:
            messagebox.showinfo("Edit Window", "請先在中央工作區選取至少一個已分析完成的音檔。", parent=self)
            return
        if self._edit_window is None or not self._edit_window.win.winfo_exists():
            self._edit_window = EditWindow(self)
        self._edit_window.load_entries(entries)

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
                self.draw_waveform(entries[0]["audio"], entries[0])
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

    def _get_cached_peaks(self, entry):
        """回傳 entry 的『絕對音量（已除以滿刻度，0~1）』峰值快取陣列。
        只在第一次（或音檔物件變動後，用 is 比對而非重算）解碼一次，之後拖 dB/LUFS、
        調整視窗尺寸都直接複用同一份快取、只做便宜的重取樣，不重新掃整段 PCM。"""
        audio = entry.get("audio")
        if audio is None:
            return None
        cached = entry.get("_peak_cache")
        if cached is not None and cached[0] is audio:
            return cached[1]
        dtype = {1: np.int8, 2: np.int16, 4: np.int32}.get(audio.sample_width, np.int16)
        raw = np.frombuffer(audio.raw_data, dtype=dtype)
        channels = audio.channels or 1
        n_frames = len(raw) // channels
        if n_frames <= 0:
            peaks = np.zeros(1, dtype=np.float32)
        else:
            res = min(self._WAVE_CACHE_RES, n_frames)
            chunk = max(1, n_frames // res)
            usable = (n_frames // chunk) * chunk
            mat = raw[:usable * channels].reshape(-1, chunk, channels)
            peaks = np.abs(mat).max(axis=1).max(axis=1).astype(np.float32)
        full_scale = float(2 ** (8 * audio.sample_width - 1))
        if full_scale:
            peaks = peaks / full_scale
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

        peaks_abs = self._get_cached_peaks(entry) if entry is not None else None
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
            self.draw_waveform(entries[0]["audio"], entries[0])

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

            sd.play(self.playback_data[start_idx:], samplerate=self.playback_sr, device=self.get_selected_device())
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
        self.current_audio = entry["audio"]
        self.playback_duration = entry["audio"].duration_seconds
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
            sd.play(self.playback_data[start_idx:], samplerate=self.playback_sr, device=self.get_selected_device())
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
            if self.loop_var.get():
                self.pause_position = 0
                self.scrub_var.set(0)
                self.play_original()
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
        action_type, snapshot = self._undo_stack.pop()
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

        # 找出所有「真的有檔案會被匯出」的工作區（就緒且有勾選），與計數一致
        exportable = [ws for ws in self.workspaces if self._ready_export_count(ws) > 0]
        if not exportable:
            # 以前這裡靜默 return → 按鈕亮著、按了卻毫無反應。改成講清楚原因。
            messagebox.showinfo(
                "沒有可匯出的檔案",
                "目前沒有任何『🟢 就緒且已勾選 ✅』的檔案。\n\n"
                "可能原因：\n"
                "• 檔案還在分析中（🟡 載入中）\n"
                "• 分析失敗或檔案離線（🔴）\n"
                "• 左側勾選欄全部被取消（⬜）",
                parent=self)
            return

        fmt = self.format_menu.get()
        sr  = self.sr_menu.get()
        br  = self.bit_menu.get()

        # 提醒：輸出格式仍是預設的「Original」（＝尚未指定要轉成哪種格式）。
        # 此時只會做響度平衡、維持原始副檔名（例如 .wav 仍輸出 .wav），不做轉檔。
        # 讓使用者確認，避免「以為沒選格式卻還是輸出了」的疑惑。
        if fmt == "Original" and not getattr(self, "_original_fmt_ok", False):
            go_on = messagebox.askyesno(
                "尚未選擇輸出格式",
                "「輸出格式」目前是「Original」（尚未指定轉換格式）。\n\n"
                "將維持原始格式輸出（例如 .wav 仍輸出為 .wav），\n"
                "只進行響度平衡，不做格式轉換。\n\n"
                "要以原始格式繼續匯出嗎？\n"
                "（若要轉成 WAV／MP3／FLAC 等，請按「否」，\n"
                "再到左下角「輸出格式」選擇想要的格式。）",
                icon="warning", default="no", parent=self)
            if not go_on:
                return

        if len(self.workspaces) == 1:
            selected_workspaces = exportable
        else:
            selected_workspaces = self._show_workspace_export_dialog(exportable)
            if not selected_workspaces:
                return

        custom_name = self._sanitize_export_folder_name(self.folder_name_entry.get())
        export_jobs = self._build_export_jobs(selected_workspaces, custom_name)
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
                entries.append({
                    "name": entry["name"],
                    "path": entry["path"],
                    "audio": entry["audio"],
                    "target_lufs": entry.get("target_lufs"),
                    "lufs": entry.get("lufs"),
                    "source_bit_depth": entry.get("source_bit_depth"),
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
    每個選取的音檔各佔一條有底色的軌道；可框選範圍剪下/複製/貼上/刪除、拖拉軌道左上/右上角
    做淡入/淡出、Cmd 拖曳搬移片段、Cmd+E 在播放頭分割。所有編輯都是非破壞性的：存在
    entry['edit_regions']，預覽播放跟真正匯出都是即時依這份記錄重新組出音訊，不會動到來源檔案。
    """

    TRACK_H = 92
    RULER_H = 26
    HANDLE_SIZE = 12
    MIN_PX_PER_SEC = 8
    MAX_PX_PER_SEC = 800
    MARQUEE_ZONE = 0.65  # 片段內縱向比例：上面是搬移熱區，下面（含這條線）是框選熱區（仿 Logic Marquee）

    def __init__(self, app):
        self.app = app
        self.win = None
        self.tracks = []           # [{"entry":, "color":, "regions": [EditRegion,...]}]
        self.selection = None      # (track_idx, t0, t1)
        self.playhead = 0.0
        self.playhead_track = 0
        self.px_per_sec = 80.0
        self.clipboard = None      # [EditRegion,...]（相對時間，貼上時平移到播放頭）
        self.undo_stack = []
        self.redo_stack = []
        self.is_playing = False
        self._drag = None
        self._build_ui()

    # ---------- 視窗與資料 ----------

    def _build_ui(self):
        self.win = ctk.CTkToplevel(self.app)
        self.win.title("Edit Window")
        self.win.geometry("980x520")
        self.win.configure(fg_color=COLOR_BG)
        self.win.protocol("WM_DELETE_WINDOW", self.on_close)

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
        _btn("✂︎", self.cmd_cut, w=34)
        _btn("⧉", self.cmd_copy, w=34)
        _btn("📋", self.cmd_paste, w=34)
        _btn("🗑", self.cmd_delete, w=34)
        _btn("✂︎E", self.cmd_split, w=40)
        ctk.CTkFrame(toolbar, width=1, height=24, fg_color="#3A3A3C").pack(side="left", padx=6)
        _btn("Fade In", self.cmd_fade_in, w=64)
        _btn("Fade Out", self.cmd_fade_out, w=68)
        ctk.CTkFrame(toolbar, width=1, height=24, fg_color="#3A3A3C").pack(side="left", padx=6)
        _btn("↶", self.cmd_undo, w=34)
        _btn("↷", self.cmd_redo, w=34)
        ctk.CTkFrame(toolbar, width=1, height=24, fg_color="#3A3A3C").pack(side="left", padx=6)
        _btn("－", lambda: self.zoom(0.7), w=30)
        _btn("＋", lambda: self.zoom(1.4), w=30)

        self.lbl_hint = ctk.CTkLabel(toolbar, text="片段上半部拖曳＝搬移（可跨軌、自動磁性吸附）；下半部/空白處拖曳＝框選範圍；拖角落淡入淡出",
                                     font=("Arial", 10), text_color="#8E8E93")
        self.lbl_hint.pack(side="left", padx=10)

        body = ctk.CTkFrame(self.win, fg_color=COLOR_BG)
        body.pack(side="top", fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(body, bg="#141416", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        hbar = ttk.Scrollbar(body, orient="horizontal", command=self.canvas.xview)
        hbar.grid(row=1, column=0, sticky="ew")
        vbar = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        vbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<Motion>", self._on_hover)

        # 工具列按鈕（剪下/複製/淡入…）用滑鼠點過一次後會把 Tk 鍵盤焦點吃走，之後空白鍵/
        # Enter 這類快捷鍵可能就送不到下面 <space>/<Return> 這些綁在 win 上的處理常式。
        # 保險作法：這個視窗裡任何一次滑鼠點擊，之後都把焦點搶回 canvas，快捷鍵才會穩定有效。
        self.win.bind("<Button-1>", lambda e: self.canvas.focus_set(), add="+")

        for seq, fn in [
            ("<Command-x>", self.cmd_cut), ("<Command-c>", self.cmd_copy),
            ("<Command-v>", self.cmd_paste), ("<Command-e>", self.cmd_split),
            ("<Command-z>", self.cmd_undo), ("<Command-Shift-Z>", self.cmd_redo),
            ("<BackSpace>", self.cmd_delete), ("<Delete>", self.cmd_delete),
            ("<space>", lambda e: self.toggle_play()),
            ("<Return>", lambda e: self.restart_from_head()),
            ("<KP_Enter>", lambda e: self.restart_from_head()),
        ]:
            self.win.bind(seq, lambda e, f=fn: (f(), "break")[-1])

    def load_entries(self, entries):
        """(重新)載入要編輯的音檔清單，各自還原既有的 edit_regions（沒有就整段一軌）。"""
        self.tracks = []
        for i, entry in enumerate(entries):
            audio = entry.get("audio")
            if audio is None:
                continue
            saved = entry.get("edit_regions")
            if saved:
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
            })
        self.selection = None
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
                best = max(best, r.track_offset + r.length)
        return best

    def redraw(self):
        c = self.canvas
        c.delete("all")
        # PhotoImage 沒有其他 Python 參照就會被回收、畫布上顯示會變空白，所以每次重畫都要
        # 留著這次用到的淡入/淡出疊圖參照（見 _make_fade_image／_draw_region）。
        self._fade_imgs = []
        n = len(self.tracks)
        dur = max(self.total_duration(), 1.0)
        width = max(int(dur * self.px_per_sec) + 80, c.winfo_width() or 800)
        height = self.RULER_H + self.TRACK_H * max(n, 1)
        c.configure(scrollregion=(0, 0, width, height))

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
            c.create_rectangle(0, top, width, bottom, fill="#101012", outline="#232328")
            for r in t["regions"]:
                self._draw_region(t, idx, r, top, bottom)
            # 播放頭
            if idx == self.playhead_track:
                px = self.playhead * self.px_per_sec
                c.create_line(px, top, px, bottom, fill=COLOR_CYAN, width=2, tags="playhead")
            # 軌名籤
            fname = os.path.basename(t["entry"]["path"])
            tag = c.create_text(6, top + 5, anchor="nw", text=fname, fill="#EAEAEA", font=("Arial", 9, "bold"))
            bb = c.bbox(tag)
            if bb:
                c.create_rectangle(bb[0]-3, bb[1]-2, bb[2]+3, bb[3]+2, fill="#000000", stipple="gray50", outline="")
                c.tag_raise(tag)

        if self.selection:
            ti, t0, t1 = self.selection
            if 0 <= ti < n:
                top = self._lane_top(ti)
                c.create_rectangle(t0*self.px_per_sec, top, t1*self.px_per_sec, top+self.TRACK_H,
                                   outline=COLOR_CYAN, width=2, dash=(3, 2))

    def _draw_region(self, t, idx, r, top, bottom):
        c = self.canvas
        x0 = r.track_offset * self.px_per_sec
        x1 = (r.track_offset + r.length) * self.px_per_sec
        pad = 3
        c.create_rectangle(x0, top+pad, x1, bottom-pad, fill=t["color"], outline="#0A0A0C", width=1)
        # 熱區分界提示線：上面拖曳＝搬移片段，下面（含這條線往下）拖曳＝框選範圍
        zone_y = top + pad + (bottom - top - 2*pad) * self.MARQUEE_ZONE
        c.create_line(x0, zone_y, x1, zone_y, fill="#0A0A0C", width=1, dash=(2, 2))

        # 波形（用 peak cache；來源檔跟這軌自己的檔案不同時，臨時算一份不快取，數量通常很小）
        samples = None
        cache_key = f"_ew_peak_cache_{id(self)}"
        entry_for_peaks = t["entry"] if r.source_path == t["entry"]["path"] else None
        peaks = None
        if entry_for_peaks is not None:
            peaks = self.app._peek_cached_peaks(entry_for_peaks)
            if peaks is None:
                self.app._queue_peak_decode(entry_for_peaks)
        if peaks is not None and len(peaks) > 1 and r.length > 0:
            audio = t["entry"]["audio"]
            src_dur = audio.duration_seconds if r.source_path == t["entry"]["path"] else r.length
            s_ratio = r.src_start / src_dur if src_dur > 0 else 0
            e_ratio = r.src_end / src_dur if src_dur > 0 else 1
            s_i = max(0, int(s_ratio * len(peaks)))
            e_i = min(len(peaks), max(s_i+1, int(e_ratio * len(peaks))))
            seg = peaks[s_i:e_i]
            w = max(1, int(x1 - x0))
            if len(seg) > 0:
                idxs = np.linspace(0, len(seg)-1, w).astype(int)
                resized = seg[idxs]
                amp = (self.TRACK_H - 2*pad) / 2 * 0.8
                cy = (top + bottom) / 2
                for i, peak in enumerate(resized):
                    lh = min(float(peak), 1.0) * amp
                    c.create_line(x0+i, cy-lh, x0+i, cy+lh, fill="#CFE9FF")

        # 淡入/淡出：仿 Logic Pro——衰減掉的楔形區域蓋一層白色半透明疊圖（角落最濃、
        # 靠近增益曲線那條斜線漸漸透明消失），斜邊再描一條亮白線標出增益曲線本身。
        # Tk 畫布原生填色沒有真的 alpha 混合（stipple 是稀疏網點，在深色底上看起來反而像變黑），
        # 這裡改用 PIL 算出真正逐像素半透明的 RGBA 圖，用 create_image 疊上去才會是真的「白色透明」。
        ph = bottom - pad - (top + pad)
        if r.fade_in > 0:
            fw = min(r.fade_in * self.px_per_sec, x1 - x0)
            img = self._make_fade_image(int(round(fw)), int(round(ph)), is_fade_in=True)
            if img is not None:
                self._fade_imgs.append(img)
                c.create_image(x0, top+pad, anchor="nw", image=img)
            c.create_line(x0, bottom-pad, x0+fw, top+pad, fill="#FFFFFF", width=2)
        if r.fade_out > 0:
            fw = min(r.fade_out * self.px_per_sec, x1 - x0)
            img = self._make_fade_image(int(round(fw)), int(round(ph)), is_fade_in=False)
            if img is not None:
                self._fade_imgs.append(img)
                c.create_image(x1-fw, top+pad, anchor="nw", image=img)
            c.create_line(x1, bottom-pad, x1-fw, top+pad, fill="#FFFFFF", width=2)

        # 淡入/淡出把手（左上/右上小三角）
        hs = self.HANDLE_SIZE
        c.create_polygon(x0, top+pad, x0+hs, top+pad, x0, top+pad+hs, fill=COLOR_CYAN, outline="")
        c.create_polygon(x1, top+pad, x1-hs, top+pad, x1, top+pad+hs, fill=COLOR_CYAN, outline="")

    def _make_fade_image(self, w, h, is_fade_in, max_alpha=130, ss=3):
        """算出淡入/淡出的白色半透明疊圖（RGBA，真的逐像素 alpha，不是 stipple 網點）。
        疊圖只覆蓋「被衰減掉」的那個三角楔形：楔形最尖的角落（音量最接近 0）alpha 最高，
        沿著楔形往增益曲線那條斜邊漸漸淡出到 0，看起來像一層柔和的白霧蓋在波形上，
        跟 Logic Pro 的淡化視覺同一個概念。ss=supersample 倍數，讓斜邊平滑不鋸齒。"""
        if w < 1 or h < 1:
            return None
        W, H = max(1, w * ss), max(1, h * ss)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        if is_fade_in:
            diag_y = H - (H / W) * xx
            in_wedge = yy <= diag_y
            grad = np.clip(1.0 - xx / W, 0.0, 1.0)
        else:
            diag_y = H - (H / W) * (W - xx)
            in_wedge = yy <= diag_y
            grad = np.clip(1.0 - (W - xx) / W, 0.0, 1.0)
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

    # ---------- hit-testing / 滑鼠互動 ----------

    def _track_at_y(self, y):
        if not self.tracks:
            return None
        idx = int((y - self.RULER_H) // self.TRACK_H)
        if 0 <= idx < len(self.tracks):
            return idx
        return None

    def _region_at(self, track_idx, x_time):
        for r in self.tracks[track_idx]["regions"]:
            if r.track_offset <= x_time <= r.track_offset + r.length:
                return r
        return None

    def _handle_at(self, track_idx, x_px, y_px):
        top = self._lane_top(track_idx)
        for r in self.tracks[track_idx]["regions"]:
            x0 = r.track_offset * self.px_per_sec
            x1 = (r.track_offset + r.length) * self.px_per_sec
            if x0 <= x_px <= x0 + self.HANDLE_SIZE + 4 and top+3 <= y_px <= top+3+self.HANDLE_SIZE+4:
                return (r, "in")
            if x1 - self.HANDLE_SIZE - 4 <= x_px <= x1 and top+3 <= y_px <= top+3+self.HANDLE_SIZE+4:
                return (r, "out")
        return None

    def _on_press(self, event):
        """仿 Logic Pro 的 Marquee 分區：片段上半部直接按住拖曳＝搬移（免 ⌘、可跨軌）；
        片段下半部或空白處拖曳＝框選範圍（給剪下/複製/淡入淡出用）。"""
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)

        # 時間尺（最上面那一條）：跟一般 DAW 一樣可以直接按住拖曳播放中線來 seek。
        if y < self.RULER_H:
            was_playing = self.is_playing
            if was_playing:
                sd.stop()
                self.is_playing = False
                self.btn_play.configure(text="▶")
            self.playhead = max(0.0, x / self.px_per_sec)
            self._drag = {"mode": "playhead", "was_playing": was_playing}
            self.redraw()
            return

        ti = self._track_at_y(y)
        if ti is None:
            return
        self.playhead_track = ti
        handle = self._handle_at(ti, x, y)
        if handle:
            r, which = handle
            self._push_undo()
            self._drag = {"mode": "fade", "region": r, "which": which, "start_x": x,
                          "orig_fade_in": r.fade_in, "orig_fade_out": r.fade_out}
            return

        t_time = max(0.0, x / self.px_per_sec)
        region = self._region_at(ti, t_time)
        lane_top = self._lane_top(ti)
        rel_y = (y - lane_top) / self.TRACK_H
        if region is not None and rel_y < self.MARQUEE_ZONE:
            self._push_undo()
            self._drag = {"mode": "move", "track": ti, "region": region,
                          "start_x": x, "orig_offset": region.track_offset}
            return

        self._drag = {"mode": "select", "track": ti, "start_t": t_time}
        self.selection = (ti, t_time, t_time)
        self.playhead = t_time
        self.redraw()

    def _on_hover(self, event):
        """滑鼠移到時間尺上時換成拖曳游標，提示可以按住拖拉播放中線。"""
        y = self.canvas.canvasy(event.y)
        self.canvas.configure(cursor="sb_h_double_arrow" if y < self.RULER_H else "")

    def _snap_candidates(self, exclude_region=None):
        """磁性吸附候選時間點：0 秒、播放頭、以及所有軌道（含跨軌）裡其他 region 的起訖點。"""
        times = {0.0, self.playhead}
        for t in self.tracks:
            for r in t["regions"]:
                if r is exclude_region:
                    continue
                times.add(r.track_offset)
                times.add(r.track_offset + r.length)
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

            # ⌘拖曳跨軌搬移：滑鼠目前所在的軌道跟片段原本所在的軌道不同 → 把片段從舊軌道的
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

    def _on_release(self, event):
        if self._drag and self._drag["mode"] == "select":
            ti, t0, t1 = self.selection
            if abs(t1 - t0) < 0.01:
                self.selection = None
                self.redraw()
        elif self._drag and self._drag["mode"] == "playhead" and self._drag.get("was_playing"):
            self.play()  # 拖曳時原本正在播放 → 放開後從新的位置接續播放
        self._drag = None

    # ---------- 剪輯操作 ----------

    def _active_track_regions(self):
        if self.selection:
            ti = self.selection[0]
        else:
            ti = self.playhead_track
        if ti is None or ti >= len(self.tracks):
            return None
        return self.tracks[ti]

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
                cut_from_tail = r_end - t0
                r.src_end -= cut_from_tail
                new_regions.append(r)
            elif t0 <= r_start < t1 < r_end:
                # 右邊留、左邊被刪：往前推齊到 t0，來源起點跟著往後移
                r.src_start += (t1 - r_start)
                r.track_offset = t0
                new_regions.append(r)
            elif r_start < t0 and r_end > t1:
                # 選取範圍完全在這個 region 內部 → 切成前後兩段
                left = EditRegion(r.source_path, r.src_start, r.src_start + (t0 - r_start),
                                  r.track_offset, fade_in=r.fade_in, fade_out=0.0)
                right = EditRegion(r.source_path, r.src_start + (t1 - r_start), r.src_end,
                                   t0, fade_in=0.0, fade_out=r.fade_out)
                new_regions.append(left)
                new_regions.append(right)
            else:
                new_regions.append(r)
        new_regions.sort(key=lambda r: r.track_offset)
        track["regions"] = [r for r in new_regions if r.length > 1e-4]
        if not track["regions"]:
            track["regions"] = [EditRegion(track["entry"]["path"], 0.0, 0.0, 0.0)]

    def cmd_copy(self):
        if not self.selection:
            return
        ti, t0, t1 = self.selection
        track = self.tracks[ti]
        clip = []
        for r in track["regions"]:
            r_start, r_end = r.track_offset, r.track_offset + r.length
            lo, hi = max(r_start, t0), min(r_end, t1)
            if hi <= lo:
                continue
            src_lo = r.src_start + (lo - r_start)
            src_hi = r.src_start + (hi - r_start)
            clip.append(EditRegion(r.source_path, src_lo, src_hi, lo - t0))
        if clip:
            self.clipboard = clip

    def cmd_cut(self):
        if not self.selection:
            return
        self.cmd_copy()
        self._push_undo()
        ti, t0, t1 = self.selection
        self._ripple_delete(self.tracks[ti], t0, t1)
        self.selection = None
        self.redraw()

    def cmd_delete(self):
        if not self.selection:
            return
        self._push_undo()
        ti, t0, t1 = self.selection
        self._ripple_delete(self.tracks[ti], t0, t1)
        self.selection = None
        self.redraw()

    def cmd_paste(self):
        if not self.clipboard:
            return
        ti = self.selection[0] if self.selection else self.playhead_track
        if ti is None or ti >= len(self.tracks):
            return
        self._push_undo()
        track = self.tracks[ti]
        ins_at = self.playhead
        clip_dur = max((c.track_offset + c.length) for c in self.clipboard)
        # 先把 ins_at 之後的內容往右推出空間（ripple insert）
        for r in track["regions"]:
            if r.track_offset >= ins_at:
                r.track_offset += clip_dur
            elif r.track_offset + r.length > ins_at:
                # 播放頭切在 region 中間 → 先分割再推
                pass
        for c in self.clipboard:
            track["regions"].append(EditRegion(c.source_path, c.src_start, c.src_end,
                                                ins_at + c.track_offset, c.fade_in, c.fade_out))
        track["regions"].sort(key=lambda r: r.track_offset)
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
                left = EditRegion(r.source_path, r.src_start, r.src_start + (t - r.track_offset),
                                  r.track_offset, fade_in=r.fade_in, fade_out=0.0)
                right = EditRegion(r.source_path, r.src_start + (t - r.track_offset), r.src_end,
                                   t, fade_in=0.0, fade_out=r.fade_out)
                track["regions"].remove(r)
                track["regions"].extend([left, right])
                track["regions"].sort(key=lambda rr: rr.track_offset)
                self.redraw()
                return

    def cmd_fade_in(self):
        region, ti = self._selection_or_edge_region(edge="in")
        if region is None:
            return
        self._push_undo()
        region.fade_in = max(region.fade_in, min(region.length, 0.3)) if region.fade_in <= 0 else region.fade_in
        if self.selection:
            _, t0, t1 = self.selection
            region.fade_in = max(0.0, min(region.length, t1 - region.track_offset))
        self.redraw()

    def cmd_fade_out(self):
        region, ti = self._selection_or_edge_region(edge="out")
        if region is None:
            return
        self._push_undo()
        if self.selection:
            _, t0, t1 = self.selection
            region.fade_out = max(0.0, min(region.length, (region.track_offset + region.length) - t0))
        else:
            region.fade_out = max(region.fade_out, min(region.length, 0.3))
        self.redraw()

    def _selection_or_edge_region(self, edge):
        track = self._active_track_regions()
        if track is None or not track["regions"]:
            return None, None
        if self.selection:
            ti, t0, t1 = self.selection
            anchor = t0 if edge == "in" else t1
            for r in track["regions"]:
                if r.track_offset <= anchor <= r.track_offset + r.length:
                    return r, ti
        regions = sorted(track["regions"], key=lambda r: r.track_offset)
        return (regions[0] if edge == "in" else regions[-1]), self.playhead_track

    # ---------- 播放預覽 ----------

    def toggle_play(self):
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def play(self):
        ti = self.playhead_track
        if ti is None or ti >= len(self.tracks):
            return
        track = self.tracks[ti]
        entry = track["entry"]
        base_audio = entry["audio"]
        rendered = self.app._render_region_list(track["regions"], base_audio.frame_rate, base_audio.channels)
        start_idx = int(self.playhead * base_audio.frame_rate)
        if start_idx >= len(rendered):
            start_idx = 0
            self.playhead = 0.0
        try:
            sd.stop()
            sd.play(rendered[start_idx:], samplerate=base_audio.frame_rate, device=self.app.get_selected_device())
        except Exception:
            return
        self.is_playing = True
        self._play_start_sys = time.time() - self.playhead
        self._play_sr = base_audio.frame_rate
        self._play_len = len(rendered)
        self.btn_play.configure(text="⏸")
        self._tick()

    def _tick(self):
        if not self.is_playing:
            return
        elapsed = time.time() - self._play_start_sys
        self.playhead = elapsed
        if elapsed * self._play_sr >= self._play_len:
            self.stop()
            return
        self.redraw()
        self.win.after(80, self._tick)

    def pause(self):
        sd.stop()
        self.is_playing = False
        self.btn_play.configure(text="▶")

    def stop(self):
        sd.stop()
        self.is_playing = False
        self.playhead = 0.0
        self.btn_play.configure(text="▶")
        self.redraw()

    def restart_from_head(self):
        """Enter：播放頭歸零並從頭開始播放（DAW 常見的「回到開頭」快捷鍵）。"""
        self.playhead = 0.0
        self.play()

    # ---------- 關閉：寫回非破壞性編輯記錄 ----------

    def on_close(self):
        self.pause()
        for t in self.tracks:
            entry = t["entry"]
            entry["edit_regions"] = [r.to_dict() for r in t["regions"]]
            dur = self.app._entry_edit_regions(entry)
            if dur is not None:
                total = max((r.track_offset + r.length for r in dur), default=0.0)
                m, s = divmod(int(total), 60)
                entry["duration"] = f"{m:02d}:{s:02d}"
                if entry.get("_table") and entry["_table"].exists(entry["path"]):
                    entry["_table"].set(entry["path"], "Duration", entry["duration"])
        self.app._schedule_autosave()
        self.app._schedule_wave_draw()
        self.win.destroy()
        self.app._edit_window = None


if __name__ == "__main__":
    app = AudioBalancerApp()
    # 雙擊 .abproj 開啟：PyInstaller 的 argv_emulation 會把 macOS 的「開啟檔案」事件轉成 sys.argv，
    # 在原本自動還原的 session 之後，再把該檔的工作區附加進來（與選單「開啟專案」行為一致）。
    _launch_path = next((a for a in sys.argv[1:] if a.lower().endswith(".abproj") and os.path.isfile(a)), None)
    if _launch_path:
        app.after(200, lambda p=_launch_path: app._open_project_path(p))
    app.mainloop()
