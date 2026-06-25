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

def _bundled_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).parent))

def find_ffmpeg():
    candidates = [
        str(_bundled_dir() / "ffmpeg"),
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/usr/bin/ffmpeg",
    ]
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    found = shutil.which("ffmpeg")
    return found if found else None

FFMPEG_BIN = find_ffmpeg()

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
                pass

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
        self.export_folder = ""

        # 自動存檔
        self._autosave_job = None

        # Undo stack：儲存 (action_type, [(path, old_target_lufs), ...])
        self._undo_stack: list = []
        # Guard 防止 slider ↔ entry 互相觸發
        self._updating_lufs = False

        self.setup_ui_styles()
        self.create_layout()

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

    # ========== Layout ==========

    def create_layout(self):
        # row 0: Top Bar, row 1: Tab Bar, row 2: Main Content (weight=1), row 3: Border, row 4: Bottom Bar
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # ==================== 頂部標題與匯入列 ====================
        self.top_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.top_bar.grid(row=0, column=0, padx=20, pady=(15, 10), sticky="ew")
        self.top_bar.columnconfigure(1, weight=1)

        self.top_title = ctk.CTkLabel(self.top_bar, text="Audio Loudness Balancer Assistant", font=("Roboto", 18, "bold"), text_color="#D1D1D6")
        self.top_title.grid(row=0, column=0, sticky="w")

        self.top_main_title = ctk.CTkLabel(self.top_bar, text="音量平衡輔助化工具", font=("Roboto", 24, "bold"), text_color="white")
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
        self.left_panel = ctk.CTkFrame(self._main_paned, fg_color=COLOR_PANEL, corner_radius=8)
        self._main_paned.add(self.left_panel, minsize=150, width=220, stretch="never")
        self.left_panel.rowconfigure(1, weight=1)
        self.left_panel.columnconfigure(0, weight=1)

        ctk.CTkLabel(self.left_panel, text="資料夾結構", font=("Roboto", 14, "bold"), text_color="white").grid(row=0, column=0, padx=10, pady=10, sticky="w")

        # Container 用於放置各工作區的 dir_tree
        self.left_content_container = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        self.left_content_container.grid(row=1, column=0, sticky="nsew")
        self.left_content_container.rowconfigure(0, weight=1)
        self.left_content_container.columnconfigure(0, weight=1)

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
        # 版面/視窗變動時，依目前尺寸重畫波形（多選大區放大後也正確填滿）
        self.waveform_canvas.bind("<Configure>", self._on_waveform_configure)
        # 視窗縮放時，多選版面的右側區寬度隨之調整（波形隨視窗變寬）
        self.bind("<Configure>", self._on_window_configure)

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
        self.btn_loop = ctk.CTkButton(self.transport_controls, text="🔁", width=35, height=30, fg_color="#3A3A3C", command=self.toggle_loop)
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
        self.lufs_entry.bind("<Return>",   self._on_lufs_entry_commit)
        self.lufs_entry.bind("<KP_Enter>", self._on_lufs_entry_commit)
        self.lufs_entry.bind("<FocusOut>", self._on_lufs_entry_commit)
        # 滑鼠滾輪在數值上、上下滑動即可微調（每格 0.1）
        self.lufs_entry.bind("<MouseWheel>", self._on_lufs_scroll)
        self.lufs_entry.bind("<Button-4>", self._on_lufs_scroll)   # 部分系統的滾輪上
        self.lufs_entry.bind("<Button-5>", self._on_lufs_scroll)   # 部分系統的滾輪下
        ctk.CTkLabel(self.t_lufs_frame, text="LUFS", font=("Arial", 12), text_color=COLOR_TEXT_DIM).pack(side="left", padx=(4, 0))
        # 一鍵恢復預設
        self.btn_lufs_reset = ctk.CTkButton(
            self.t_lufs_frame, text="↺", width=28, height=28,
            font=("Arial", 14), fg_color="#3A3A3C", hover_color="#4A4A4C",
            command=self._reset_lufs_to_default
        )
        self.btn_lufs_reset.pack(side="left", padx=(6, 0))
        self.lbl_suggest_lufs = ctk.CTkLabel(self.t_lufs_frame, text="", font=("Arial", 10), text_color="#888888")
        self.lbl_suggest_lufs.pack(side="left", padx=(8, 0))

        # 批次 ±Gain Fader（row=0/1，置於最上方，與 LUFS Fader 對調位置）；上下限 ±20 dB
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
        self.gain_adj_entry.bind("<Return>",   self._on_gain_entry_commit)
        self.gain_adj_entry.bind("<KP_Enter>", self._on_gain_entry_commit)
        self.gain_adj_entry.bind("<FocusOut>", self._on_gain_entry_commit)
        # 滑鼠滾輪在數值上、上下滑動即可微調（每格 0.1）
        self.gain_adj_entry.bind("<MouseWheel>", self._on_gain_scroll)
        self.gain_adj_entry.bind("<Button-4>", self._on_gain_scroll)
        self.gain_adj_entry.bind("<Button-5>", self._on_gain_scroll)
        ctk.CTkLabel(self.gain_adj_frame, text="dB", font=("Arial", 12), text_color=COLOR_TEXT_DIM).pack(side="left", padx=(4, 0))
        ctk.CTkButton(
            self.gain_adj_frame, text="套用", width=46, height=28,
            font=("Arial", 11), fg_color="#3A3A3C", hover_color="#4A4A4C",
            command=self._apply_global_gain
        ).pack(side="left", padx=(8, 0))
        self.btn_gain_reset = ctk.CTkButton(
            self.gain_adj_frame, text="↺", width=28, height=28,
            font=("Arial", 14), fg_color="#3A3A3C", hover_color="#4A4A4C",
            command=self._reset_gain_to_zero
        )
        self.btn_gain_reset.pack(side="left", padx=(6, 0))

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

        # 輸出裝置：單選版面放在音量表右側（前一版位置）；多選窄欄則移到下方。
        # 由 _apply_meter_layout() 依模式重新佈置。
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
        # 依目前模式佈置音量表＋裝置選單（單選：裝置在右側；多選：裝置在下方）
        self._apply_meter_layout(getattr(self, "_right_layout_multi", False))

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
        ctk.CTkLabel(self.settings_group, text="位元率:", font=("Arial", 11), text_color="#8E8E93").pack(side="left", padx=(0, 4))
        self.bit_menu = ctk.CTkOptionMenu(self.settings_group, values=BITRATES, fg_color="#3A3A3C", height=26, width=78, font=("Arial", 11), anchor="center")
        self.bit_menu.set("Original")
        self.bit_menu.configure(state="disabled")  # 預設 Original 格式 → disable
        self.bit_menu.pack(side="left", padx=(0, 10))
        self.silence_var = tk.BooleanVar(value=False)
        self.chk_silence = ctk.CTkCheckBox(self.settings_group, text="靜音移除", variable=self.silence_var,
                                           font=("Arial", 11), text_color="#8E8E93",
                                           fg_color="#00E5FF", hover_color="#00C8E0", checkmark_color="black")
        self.chk_silence.pack(side="left")
        if not FFMPEG_BIN:
            self.chk_silence.configure(state="disabled")

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
        self.bind("<space>", lambda e: None if self._focus_in_text_entry() else self.toggle_play_pause())
        self.bind("<Left>", lambda e: None if self._focus_in_text_entry() else self.seek_backward())
        self.bind("<Right>", lambda e: None if self._focus_in_text_entry() else self.seek_forward())
        self.bind("<Up>", lambda e: None if (self._focus_in_text_entry() or self.focus_get() in (self.file_table, self.dir_tree)) else self.select_prev_file())
        self.bind("<Down>", lambda e: None if (self._focus_in_text_entry() or self.focus_get() in (self.file_table, self.dir_tree)) else self.select_next_file())
        self.bind("<Delete>", lambda e: None if self._focus_in_text_entry() else self.remove_selected_files())
        self.bind("<BackSpace>", lambda e: None if self._focus_in_text_entry() else self.remove_selected_files())
        # 全選
        self.bind("<Command-a>", lambda e: None if self._focus_in_text_entry() else self._select_all())
        self.bind("<Control-a>", lambda e: None if self._focus_in_text_entry() else self._select_all())
        # Undo
        self.bind("<Command-z>", lambda e: None if self._focus_in_text_entry() else self._undo())
        self.bind("<Control-z>", lambda e: None if self._focus_in_text_entry() else self._undo())
        # 儲存 / 開啟整個專案
        self.bind("<Command-s>", lambda e: self._save_project())
        self.bind("<Control-s>",  lambda e: self._save_project())
        self.bind("<Command-o>", lambda e: self._open_project())
        self.bind("<Control-o>",  lambda e: self._open_project())

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

        ws.dir_tree = tree
        ws.left_panel_inner = inner_left

        # --- Center inner frame ---
        inner_center = ctk.CTkFrame(self.center_content_container, fg_color="transparent")
        inner_center.grid(row=0, column=0, sticky="nsew")
        inner_center.rowconfigure(0, weight=1)
        inner_center.columnconfigure(0, weight=1)
        inner_center.grid_remove()

        # 中央工作區：勾選（全選）擺在『真正的最左邊』→ 用 #0 樹欄當勾選欄（展開/收合箭頭也在這），
        # 檔名移到緊接其後的「檔案」欄。資料欄 values 依 cols 順序：(檔名, 時長, 狀態, 原始, 目標)。
        cols = ("檔案", "Duration", "Status", "原始 LUFS", "目標 LUFS")
        ft = ttk.Treeview(inner_center, columns=cols, show="tree headings", selectmode="extended")
        # 顯示順序：檔名緊接勾選欄之後、狀態欄擺最右。
        ft["displaycolumns"] = ("檔案", "Duration", "原始 LUFS", "目標 LUFS", "Status")
        ft.heading("#0", text="☑", command=lambda: self._toggle_all_exports())  # #0 = 勾選/全選
        ft.heading("檔案", text="檔案 / 資料夾")
        ft.heading("Duration", text="時長")
        ft.heading("Status", text="狀態")
        ft.heading("原始 LUFS", text="原始 LUFS")
        ft.heading("目標 LUFS", text="目標 LUFS")
        ft.column("#0", width=50, minwidth=46, anchor="center", stretch=False)   # 勾選欄（含展開箭頭）
        ft.column("檔案", width=180, minwidth=120, anchor="w", stretch=True)
        ft.column("Duration", width=74, minwidth=58, anchor="center", stretch=True)
        ft.column("Status", width=92, minwidth=72, anchor="center", stretch=True)
        ft.column("原始 LUFS", width=100, minwidth=84, anchor="center", stretch=True)
        ft.column("目標 LUFS", width=100, minwidth=84, anchor="center", stretch=True)
        ft.tag_configure("folder", foreground="#E0E0E0")
        ft.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        ft.bind("<<TreeviewSelect>>", self.on_table_select)
        ft.bind("<Button-1>", self._on_file_table_click)
        ft.bind("<Button-2>", self.on_table_right_click)
        ft.bind("<Button-3>", self.on_table_right_click)
        ft.bind("<Delete>", lambda e: self.remove_selected_files())
        ft.bind("<BackSpace>", lambda e: self.remove_selected_files())
        # 全選（Cmd/Ctrl+A）：直接綁在表格 widget 上。
        # macOS 上 Cmd+A 其實是被系統 Edit 選單攔截、再以虛擬事件 <<SelectAll>> 送到目前
        # 焦點 widget，所以「一定要」綁 <<SelectAll>>（這才是真正會收到的事件）；
        # 另外保險再綁 <Command-a>/<Control-a> 給非 macOS／直接按鍵的情況。
        ft.bind("<<SelectAll>>", self._select_all_files)
        ft.bind("<Command-a>", self._select_all_files)
        ft.bind("<Control-a>", self._select_all_files)
        if _DND_AVAILABLE:
            try:
                ft.drop_target_register(DND_FILES)
                ft.dnd_bind("<<Drop>>", self._on_drop_files)
            except Exception:
                pass

        ws.file_table = ft
        ws.center_panel_inner = inner_center

        return idx

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
        self._apply_right_layout(False)
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
            ws_data["audio_files"].append({
                "path": e["path"], "name": e["name"], "duration": e["duration"],
                "lufs": lufs_val, "target_lufs": target_val, "export": e.get("export", True),
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
            if not os.path.isfile(path):
                continue  # 檔案已被移走/刪除 → 略過
            lufs_saved = ef.get("lufs")
            target_saved = ef.get("target_lufs")
            dur_saved = ef.get("duration", "--:--")
            export_val = ef.get("export", True)
            entry = {
                "name": ef["name"], "path": path, "duration": dur_saved,
                "status": "🟡 載入中",
                "lufs": lufs_saved if lufs_saved is not None else "--",
                "target_lufs": target_saved, "audio": None, "export": export_val,
                "_table": ws.file_table,
            }
            ws.audio_files.append(entry)
            lufs_display = f"{lufs_saved:.1f} LUFS" if lufs_saved is not None else "--"
            target_display = f"{target_saved:.1f} LUFS" if target_saved is not None else "--"
            self._insert_file_row_into(ws.file_table, path, export_val,
                                       dur_saved, entry["status"], lufs_display, target_display)
            threading.Thread(target=self.analyze_single_file, args=(entry,), daemon=True).start()

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
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

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
        if not ws_list:
            return
        bind = (len(ws_list) == 1)
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
        valid_exts = ('.wav', '.mp3', '.flac', '.aiff', '.aif')
        raw = event.data or ""
        # tkinterdnd2 在 macOS 傳回的路徑用空格分隔，帶括號
        paths = self.tk.splitlist(raw)
        for p in paths:
            p = p.strip()
            if os.path.isfile(p) and p.lower().endswith(valid_exts):
                self.add_file_to_table(p)
            elif os.path.isdir(p):
                for fname in sorted(os.listdir(p)):
                    if fname.lower().endswith(valid_exts):
                        self.add_file_to_table(os.path.join(p, fname))

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
                nodes.append({
                    "name": tree.item(iid, "text"),
                    "path": ws.tree_item_paths.get(iid, ""),
                    "parent": index_of.get(parent_iid, -1),
                })
                index_of[iid] = len(nodes) - 1
                walk(iid)
        walk("")
        return nodes

    def _restore_dir_tree(self, ws, nodes):
        """由序列化節點清單重建左側目錄樹；磁碟上已不存在的檔案節點自動略過，避免幽靈項目。"""
        tree = ws.dir_tree
        tree.delete(*tree.get_children())
        ws.tree_item_paths.clear()
        iid_by_index = {}
        for i, n in enumerate(nodes):
            path = n.get("path", "")
            # 檔案節點（path 不是資料夾）若已不存在 → 跳過（葉節點，跳過不影響其他節點）
            if path and not os.path.isdir(path) and not os.path.isfile(path):
                iid_by_index[i] = None
                continue
            parent_iid = iid_by_index.get(n.get("parent", -1), "")
            if parent_iid is None:
                parent_iid = ""
            # 檔案節點 → 淡灰；資料夾節點 → 亮色
            tag = "dimfile" if (path and os.path.isfile(path)) else "dirfolder"
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
            with open(self._session_path(), "w", encoding="utf-8") as f:
                json.dump(self._project_data(), f, ensure_ascii=False, indent=2)
        except Exception:
            traceback.print_exc()

    def _on_close(self):
        if self._device_poll_job is not None:
            try:
                self.after_cancel(self._device_poll_job)
            except Exception:
                pass
        self._save_session()
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
        valid_exts = ('.wav', '.mp3', '.flac', '.aiff', '.aif', '.ogg', '.m4a')
        tree = ws.dir_tree
        root_node = tree.insert(parent_node, "end", text=os.path.basename(folder_path) or folder_path,
                                open=True, tags=("dirfolder",))
        ws.tree_item_paths[root_node] = folder_path
        node_map = {folder_path: root_node}

        for root, dirs, files in os.walk(folder_path):
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
        for ws_data in data["workspaces"]:
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
        """讀取 #0 勾選欄的狀態字（☑/☐）。"""
        return (table.item(iid, "text") or "").strip()

    @staticmethod
    def _set_check(table, iid, glyph):
        """設定 #0 勾選欄的狀態字。"""
        table.item(iid, text=glyph)

    def _sync_folder_check(self, table, file_iid):
        """子檔變動後，讓母資料夾的勾選字反映『底下是否全勾』。"""
        parent = table.parent(file_iid)
        if parent and table.tag_has("folder", parent):
            kids = table.get_children(parent)
            all_on = bool(kids) and all(self._get_check(table, k) == "☑" for k in kids)
            self._set_check(table, parent, "☑" if all_on else "☐")

    def _on_file_table_click(self, event):
        """點 #0 勾選欄切換勾選；點資料夾的勾選欄則一鍵切換其底下所有檔案。
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
            any_checked = any(self._get_check(tree, c) == "☑" for c in children)
            new_val = "☐" if any_checked else "☑"
            for c in children:
                self._set_check(tree, c, new_val)
                if ws:
                    entry = next((e for e in ws.audio_files if e["path"] == c), None)
                    if entry:
                        entry["export"] = (new_val == "☑")
            self._set_check(tree, item, new_val)
            self._schedule_autosave()
        else:
            new_val = "☐" if self._get_check(tree, item) == "☑" else "☑"
            self._set_check(tree, item, new_val)
            if ws:
                entry = next((e for e in ws.audio_files if e["path"] == item), None)
                if entry:
                    entry["export"] = (new_val == "☑")
                    self._schedule_autosave()
            self._sync_folder_check(tree, item)

    def _toggle_all_exports(self):
        """切換目前工作區所有檔案的匯出勾選（全選/全不選）。"""
        items = self._iter_file_iids()
        if not items:
            return
        # 若有任何一個是勾選的，就全部取消；否則全部勾選
        any_checked = any(self._get_check(self.file_table, item) == "☑" for item in items)
        new_val = "☐" if any_checked else "☑"
        for item in items:
            self._set_check(self.file_table, item, new_val)
            entry = next((e for e in self.audio_files if e["path"] == item), None)
            if entry:
                entry["export"] = (new_val == "☑")
        for top in self.file_table.get_children(""):
            if self.file_table.tag_has("folder", top):
                self._set_check(self.file_table, top, new_val)
        self._schedule_autosave()

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
        """格式切換時，動態 enable/disable 位元率選單。"""
        is_lossy = fmt.lower() in LOSSY_FORMATS
        if is_lossy:
            self.bit_menu.configure(state="normal")
            if self.bit_menu.get() == "Original":
                self.bit_menu.set("128")
        else:
            self.bit_menu.configure(state="disabled")
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
            current = []

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
                if path and os.path.isfile(path):
                    return 1  # 葉節點音檔
                if iid != "" and (not path or os.path.isdir(path)):
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
            filetypes=[("音訊檔", "*.wav *.mp3 *.flac *.aiff *.aif *.ogg *.m4a"),
                       ("所有檔案", "*.*")],
        )
        if not paths:
            return
        ws = self.workspaces[self.active_ws_idx]
        self._add_files_to_dir_tree(ws, list(paths))
        self._schedule_autosave()

    def _add_files_to_dir_tree(self, ws, paths):
        """把選取的音檔加入左側目錄樹：依母資料夾分組、去重複、保留現有內容。"""
        valid_exts = ('.wav', '.mp3', '.flac', '.aiff', '.aif', '.ogg', '.m4a')
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
        valid_exts = ('.wav', '.mp3', '.flac', '.aiff', '.aif', '.ogg', '.m4a')
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
            AUDIO_EXTS = ('.wav', '.mp3', '.flac', '.aiff', '.aif', '.ogg', '.m4a')
            existing_paths = {f["path"] for f in self.audio_files}
            for _, full_path in self.drag_items:
                if os.path.isfile(full_path):
                    if full_path not in existing_paths:
                        self.add_file_to_table(full_path)
                        existing_paths.add(full_path)
                elif os.path.isdir(full_path):
                    for fname in sorted(os.listdir(full_path)):
                        fpath = os.path.join(full_path, fname)
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
            table.insert("", "end", iid=folder_iid, text="☑",
                         values=(f"📁 {folder_name}", "", "", "", ""), tags=("folder",), open=True)
        return folder_iid

    def _insert_file_row_into(self, table, file_path, export_val, dur, status, lufs_display, target_display):
        """把單一檔案列插入對應母資料夾節點底下（tree headings 階層結構）。
        #0 樹欄當勾選欄（☑/☐），檔名放在緊接其後的「檔案」欄。"""
        folder_iid = self._ensure_folder_node(table, file_path)
        if table.exists(file_path):
            return  # 已存在則略過，避免重複
        table.insert(folder_iid, "end", iid=file_path, text=("☑" if export_val else "☐"),
                     values=(os.path.basename(file_path), dur, status, lufs_display, target_display),
                     tags=("file",))

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
        """移除底下已無檔案的母資料夾分組節點。"""
        table = table or self.file_table
        for top in list(table.get_children("")):
            if table.tag_has("folder", top) and not table.get_children(top):
                table.delete(top)

    def add_file_to_table(self, file_path):
        fname = os.path.basename(file_path)
        entry = {"name": fname, "path": file_path, "duration": "--:--", "status": "🟡 載入中",
                 "lufs": "--", "target_lufs": None, "audio": None, "export": True,
                 "_table": self.file_table}
        self.audio_files.append(entry)
        # 依「母資料夾」自動分組顯示（上方可展開／收合）
        self._insert_file_row_into(self.file_table, file_path, True,
                                   entry["duration"], entry["status"], entry["lufs"], "--")
        threading.Thread(target=self.analyze_single_file, args=(entry,), daemon=True).start()
        self.check_export_ready()
        self._schedule_autosave()

    def _focus_in_text_entry(self):
        """目前鍵盤焦點是否落在任何文字輸入框內。

        customtkinter 的 CTkEntry 內層是 tkinter.Entry，focus_get() 會回傳內層的
        tk.Entry，因此兩種型別都要判斷；否則在右側參數欄（LUFS、批次 ±Gain、
        資料夾名稱…）打字時，全域快捷鍵會誤觸到中間工作區的操作。
        """
        return isinstance(self.focus_get(), (ctk.CTkEntry, tk.Entry))

    def remove_selected_files(self):
        selected = self.file_table.selection()
        # 選到資料夾節點時，展開成其底下所有檔案一併移除
        file_iids = []
        for iid in selected:
            if self.file_table.tag_has("folder", iid):
                file_iids.extend(self.file_table.get_children(iid))
            else:
                file_iids.append(iid)

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
        self.check_export_ready()
        self._schedule_autosave()

    def on_table_right_click(self, event):
        selected = self.file_table.selection()
        if selected:
            menu = tk.Menu(self, tearoff=0)
            menu.add_command(label=f"移除選取的 {len(selected)} 個檔案",
                            command=lambda: self.remove_selected_files())
            menu.post(event.x_root, event.y_root)

    def analyze_single_file(self, entry):
        try:
            audio = AudioSegment.from_file(entry["path"])
            entry["audio"] = audio

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
                if samples.ndim == 1:
                    analysis_samples = np.pad(samples, (0, pad_length), mode='constant')
                else:
                    analysis_samples = np.pad(samples, ((0, pad_length), (0, 0)), mode='constant')
                lufs = meter.integrated_loudness(analysis_samples)
            else:
                lufs = meter.integrated_loudness(samples)

            entry["lufs"] = lufs
            if entry.get("target_lufs") is None:
                entry["target_lufs"] = lufs  # 預設目標 = 原始 LUFS（不改音量）
            entry["status"] = "🟢 就緒"
            target_display = f"{entry['target_lufs']:.1f} LUFS"
            self._enqueue_ui(self.update_table_row, entry["path"], entry["duration"], entry["status"],
                             f"{lufs:.1f} LUFS", target_display, entry.get("_table"))
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

    def update_table_row(self, iid, dur, status, lufs, target_lufs=None, table=None):
        # 還原 session 時會同時分析多個工作區的檔案，每個工作區各有自己的
        # file_table；用 entry 記住的 table 路由到正確的那個表，沒帶就更新作用中的。
        table = table or self.file_table
        if table.exists(iid):
            table.set(iid, "Duration", dur)
            table.set(iid, "Status", status)
            table.set(iid, "原始 LUFS", lufs)
            if target_lufs is not None:
                table.set(iid, "目標 LUFS", target_lufs)

    def on_table_select(self, event):
        if event is not None and hasattr(event, 'widget'):
            event.widget.focus_set()  # 確保鍵盤 focus 在 file_table 上
        selected = self.file_table.selection()
        # 只取「檔案」節點（略過母資料夾分組節點）
        file_sel = [s for s in selected if not self.file_table.tag_has("folder", s)]
        # 換選取 → 批次 Gain 滑桿歸零、解除 baseline（已套用到檔案的目標值會保留）
        if hasattr(self, "gain_adj_var"):
            if getattr(self, "_gain_apply_job", None):
                try:
                    self.after_cancel(self._gain_apply_job)
                except Exception:
                    pass
                self._gain_apply_job = None
            self.gain_adj_var.set(0.0)
            self.gain_entry_var.set("0.0")
            self._gain_active = False
        if not file_sel:
            self._current_wave_entries = []
            self._multi_bands = []
            self._apply_right_layout(False)
            return

        path = file_sel[0]  # 以第一個選取檔案為主檔（播放／LUFS 控制對象）
        fname = os.path.basename(path)
        if len(file_sel) > 1:
            self.lbl_active_file.configure(text=f"{fname}　（已選 {len(file_sel)} 個）")
        else:
            self.lbl_active_file.configure(text=fname)
        self.stop_playback()

        entry = next((item for item in self.audio_files if item["path"] == path), None)
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

        # 波形：多選 → 多軌疊圖（並把右側切成左波形、右參數）；單選 → 單一波形。
        # 大量選取（如 Cmd+A 全選）時，逐軌解碼＋繪製會卡死 UI，故：
        #   1) 用 dict 查表，避免 O(N²) 線性搜尋；
        #   2) 繪圖去抖動（_schedule_wave_draw），連續選取只畫最後一次；
        #   3) 軌數過多時在 draw_multi_waveforms 內改顯示摘要、不逐軌解碼。
        by_path = {it["path"]: it for it in self.audio_files}
        sel_entries = []
        for p in file_sel:
            e = by_path.get(p)
            if e and e.get("audio") is not None:
                sel_entries.append(e)
        self._current_wave_entries = sel_entries
        self._apply_right_layout(len(sel_entries) > 1)
        self._schedule_wave_draw()

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
            if len(entries) > 1:
                self.draw_multi_waveforms(entries)
            elif len(entries) == 1:
                self.draw_waveform(entries[0]["audio"])
            else:
                self.waveform_canvas.delete("all")
        except Exception:
            traceback.print_exc()

    def draw_waveform(self, audio):
        self.waveform_canvas.delete("all")
        self._playhead_band = None  # 單軌顯示 → 播放桿畫滿整個高度
        self._multi_bands = []      # 單軌顯示 → 沒有可點選的多軌
        width = self.waveform_canvas.winfo_width()
        height = self.waveform_canvas.winfo_height()

        if width <= 1 or height <= 1:
            width = 370
            height = 120
        self._active_track_width = width  # 單軌：播放桿/seek 以整寬為基準

        samples = np.array(audio.get_array_of_samples())
        if audio.channels > 1:
            samples = samples.reshape((-1, audio.channels)).mean(axis=1)

        chunk_size = max(1, len(samples) // width)
        peaks = []
        for i in range(0, len(samples), chunk_size):
            chunk = samples[i:i+chunk_size]
            if len(chunk) > 0:
                peaks.append(np.max(np.abs(chunk)))

        if not peaks: return

        max_peak = max(peaks) if max(peaks) > 0 else 1
        normalized_peaks = [p / max_peak for p in peaks]

        center_y = height / 2
        for x, peak in enumerate(normalized_peaks):
            line_height = peak * (height / 2) * 0.9
            self.waveform_canvas.create_line(x, center_y - line_height, x, center_y + line_height, fill="#4DA6FF")

    def draw_multi_waveforms(self, entries):
        """多選時：把右側波形區垂直切成多軌，各檔案各畫一條波形。
        每軌的『水平長度』依時長等比縮放（最長的填滿整寬），並在檔名後標出時間，
        讓使用者一眼量化出哪些長、哪些短。"""
        self.waveform_canvas.delete("all")
        width = self.waveform_canvas.winfo_width()
        height = self.waveform_canvas.winfo_height()
        if width <= 1 or height <= 1:
            width, height = 370, 100

        n = len(entries)
        # 軌數過多時不逐軌解碼/繪製（全選大量檔案會把 UI 卡死），改顯示精簡摘要。
        # 此時逐軌波形也太細沒有意義；對選取的批次操作（LUFS 等）不受影響。
        MAX_WAVE_TRACKS = 12
        if n > MAX_WAVE_TRACKS:
            self._multi_bands = []
            self._playhead_band = None
            cx, cy = width / 2, height / 2
            self.waveform_canvas.create_text(cx, cy - 9, text=f"已選取 {n} 個檔案",
                                             fill="#E5E5EA", font=("Arial", 13, "bold"))
            self.waveform_canvas.create_text(cx, cy + 13, text="（檔案較多，已略過逐軌波形預覽）",
                                             fill="#8E8E93", font=("Arial", 10))
            return
        band_h = height / n
        color = "#4DA6FF"          # 波形統一藍色（播放桿維持青色 #00E5FF 以保持對比）
        END_COLOR = "#3A3A3C"      # 各軌結尾的長度刻度線
        MIN_W = 16                 # 極短音檔仍保留可見/可點寬度

        # 先取得每軌時長，換算成最長者填滿整寬的等比寬度
        durs = []
        for e in entries:
            a = e.get("audio")
            durs.append(a.duration_seconds if a is not None else 0.0)
        max_dur = max(durs) if durs and max(durs) > 0 else 1.0

        playing_path = getattr(self, "current_file_path", None)
        playing_band = None
        self._multi_bands = []  # (上緣, 下緣, entry, 該軌像素寬) → 供點選切換 / seek
        for idx, entry in enumerate(entries):
            audio = entry.get("audio")
            band_top = idx * band_h
            center_y = band_top + band_h / 2
            band_bottom = height if idx == n - 1 else band_top + band_h
            dur = durs[idx]
            track_w = max(MIN_W, width * (dur / max_dur)) if audio is not None else MIN_W
            track_w = min(track_w, width)
            self._multi_bands.append((band_top, band_bottom, entry, track_w))
            is_active = (entry["path"] == playing_path)
            if is_active:
                playing_band = (band_top, band_top + band_h)
                self._active_track_width = track_w
                # 主軌：整列淡底 + 左側強調條，一眼看出選到哪一軌
                self.waveform_canvas.create_rectangle(0, band_top, width, band_bottom, fill="#1B1B22", outline="")
                self.waveform_canvas.create_rectangle(0, band_top, 4, band_bottom, fill=color, outline="")

            if idx > 0:  # 軌與軌之間的分隔線
                self.waveform_canvas.create_line(0, band_top, width, band_top, fill="#2A2A2C")

            if audio is not None:
                samples = np.array(audio.get_array_of_samples())
                if audio.channels > 1:
                    samples = samples.reshape((-1, audio.channels)).mean(axis=1)
                w = max(1, int(track_w))
                chunk_size = max(1, len(samples) // w)
                peaks = []
                for i in range(0, len(samples), chunk_size):
                    chunk = samples[i:i + chunk_size]
                    if len(chunk) > 0:
                        peaks.append(np.max(np.abs(chunk)))
                if peaks:
                    max_peak = max(peaks) if max(peaks) > 0 else 1
                    amp = (band_h / 2) * 0.78
                    for x, peak in enumerate(peaks):
                        lh = (peak / max_peak) * amp
                        self.waveform_canvas.create_line(x, center_y - lh, x, center_y + lh, fill=color)

            # 每軌結尾畫一條淡色刻度線，明確標出此音檔的長度位置
            self.waveform_canvas.create_line(track_w, band_top + 2, track_w, band_bottom - 2, fill=END_COLOR)

            # 主軌整軌外框（只框到該軌長度，凸顯目前可播放音檔的實際長度）
            if is_active:
                self.waveform_canvas.create_rectangle(1, band_top + 1, max(track_w, 6), band_bottom - 1, outline=color, width=2)

            # 檔名 + 時長標籤（量化呈現）；主軌加深色底牌 + ▶ 前綴
            label = f"{os.path.basename(entry['path'])}  ·  {self.format_time(dur)}"
            if is_active:
                txt = self.waveform_canvas.create_text(10, band_top + 11, anchor="w",
                                                       text="▶ " + label, fill=color, font=("Arial", 9, "bold"))
                bb = self.waveform_canvas.bbox(txt)
                if bb:
                    self.waveform_canvas.create_rectangle(bb[0] - 4, bb[1] - 2, bb[2] + 4, bb[3] + 2, fill="#0A0A0C", outline="")
                    self.waveform_canvas.tag_raise(txt)
            else:
                t2 = self.waveform_canvas.create_text(5, band_top + 9, anchor="w",
                                                      text=label, fill=color, font=("Arial", 9, "bold"))
                bb = self.waveform_canvas.bbox(t2)
                if bb:
                    self.waveform_canvas.create_rectangle(bb[0] - 3, bb[1] - 1, bb[2] + 3, bb[3] + 1, fill="#15151A", outline="")
                    self.waveform_canvas.tag_raise(t2)

        # 播放桿只畫在「正在播放的主檔」那一軌（找不到主檔時預設第一軌）
        if playing_band is None and n > 0:
            playing_band = (0, band_h)
            if self._multi_bands:
                self._active_track_width = self._multi_bands[0][3]
        self._playhead_band = playing_band

    def _playhead_yrange(self):
        """播放桿的垂直範圍：多選時限定在正在播放的那一軌，否則畫滿整個高度。"""
        band = getattr(self, "_playhead_band", None)
        if band is None:
            return 0, self.waveform_canvas.winfo_height()
        return band[0], band[1]

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
        if len(entries) > 1:
            self.draw_multi_waveforms(entries)
        elif len(entries) == 1:
            self.draw_waveform(entries[0]["audio"])

    def _multi_right_width(self):
        """多選時右側區（波形＋參數）的目標寬度：隨視窗寬度縮放，
        中間清單保留足夠寬度顯示完整欄位；右側區越寬，波形也越寬。"""
        try:
            win_w = self.winfo_width()
        except Exception:
            win_w = 1280
        if win_w < 700:
            win_w = 1280  # 視窗尚未 realize，先用預設值
        LEFT = 235        # 左側資料夾樹 + sash
        CENTER_MIN = 495  # 中間檔案清單至少保留這麼寬（完整顯示原始/目標 LUFS 欄）
        right = win_w - LEFT - CENTER_MIN
        return int(max(520, min(right, 1200)))

    def _on_window_configure(self, event=None):
        """視窗大小改變時，若在多選版面則重算右側區寬度（波形隨視窗放大而變寬）。"""
        if event is not None and event.widget is not self:
            return
        if getattr(self, "_layout_settling", False):
            return  # 版面切換凍結期：不重算右側寬度
        if not getattr(self, "_right_layout_multi", False):
            return
        if getattr(self, "_winsize_job", None):
            try:
                self.after_cancel(self._winsize_job)
            except Exception:
                pass
        self._winsize_job = self.after(150, self._refresh_multi_width)

    def _refresh_multi_width(self):
        self._winsize_job = None
        if getattr(self, "_right_layout_multi", False):
            try:
                self._main_paned.paneconfigure(self.right_panel, width=self._multi_right_width())
            except Exception:
                pass

    def _apply_meter_layout(self, multi):
        """音量表與輸出裝置選單的佈置：
        單選（參數欄較寬）→ 裝置選單放在音量表右側（與前一版相同）；
        多選（參數欄較窄）→ 裝置選單移到音量表下方、佔滿整列，避免被擠壓。"""
        lw = self.lufs_wrapper
        if multi:
            lw.columnconfigure(0, weight=1)
            lw.columnconfigure(1, weight=1)
            self.meter_frame.grid_configure(row=5, column=0, columnspan=2, sticky="")
            self.device_frame.grid_configure(row=6, column=0, columnspan=2, sticky="ew", padx=20, pady=(2, 12))
            try:
                self.device_menu.pack_configure(fill="x", anchor="nw")
            except Exception:
                pass
        else:
            lw.columnconfigure(0, weight=0)
            lw.columnconfigure(1, weight=1)
            self.meter_frame.grid_configure(row=5, column=0, columnspan=1, sticky="w")
            self.device_frame.grid_configure(row=5, column=1, columnspan=1, sticky="nw", padx=(8, 0), pady=(8, 14))
            try:
                self.device_menu.pack_configure(fill="none", anchor="nw")
            except Exception:
                pass

    def _apply_right_layout(self, multi):
        """多選時：波形整組移到左側獨立一欄，播放器＋參數欄移到右側並加寬右側面板；
        單選／無選取時還原為原本的單欄垂直堆疊。只在模式切換時重排一次。

        ⚠️ 前提：lufs_wrapper 必須是「純 CTkFrame」（見其建立處說明）。若改回
        CTkScrollableFrame，這裡的重排會踩到 CTk 內部 <Configure> 無限遞迴而 100% CPU 卡死。
        另外這裡「不可」呼叫 update_idletasks()——波形重畫已用 _schedule_wave_draw 去抖動排程，
        幾何會在事件迴圈自然收斂。"""
        if getattr(self, "_right_layout_multi", False) == multi:
            return
        self._right_layout_multi = multi
        # 切換版面是一次劇烈的幾何變動，會同時驚動多個「因 <Configure> 改幾何」的回饋
        # （CTk 捲動框配適、左樹欄寬、捲軸自動隱藏、波形重畫…），彼此互觸成無限迴圈卡死。
        # 對策：切換期間先「凍結」這些回饋，讓 Tk 幾何自行收斂，再做一次乾淨的最終配置。
        self._layout_settling = True
        rp = self.right_panel
        if multi:
            try:
                self._main_paned.paneconfigure(rp, width=self._multi_right_width())
            except Exception:
                pass
            # 波形欄伸縮、參數欄固定寬 → 視窗變寬時多出來的空間都給波形（更容易看出長短）。
            rp.columnconfigure(0, weight=1, minsize=250)   # 波形（獨立左欄、伸縮）
            rp.columnconfigure(1, weight=0, minsize=330)   # 播放器＋參數＋音量表（固定寬右欄）
            rp.rowconfigure(1, weight=0)
            rp.rowconfigure(2, weight=1)   # 參數捲動框吃滿剩餘高度 → 視窗矮時內部捲動
            rp.rowconfigure(3, weight=0)
            self.lbl_active_file.grid_configure(row=0, column=0, columnspan=2, sticky="w")
            self.waveform_canvas.grid_configure(row=1, column=0, rowspan=2, sticky="nsew", pady=(5, 12))
            self.player_frame.grid_configure(row=1, column=1, rowspan=1, sticky="new")
            self.lufs_wrapper.grid_configure(row=2, column=1, rowspan=1, sticky="nsew")
        else:
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
        # 音量表/裝置選單依模式佈置（單選：裝置在右側；多選：裝置在下方）
        self._apply_meter_layout(multi)
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
            self.draw_waveform(self.current_audio)

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
        self._schedule_autosave()

    def _on_lufs_slider(self, val):
        """LUFS 滑桿拖曳：每一格只更新「大數字」（最輕量，與批次 dB 滑桿一致）；
        資訊卡、寫入檔案與表格（多選時很重）全部去抖動到停手後才做，讓拖曳順暢不卡。"""
        val = float(val)
        self._ensure_ab_target()
        self.lufs_entry_var.set(f"{val:.1f}")
        self._pending_lufs_val = val
        if getattr(self, "_lufs_apply_job", None):
            try:
                self.after_cancel(self._lufs_apply_job)
            except Exception:
                pass
        self._lufs_apply_job = self.after(50, self._flush_lufs_apply)

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

                self.playback_data = self.apply_soft_clipper(samples_float)
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
        self.fade_meters_to_zero()
        self.play_btn.configure(text="▶", command=self.play_original)

    def stop_playback(self):
        sd.stop()
        self.is_playing = False
        self.pause_position = 0
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

    def on_waveform_click(self, event):
        if not self.current_audio: return
        # 多選多軌：判斷按下的是哪一軌，若不是目前主軌 → 切換成可播放的主檔
        bands = getattr(self, "_multi_bands", None)
        if bands:
            target_entry = None
            target_w = None
            for top, bottom, entry, tw in bands:
                if top <= event.y < bottom:
                    target_entry = entry
                    target_w = tw
                    break
            if target_entry is None:
                target_entry = bands[-1][2]  # 點在最後一軌之外 → 取最後一軌
                target_w = bands[-1][3]
            if target_entry["path"] != getattr(self, "current_file_path", None):
                # 以被點選那一軌的實際寬度換算 seek 比例（各軌寬度依時長不同）
                ratio = max(0.0, min(1.0, event.x / target_w)) if target_w and target_w > 1 else 0.0
                self._set_active_multi_track(target_entry, seek_ratio=ratio)
                return
        # 點到的是目前主軌（或單軌）→ 照常在這一軌內 seek
        self._seek_current_track(event)

    def on_waveform_drag(self, event):
        # 拖曳只在目前主軌內 seek，不跨軌切換（避免拖過邊界時一直切換）
        self._seek_current_track(event)

    def on_waveform_release(self, event):
        pass

    def _set_active_multi_track(self, entry, seek_ratio=0.0):
        """多選多軌時：把點選的那一軌設為目前可播放的主檔，播放桿/音量表/LUFS 控制都跟著切過去。"""
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

        # 切檔等同重選 → 重置播放快取，讓 play_original 以新檔重建播放資料
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

        # 重畫多軌：播放桿會依新的 current_file_path 落到對應軌
        live = [e for e in getattr(self, "_current_wave_entries", []) if e.get("audio") is not None]
        if len(live) > 1:
            self.draw_multi_waveforms(live)

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
        self.loop_var.set(not self.loop_var.get())
        if self.loop_var.get():
            self.btn_loop.configure(fg_color=COLOR_CYAN, text_color="black")
        else:
            self.btn_loop.configure(fg_color="#3A3A3C", text_color="white")

    def on_scrub(self, val):
        if self.current_audio:
            dur = self.current_audio.duration_seconds
            self.lbl_time.configure(text=f"{self.format_time(val)} / {self.format_time(dur)}")
            self.pause_position = float(val)
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

    def _reset_lufs_to_default(self):
        """↺：把選取的「每個」檔案的目標 LUFS 各自還原成自己的『原始 LUFS』。
        多選時不會把所有檔案變成同一個值，而是各自回到各自量到的原始響度。"""
        selected = self.file_table.selection()
        paths = [p for p in selected if not self.file_table.tag_has("folder", p)] if selected else (
            [self.current_file_path] if self.current_file_path else [])
        if not paths:
            return
        self._push_lufs_undo()  # 仍可用 Cmd+Z 回復這個動作
        for p in paths:
            entry = next((e for e in self.audio_files if e["path"] == p), None)
            if not entry:
                continue
            orig = entry["lufs"] if isinstance(entry.get("lufs"), float) else None
            if orig is None:
                continue  # 尚未量到原始 LUFS 的檔案略過
            entry["target_lufs"] = orig
            if self.file_table.exists(p):
                self.file_table.set(p, "目標 LUFS", f"{orig:.1f} LUFS")
        # 右側 fader／資訊卡顯示「目前主檔」的原始值（不再把全部選取設成同一個數）
        cur = next((e for e in self.audio_files if e["path"] == self.current_file_path), None)
        if cur and isinstance(cur.get("target_lufs"), float):
            self.target_lufs_var.set(cur["target_lufs"])
            self.update_target_lufs(cur["target_lufs"], from_selection=True)
        self._schedule_autosave()

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
        v = round(max(-40.0, min(-1.0, self.target_lufs_var.get() + 0.1 * self._scroll_dir(event))), 1)
        self._ensure_ab_target()
        self.target_lufs_var.set(v)
        self.update_target_lufs(v)
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
        # 0 附近阻尼：±1.0 dB 內吸附到 0（拖過去會明顯「卡」一下並固定在 0，方便快速歸零）
        if abs(val) < 1.0:
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

    def _capture_gain_baseline(self):
        """以目前選取（或主檔）的目標 LUFS 當作批次平移的基準，並推一筆 undo（可 Cmd+Z 還原）。"""
        sel = [p for p in self.file_table.selection() if not self.file_table.tag_has("folder", p)]
        if not sel and getattr(self, "current_file_path", None):
            sel = [self.current_file_path]
        self._gain_baseline = {}
        snapshot = []
        for p in sel:
            e = next((it for it in self.audio_files if it["path"] == p), None)
            if e:
                base = e["target_lufs"] if isinstance(e.get("target_lufs"), float) else None
                self._gain_baseline[p] = base
                snapshot.append((p, base))
        if snapshot:
            self._undo_stack.append(("lufs_change", snapshot))
            if len(self._undo_stack) > 50:
                self._undo_stack = self._undo_stack[-50:]

    def _ensure_gain_baseline(self, offset):
        """批次值從 0 變成非 0 的瞬間鎖定目前目標值為 baseline；回到 0 時解除。
        如此拖曳是相對位移（不會累加），且不受先前用 LUFS 滑桿改過的值影響。"""
        if abs(offset) < 1e-9:
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
        cur = next((e for e in self.audio_files if e["path"] == getattr(self, "current_file_path", None)), None)
        if cur and isinstance(cur.get("target_lufs"), float):
            self.target_lufs_var.set(cur["target_lufs"])
            self.update_info_cards()
        self._schedule_autosave()

    def _reset_gain_to_zero(self):
        """↺：把滑桿歸零並讓選取檔案回到 baseline（移除目前這次的批次平移）。"""
        if getattr(self, "_gain_apply_job", None):
            try:
                self.after_cancel(self._gain_apply_job)
            except Exception:
                pass
            self._gain_apply_job = None
        if getattr(self, "_gain_active", False):
            self._apply_gain_offset(0.0)  # 回到 baseline
        self.gain_adj_var.set(0.0)
        self.gain_entry_var.set("0.0")
        self._gain_active = False

    def _apply_global_gain(self):
        """『套用』：把目前的批次平移固定下來（拖曳時已即時套用），滑桿歸零、
        並以目前（已套用）的值為新基準，方便再往上疊加。"""
        if getattr(self, "_gain_apply_job", None):
            try:
                self.after_cancel(self._gain_apply_job)
            except Exception:
                pass
            self._gain_apply_job = None
        self._apply_gain_offset(self.gain_adj_var.get())  # 確保最後一次位移已落地
        self.gain_adj_var.set(0.0)
        self.gain_entry_var.set("0.0")
        self._gain_active = False
        self._schedule_autosave()

    # ─────────────────────────────────────────────────────────
    # 全選（Cmd+A）
    # ─────────────────────────────────────────────────────────

    def _select_all_files(self, event=None):
        """中間工作區表格的 Cmd/Ctrl+A：選取該表格內所有檔案節點。
        直接綁在表格 widget 上、回傳 "break" 攔截，確保不被 ttk.Treeview 的 class 綁定吃掉。"""
        table = event.widget if (event is not None and hasattr(event, "widget")) else self.file_table
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
                return
        items = self._iter_file_iids()
        if items:
            self.file_table.selection_set(items)

    def _get_all_tree_items(self, tree, parent=""):
        items = list(tree.get_children(parent))
        for item in list(items):
            items.extend(self._get_all_tree_items(tree, item))
        return items

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
        if self.current_file_path:
            cur = next((e for e in self.audio_files if e["path"] == self.current_file_path), None)
            if cur and isinstance(cur.get("target_lufs"), float):
                self.target_lufs_var.set(cur["target_lufs"])
                self.update_target_lufs(cur["target_lufs"], from_selection=True)
        self._schedule_autosave()

    def check_export_ready(self):
        ws = self.workspaces[self.active_ws_idx]
        if ws.audio_files and self.export_folder:
            self.btn_export.configure(state="normal", text_color="white")
        else:
            self.btn_export.configure(state="disabled", text_color="gray")

    def _update_export_path_label(self):
        """顯示完整輸出路徑（不再截斷，避免路徑名稱被吃掉）。"""
        try:
            self.lbl_export_path.configure(text=self.export_folder or "輸出:/尚未設定")
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

        # 找出所有「真的有檔案會被匯出」的工作區（就緒且有勾選），與計數一致
        exportable = [ws for ws in self.workspaces if self._ready_export_count(ws) > 0]
        if not exportable:
            return

        fmt = self.format_menu.get()
        sr  = self.sr_menu.get()
        br  = self.bit_menu.get()
        silence_remove = self.silence_var.get()

        # 提醒：輸出格式仍是預設的「Original」（＝尚未指定要轉成哪種格式）。
        # 此時只會做響度平衡、維持原始副檔名（例如 .wav 仍輸出 .wav），不做轉檔。
        # 讓使用者確認，避免「以為沒選格式卻還是輸出了」的疑惑。
        if fmt == "Original":
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

        self.btn_export.configure(state="disabled", text="⏳ 匯出中...")
        threading.Thread(target=self.export_process, args=(fmt, selected_workspaces, sr, br, silence_remove), daemon=True).start()

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

    def export_process(self, fmt, workspaces, sr="Original", br="Original", silence_remove=False):
        custom_name = self.folder_name_entry.get().strip()
        multi = len(workspaces) > 1
        # 靜音移除需要 FFmpeg：即使輸出格式是 Original，只要勾了靜音移除也要走 FFmpeg
        # （之前 Original 格式會跳過 FFmpeg → 靜音移除完全沒作用，這裡修正）
        use_ffmpeg = bool(FFMPEG_BIN) and (fmt.lower() != "original" or silence_remove)

        for ws in workspaces:
            if multi:
                ws_suffix = "_" + ws.name.replace(" ", "_")
                folder_base = (custom_name + ws_suffix) if custom_name else ws.name.replace(" ", "_")
            else:
                folder_base = custom_name if custom_name else ws.name.replace(" ", "_")

            target_dir = os.path.join(self.export_folder, folder_base)
            os.makedirs(target_dir, exist_ok=True)

            for entry in ws.audio_files:
                if entry["status"] != "🟢 就緒" or entry["audio"] is None:
                    continue
                if not entry.get("export", True):
                    continue

                try:
                    # ── Step 1: 套用 LUFS 增益 + Soft Clipper（現有邏輯）──
                    target_lufs = entry.get("target_lufs", -16.0)
                    gain_db = target_lufs - entry["lufs"]
                    linear_gain = 10 ** (gain_db / 20.0)

                    base_audio = entry["audio"]
                    samples = np.array(base_audio.get_array_of_samples())
                    max_val = float(2 ** (8 * base_audio.sample_width - 1))

                    samples_float = (samples.astype(np.float32) / max_val) * linear_gain
                    clipped_samples_float = self.apply_soft_clipper(samples_float)
                    clipped_samples_float = np.clip(clipped_samples_float, -1.0, 1.0)
                    clipped_samples_int = (clipped_samples_float * max_val).astype(samples.dtype)
                    output_audio = base_audio._spawn(clipped_samples_int.tobytes())

                    # ── Step 2: 決定輸出副檔名 ──
                    original_ext = os.path.splitext(entry["name"])[1].lower()
                    save_ext = original_ext if fmt == "Original" else "." + fmt.lower()
                    save_name = os.path.splitext(entry["name"])[0] + save_ext
                    # 保留當初 Import 進來的最上層資料夾名稱（例如 BaseGame）為一層子資料夾
                    sub = self._export_subpath_for(ws, entry["path"])
                    out_dir = os.path.join(target_dir, sub) if sub else target_dir
                    os.makedirs(out_dir, exist_ok=True)
                    save_path = os.path.join(out_dir, save_name)

                    if use_ffmpeg:
                        # ── Step 3a: FFmpeg 路徑 → 存暫存 WAV → FFmpeg 轉換 ──
                        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav")
                        os.close(tmp_fd)
                        try:
                            output_audio.export(tmp_path, format="wav")
                            # Original 格式（為了靜音移除才走 FFmpeg）→ 依原始副檔名決定編碼器/容器
                            fmt_key = original_ext.lstrip(".") if fmt.lower() == "original" else fmt.lower()
                            codec = CODEC_MAP.get(fmt_key, fmt_key)
                            container = CONTAINER_MAP.get(fmt_key, fmt_key)

                            cmd = [FFMPEG_BIN, "-y", "-i", tmp_path]
                            if sr != "Original":
                                cmd += ["-ar", str(sr)]
                            if fmt_key in LOSSY_FORMATS and br != "Original":
                                cmd += ["-b:a", f"{br}k"]
                            if silence_remove:
                                # 修掉頭尾的靜音（dead air），保留中間內容：
                                # 先去開頭靜音 → 反轉 → 再去（原本的）結尾靜音 → 轉回來
                                cmd += ["-af",
                                        "silenceremove=start_periods=1:start_silence=0:start_threshold=-50dB,"
                                        "areverse,"
                                        "silenceremove=start_periods=1:start_silence=0:start_threshold=-50dB,"
                                        "areverse"]
                            cmd += ["-codec:a", codec, "-f", container, save_path]
                            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
                        finally:
                            try:
                                os.remove(tmp_path)
                            except Exception:
                                pass
                    else:
                        # ── Step 3b: Fallback → pydub 直接匯出 ──
                        fmt_tag = save_ext.replace(".", "")
                        if sr != "Original":
                            output_audio = output_audio.set_frame_rate(int(sr))
                        output_audio.export(save_path, format=fmt_tag)

                except Exception as e:
                    print(f"Failed to export {entry['name']}: {e}")
                    traceback.print_exc()

        self.after(0, lambda: self.btn_export.configure(state="normal", text="✅ 匯出完成", text_color="#00E5FF"))
        self.after(3000, lambda: self.btn_export.configure(text="↗ 匯出音檔", text_color="white"))

if __name__ == "__main__":
    app = AudioBalancerApp()
    app.mainloop()
