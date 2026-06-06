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
from tkinter import filedialog, simpledialog
from tkinter import ttk
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
CONTAINER_MAP = {"aif": "aiff", "aiff": "aiff"}

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

        self.import_folder_btn = ctk.CTkButton(self.top_bar, text="Import Folder", fg_color="#3A3A3C", hover_color="#4A4A4C", command=self.import_folder)
        self.import_folder_btn.grid(row=0, column=2, padx=5)

        self.import_single_btn = ctk.CTkButton(self.top_bar, text="Import File", fg_color="#3A3A3C", hover_color="#4A4A4C", command=self.import_file)
        self.import_single_btn.grid(row=0, column=3, padx=5)

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
            self.tab_bar, text="📂", width=32, height=28,
            fg_color="#2C2C2E", hover_color="#3A3A3C",
            font=("Roboto", 14), text_color="#D1D1D6",
            command=self._open_project
        )
        self.btn_open_project.pack(side="left", padx=(0, 4), pady=5)

        self.btn_save_project = ctk.CTkButton(
            self.tab_bar, text="💾", width=32, height=28,
            fg_color="#2C2C2E", hover_color="#3A3A3C",
            font=("Roboto", 14), text_color="#D1D1D6",
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

        self.lbl_active_file = ctk.CTkLabel(self.right_panel, text="No File Selected", font=("Roboto", 14, "bold"), text_color="white")
        self.lbl_active_file.grid(row=0, column=0, padx=15, pady=(15, 5), sticky="w")

        self.waveform_canvas = tk.Canvas(self.right_panel, bg="#111113", highlightthickness=0, height=100, cursor="hand2")
        self.waveform_canvas.grid(row=1, column=0, padx=15, pady=(5, 5), sticky="ew")
        self.waveform_canvas.bind("<ButtonPress-1>", self.on_waveform_click)
        self.waveform_canvas.bind("<B1-Motion>", self.on_waveform_drag)
        self.waveform_canvas.bind("<ButtonRelease-1>", self.on_waveform_release)

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
        self.ab_listen_switch = ctk.CTkSwitch(self.transport_controls, text="原始 ↔ 目標",
                                              variable=self.ab_listen_var, progress_color=COLOR_RED,
                                              command=self.on_ab_toggle)
        self.ab_listen_switch.pack(side="left", padx=15)

        self.lufs_wrapper = ctk.CTkFrame(self.right_panel, fg_color="transparent", border_width=1, border_color="#3A3A3C", corner_radius=8)
        self.lufs_wrapper.grid(row=3, column=0, padx=15, pady=5, sticky="ew")
        self.lufs_wrapper.columnconfigure(0, weight=1)

        self.target_lufs_var = ctk.DoubleVar(value=-16.0)
        self.lufs_slider = ctk.CTkSlider(self.lufs_wrapper, from_=-30.0, to=-6.0, variable=self.target_lufs_var,
                                         button_color=COLOR_CYAN, progress_color=COLOR_CYAN, command=self.update_target_lufs)
        self.lufs_slider.grid(row=0, column=0, padx=20, pady=(15, 0), sticky="ew")

        self.t_lufs_frame = ctk.CTkFrame(self.lufs_wrapper, fg_color="transparent")
        self.t_lufs_frame.grid(row=1, column=0, pady=(2, 4))
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

        # 批次 ±Gain（row=2）
        self.gain_adj_frame = ctk.CTkFrame(self.lufs_wrapper, fg_color="transparent")
        self.gain_adj_frame.grid(row=2, column=0, padx=20, pady=(0, 6), sticky="ew")
        ctk.CTkLabel(self.gain_adj_frame, text="批次 ±Gain:", font=("Arial", 11), text_color=COLOR_TEXT_DIM).pack(side="left")
        self.gain_adj_var = tk.StringVar(value="0.0")
        self.gain_adj_entry = ctk.CTkEntry(
            self.gain_adj_frame, textvariable=self.gain_adj_var,
            width=58, height=26, font=("Arial", 12),
            fg_color="#1A1A1D", border_color="#3A3A3C", justify="center"
        )
        self.gain_adj_entry.pack(side="left", padx=(6, 2))
        self.gain_adj_entry.bind("<Return>", lambda e: self._apply_global_gain())
        ctk.CTkLabel(self.gain_adj_frame, text="dB", font=("Arial", 11), text_color=COLOR_TEXT_DIM).pack(side="left")
        ctk.CTkButton(
            self.gain_adj_frame, text="套用", width=46, height=26,
            font=("Arial", 11), fg_color="#3A3A3C", hover_color="#4A4A4C",
            command=self._apply_global_gain
        ).pack(side="left", padx=(8, 0))

        # 音量 bar 移到最下方（row=5）
        self.meter_frame = ctk.CTkFrame(self.lufs_wrapper, fg_color="transparent")
        self.meter_frame.grid(row=5, column=0, padx=20, pady=(10, 20), sticky="ew")

        self.level_prog_L = tk.Canvas(self.meter_frame, width=28, height=160, bg="#0A0A0A", highlightthickness=0)
        self.level_prog_L.pack(side="left", padx=(0, 5))

        self.level_prog_R = tk.Canvas(self.meter_frame, width=28, height=160, bg="#0A0A0A", highlightthickness=0)
        self.level_prog_R.pack(side="left", padx=5)

        self.scale_canvas = tk.Canvas(self.meter_frame, width=40, height=160, bg="#1C1C1E", highlightthickness=0)
        self.scale_canvas.pack(side="left", padx=(5, 0), fill="y")

        scales = [0, -6, -12, -18, -24, -30]
        canvas_height = 160
        for v in scales:
            y = int((abs(v) / 30.0) * canvas_height)
            if y == 0:
                y = 8
            elif y == canvas_height:
                y = canvas_height - 8
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

        self.device_frame = ctk.CTkFrame(self.meter_frame, fg_color="transparent")
        self.device_frame.pack(side="left", padx=(15, 0), fill="y")

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

        self.device_menu = ctk.CTkOptionMenu(self.device_frame, values=out_devices, fg_color="#3A3A3C", height=24, width=150, font=("Arial", 11), anchor="center")
        self.device_menu.set(default_out)
        self.device_menu.pack(side="top", anchor="nw", pady=(20, 0))

        self.lufs_compare_canvas = tk.Canvas(self.lufs_wrapper, bg="#1C1C1E", height=80, highlightthickness=0)
        self.lufs_compare_canvas.grid(row=3, column=0, padx=20, pady=(5, 5), sticky="ew")

        self.info_frame = ctk.CTkFrame(self.lufs_wrapper, fg_color="transparent")
        self.info_frame.grid(row=4, column=0, padx=20, pady=(5, 10), sticky="ew")
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

        self.bottom_bar.columnconfigure(0, weight=1)
        self.bottom_bar.columnconfigure(1, weight=1)
        self.bottom_bar.columnconfigure(2, weight=1)
        self.bottom_bar.rowconfigure(0, weight=1)

        self.settings_group = ctk.CTkFrame(self.bottom_bar, fg_color="transparent")
        self.settings_group.grid(row=0, column=0, sticky="nsew", padx=20, pady=0)
        self.settings_group.rowconfigure(0, weight=1)

        self.card_fmt = ctk.CTkFrame(self.settings_group, fg_color="#2C2C2E", corner_radius=6)
        self.card_fmt.grid(row=0, column=0, padx=6, pady=12)
        ctk.CTkLabel(self.card_fmt, text="輸出格式:", font=("Arial", 11), text_color="#8E8E93").pack(side="left", padx=(10, 5), pady=8)
        self.format_menu = ctk.CTkOptionMenu(self.card_fmt, values=OUTPUT_FORMATS, fg_color="#3A3A3C", height=24, width=96, font=("Arial", 11), anchor="center", command=self._on_format_changed)
        self.format_menu.pack(side="left", padx=(0, 10), pady=6)

        self.card_sr = ctk.CTkFrame(self.settings_group, fg_color="#2C2C2E", corner_radius=6)
        self.card_sr.grid(row=0, column=1, padx=6, pady=12)
        ctk.CTkLabel(self.card_sr, text="取樣率:", font=("Arial", 11), text_color="#8E8E93").pack(side="left", padx=(10, 5), pady=8)
        self.sr_menu = ctk.CTkOptionMenu(self.card_sr, values=SAMPLE_RATES, fg_color="#3A3A3C", height=24, width=96, font=("Arial", 11), anchor="center")
        self.sr_menu.set("48000")
        self.sr_menu.pack(side="left", padx=(0, 10), pady=6)

        self.card_bit = ctk.CTkFrame(self.settings_group, fg_color="#2C2C2E", corner_radius=6)
        self.card_bit.grid(row=0, column=2, padx=6, pady=12)
        ctk.CTkLabel(self.card_bit, text="位元率:", font=("Arial", 11), text_color="#8E8E93").pack(side="left", padx=(10, 5), pady=8)
        self.bit_menu = ctk.CTkOptionMenu(self.card_bit, values=BITRATES, fg_color="#3A3A3C", height=24, width=86, font=("Arial", 11), anchor="center")
        self.bit_menu.set("Original")
        self.bit_menu.configure(state="disabled")  # 預設 Original 格式，disable
        self.bit_menu.pack(side="left", padx=(0, 10), pady=6)

        # 靜音移除（需要 FFmpeg）
        self.card_silence = ctk.CTkFrame(self.settings_group, fg_color="#2C2C2E", corner_radius=6)
        self.card_silence.grid(row=0, column=3, padx=6, pady=12)
        self.silence_var = tk.BooleanVar(value=False)
        self.chk_silence = ctk.CTkCheckBox(self.card_silence, text="靜音移除", variable=self.silence_var,
                                           font=("Arial", 11), text_color="#8E8E93",
                                           fg_color="#00E5FF", hover_color="#00C8E0", checkmark_color="black")
        self.chk_silence.pack(side="left", padx=10, pady=8)
        if not FFMPEG_BIN:
            self.chk_silence.configure(state="disabled")

        self.export_group = ctk.CTkFrame(self.bottom_bar, fg_color="transparent")
        self.export_group.grid(row=0, column=1, columnspan=2, sticky="e", padx=20, pady=0)

        self.btn_export_path = ctk.CTkButton(self.export_group, text="📁 選擇輸出路徑", width=120, height=32,
                                             fg_color="#3A3A3C", hover_color="#4A4A4C", font=("Arial", 12),
                                             command=self.select_export_folder)
        self.btn_export_path.pack(side="left", padx=(0, 10))

        self.lbl_export_path = ctk.CTkLabel(self.export_group, text="輸出:/尚未設定", text_color="#8E8E93", font=("Roboto Mono", 11))
        self.lbl_export_path.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(self.export_group, text="自訂資料夾名稱:", text_color="#8E8E93", font=("Arial", 11)).pack(side="left", padx=(0, 5))
        default_folder_name = datetime.now().strftime("%Y%m%d_")
        self.folder_name_entry = ctk.CTkEntry(self.export_group, width=120, height=32, font=("Arial", 12), fg_color="#1C1C1E", border_width=1, border_color="#3A3A3C")
        self.folder_name_entry.insert(0, default_folder_name)
        self.folder_name_entry.pack(side="left", padx=(0, 15))

        self.btn_export = ctk.CTkButton(self.export_group, text="↗ 匯出音檔",
                                        font=("Roboto", 13, "bold"), width=120, height=36,
                                        fg_color="#00E5FF", text_color="black", hover_color="#00C8E0",
                                        command=self.start_export_thread)
        self.btn_export.pack(side="left", padx=0)

        # ---------------- 鍵盤快捷鍵 ----------------
        self.bind("<space>", lambda e: self.toggle_play_pause() if not isinstance(self.focus_get(), ctk.CTkEntry) else None)
        self.bind("<Left>", lambda e: self.seek_backward())
        self.bind("<Right>", lambda e: self.seek_forward())
        self.bind("<Up>", lambda e: self.select_prev_file() if self.focus_get() not in (self.file_table, self.dir_tree) else None)
        self.bind("<Down>", lambda e: self.select_next_file() if self.focus_get() not in (self.file_table, self.dir_tree) else None)
        self.bind("<Delete>", lambda e: self.remove_selected_files() if not isinstance(self.focus_get(), ctk.CTkEntry) else None)
        self.bind("<BackSpace>", lambda e: self.remove_selected_files() if not isinstance(self.focus_get(), ctk.CTkEntry) else None)
        # 全選
        self.bind("<Command-a>", lambda e: self._select_all() if not isinstance(self.focus_get(), (ctk.CTkEntry, tk.Entry)) else None)
        self.bind("<Control-a>", lambda e: self._select_all() if not isinstance(self.focus_get(), (ctk.CTkEntry, tk.Entry)) else None)
        # Undo
        self.bind("<Command-z>", lambda e: self._undo() if not isinstance(self.focus_get(), (ctk.CTkEntry, tk.Entry)) else None)
        self.bind("<Control-z>", lambda e: self._undo() if not isinstance(self.focus_get(), (ctk.CTkEntry, tk.Entry)) else None)
        # 儲存
        self.bind("<Command-s>", lambda e: self._save_project())
        self.bind("<Control-s>",  lambda e: self._save_project())

        # ==================== 關閉時自動存檔 ====================
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # ==================== 初始化工作區（從存檔還原或新建） ====================
        self._load_session()

        # ==================== 啟動裝置偵測輪詢 ====================
        self._device_poll_job = None
        self.after(2000, self._poll_audio_devices)

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
        tree.grid(row=0, column=0, padx=10, pady=(0, 2), sticky="nsew")

        dir_scrollbar_x = ttk.Scrollbar(inner_left, orient="horizontal", command=tree.xview)
        dir_scrollbar_x.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        tree.configure(xscrollcommand=dir_scrollbar_x.set)
        tree.column("#0", minwidth=300)

        tree.bind("<ButtonPress-1>", self.on_tree_drag_start)
        tree.bind("<B1-Motion>", self.on_tree_drag_motion)
        tree.bind("<ButtonRelease-1>", self.on_tree_drag_release)
        tree.bind("<Double-Button-1>", self.on_tree_double_click)

        ws.dir_tree = tree
        ws.left_panel_inner = inner_left

        # --- Center inner frame ---
        inner_center = ctk.CTkFrame(self.center_content_container, fg_color="transparent")
        inner_center.grid(row=0, column=0, sticky="nsew")
        inner_center.rowconfigure(0, weight=1)
        inner_center.columnconfigure(0, weight=1)
        inner_center.grid_remove()

        cols = ("匯出", "Name", "Duration", "Status", "原始 LUFS", "目標 LUFS")
        ft = ttk.Treeview(inner_center, columns=cols, show="headings", selectmode="extended")
        ft.heading("匯出", text="☑", command=lambda: self._toggle_all_exports())
        ft.heading("Name", text="檔名 Name")
        ft.heading("Duration", text="時長")
        ft.heading("Status", text="狀態")
        ft.heading("原始 LUFS", text="原始 LUFS")
        ft.heading("目標 LUFS", text="目標 LUFS")
        ft.column("匯出", width=40, anchor="center", stretch=False)
        ft.column("Name", width=200, anchor="w")
        ft.column("Duration", width=60, anchor="center")
        ft.column("Status", width=60, anchor="center")
        ft.column("原始 LUFS", width=90, anchor="center")
        ft.column("目標 LUFS", width=90, anchor="center")
        ft.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        ft.bind("<<TreeviewSelect>>", self.on_table_select)
        ft.bind("<Button-1>", self._on_file_table_click)
        ft.bind("<Button-2>", self.on_table_right_click)
        ft.bind("<Button-3>", self.on_table_right_click)
        ft.bind("<Delete>", lambda e: self.remove_selected_files())
        ft.bind("<BackSpace>", lambda e: self.remove_selected_files())
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
        self.lufs_compare_canvas.delete("all")
        self.check_export_ready()

    def _refresh_tab_buttons(self):
        for w in self.tab_btn_frame.winfo_children():
            w.destroy()
        for i, ws in enumerate(self.workspaces):
            is_active = (i == self.active_ws_idx)
            # 有存檔路徑 → 顯示名稱；未存檔 → 名稱後加 •
            label = ws.name if ws.project_file_path else ws.name + " •"
            btn = ctk.CTkButton(
                self.tab_btn_frame,
                text=label,
                width=120, height=28,
                fg_color=COLOR_CYAN if is_active else "#2C2C2E",
                text_color="black" if is_active else "#8E8E93",
                hover_color="#00C8E0" if is_active else "#3A3A3C",
                font=("Roboto", 12, "bold") if is_active else ("Roboto", 12),
                command=lambda idx=i: self._switch_workspace(idx) or self._refresh_tab_buttons()
            )
            btn.pack(side="left", padx=(0, 4), pady=5)
            btn.bind("<Double-Button-1>", lambda e, idx=i: self._rename_workspace_dialog(idx))
            btn.bind("<Button-2>", lambda e, idx=i: self._show_ws_context_menu(e, idx))
            btn.bind("<Button-3>", lambda e, idx=i: self._show_ws_context_menu(e, idx))

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
        menu.add_command(label="✏️  重命名", command=lambda: self._rename_workspace_dialog(idx))
        menu.add_separator()
        menu.add_command(label="💾  儲存專案", command=lambda: self._save_project(idx))
        menu.add_command(label="📂  另存新檔...", command=lambda: self._save_project_as(idx))
        menu.add_separator()
        menu.add_command(label="✕  關閉此工作區", command=lambda: self._close_workspace(idx))
        menu.post(event.x_root, event.y_root)

    def _save_project(self, ws_idx=None):
        if ws_idx is None:
            ws_idx = self.active_ws_idx
        ws = self.workspaces[ws_idx]
        if not ws.project_file_path:
            self._save_project_as(ws_idx)
            return
        self._write_project_file(ws, ws.project_file_path)
        self._refresh_tab_buttons()

    def _save_project_as(self, ws_idx=None):
        if ws_idx is None:
            ws_idx = self.active_ws_idx
        ws = self.workspaces[ws_idx]
        path = filedialog.asksaveasfilename(
            initialfile=ws.name + ".abproj",
            initialdir=self._projects_folder(),
            defaultextension=".abproj",
            filetypes=[("Audio Balancer Project", "*.abproj"), ("All Files", "*.*")],
        )
        if path:
            ws.project_file_path = path
            self._write_project_file(ws, path)
            self._refresh_tab_buttons()

    def _write_project_file(self, ws, path):
        data = {
            "version": 1,
            "name": ws.name,
            "current_folder": ws.current_folder,
            "audio_files": []
        }
        for e in ws.audio_files:
            lufs_val = e["lufs"] if isinstance(e["lufs"], float) else None
            target_val = e["target_lufs"] if isinstance(e.get("target_lufs"), float) else lufs_val
            data["audio_files"].append({
                "path": e["path"],
                "name": e["name"],
                "duration": e["duration"],
                "lufs": lufs_val,
                "target_lufs": target_val,
                "export": e.get("export", True),
            })
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _open_project(self):
        path = filedialog.askopenfilename(
            initialdir=self._projects_folder(),
            filetypes=[("Audio Balancer Project", "*.abproj"), ("All Files", "*.*")],
        )
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            traceback.print_exc()
            return
        name = data.get("name", os.path.splitext(os.path.basename(path))[0])
        idx = self._add_workspace(name)
        ws = self.workspaces[idx]
        ws.project_file_path = path

        saved_folder = data.get("current_folder", "")
        if saved_folder and os.path.isdir(saved_folder):
            self._populate_dir_tree_for_ws(ws, saved_folder)

        for ef in data.get("audio_files", []):
            fpath = ef["path"]
            if not os.path.isfile(fpath):
                continue
            lufs_saved = ef.get("lufs")
            target_saved = ef.get("target_lufs")
            dur_saved = ef.get("duration", "--:--")
            export_val = ef.get("export", True)
            entry = {
                "name": ef["name"], "path": fpath, "duration": dur_saved,
                "status": "🟡 載入中",
                "lufs": lufs_saved if lufs_saved is not None else "--",
                "target_lufs": target_saved, "audio": None, "export": export_val,
            }
            ws.audio_files.append(entry)
            lufs_display = f"{lufs_saved:.1f} LUFS" if lufs_saved is not None else "--"
            target_display = f"{target_saved:.1f} LUFS" if target_saved is not None else "--"
            ws.file_table.insert("", "end", iid=fpath, values=(
                "☑" if export_val else "☐", ef["name"], dur_saved, entry["status"],
                lufs_display, target_display,
            ))
            threading.Thread(target=self.analyze_single_file, args=(entry,), daemon=True).start()

        self._switch_workspace(idx)
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

    def _schedule_autosave(self):
        """Debounce: cancel pending save and reschedule 800 ms later."""
        if self._autosave_job is not None:
            try:
                self.after_cancel(self._autosave_job)
            except Exception:
                pass
        self._autosave_job = self.after(800, self._autosave_all)

    def _autosave_all(self):
        """Auto-save session AND all workspace project files that have a path."""
        self._save_session()
        for i, ws in enumerate(self.workspaces):
            if ws.project_file_path:
                try:
                    self._write_project_file(ws, ws.project_file_path)
                except Exception:
                    pass

    def _save_session(self):
        self._autosave_job = None
        try:
            data = {
                "version": 1,
                "export_folder": self.export_folder,
                "active_ws_idx": self.active_ws_idx,
                "workspaces": []
            }
            for ws in self.workspaces:
                ws_data = {
                    "name": ws.name,
                    "current_folder": ws.current_folder,
                    "audio_files": []
                }
                for e in ws.audio_files:
                    lufs_val = e["lufs"] if isinstance(e["lufs"], float) else None
                    target_val = e["target_lufs"] if isinstance(e.get("target_lufs"), float) else lufs_val
                    ws_data["audio_files"].append({
                        "path": e["path"],
                        "name": e["name"],
                        "duration": e["duration"],
                        "lufs": lufs_val,
                        "target_lufs": target_val,
                        "export": e.get("export", True),
                    })
                data["workspaces"].append(ws_data)
            with open(self._session_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
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

        root_node = tree.insert("", "end", text=os.path.basename(folder_path), open=True)
        ws.tree_item_paths[root_node] = folder_path

        valid_exts = ('.wav', '.mp3', '.flac', '.aiff')
        node_map = {folder_path: root_node}

        for root, dirs, files in os.walk(folder_path):
            parent_node = node_map.get(root)
            if not parent_node:
                continue
            for d in sorted(dirs):
                dir_path = os.path.join(root, d)
                node = tree.insert(parent_node, "end", text=d)
                node_map[dir_path] = node
                ws.tree_item_paths[node] = dir_path
            for fname in sorted(files):
                if fname.lower().endswith(valid_exts):
                    file_node = tree.insert(parent_node, "end", text=fname)
                    full_path = os.path.join(root, fname)
                    ws.tree_item_paths[file_node] = full_path

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
            idx = self._add_workspace(ws_data.get("name", f"工作區 {len(self.workspaces)}"))
            ws = self.workspaces[idx]

            # Rebuild dir tree from saved folder
            saved_folder = ws_data.get("current_folder", "")
            if saved_folder and os.path.isdir(saved_folder):
                self._populate_dir_tree_for_ws(ws, saved_folder)

            # Restore audio file entries
            for ef in ws_data.get("audio_files", []):
                path = ef["path"]
                if not os.path.isfile(path):
                    continue  # File was moved or deleted — skip

                lufs_saved = ef.get("lufs")
                target_saved = ef.get("target_lufs")
                dur_saved = ef.get("duration", "--:--")
                export_val = ef.get("export", True)

                # Build entry — status starts as "🟡 載入中" until re-analysed
                entry = {
                    "name": ef["name"],
                    "path": path,
                    "duration": dur_saved,
                    "status": "🟡 載入中",
                    "lufs": lufs_saved if lufs_saved is not None else "--",
                    "target_lufs": target_saved,  # keep user's choice
                    "audio": None,
                    "export": export_val,
                }
                ws.audio_files.append(entry)

                lufs_display = f"{lufs_saved:.1f} LUFS" if lufs_saved is not None else "--"
                target_display = f"{target_saved:.1f} LUFS" if target_saved is not None else "--"
                ws.file_table.insert("", "end", iid=path, values=(
                    "☑" if export_val else "☐",
                    ef["name"],
                    dur_saved,
                    entry["status"],
                    lufs_display,
                    target_display,
                ))
                threading.Thread(target=self.analyze_single_file, args=(entry,), daemon=True).start()

        # --- Restore export folder ---
        saved_export = data.get("export_folder", "")
        if saved_export and os.path.isdir(saved_export):
            self.export_folder = saved_export
            parts = os.path.normpath(saved_export).split(os.sep)
            display_path = ".../" + "/".join(parts[-2:]) if len(parts) > 2 else saved_export
            self.lbl_export_path.configure(text=display_path)

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

    def _on_file_table_click(self, event):
        """處理檔案列表的點擊事件 — 若點在「匯出」欄則切換勾選。"""
        tree = event.widget
        region = tree.identify_region(event.x, event.y)
        if region == "cell":
            col = tree.identify_column(event.x)
            if col == "#1":  # 第一欄 = "匯出"
                item = tree.identify_row(event.y)
                if item:
                    current = tree.set(item, "匯出")
                    new_val = "☐" if current == "☑" else "☑"
                    tree.set(item, "匯出", new_val)
                    ws = next((w for w in self.workspaces if w.file_table == tree), None)
                    if ws:
                        entry = next((e for e in ws.audio_files if e["path"] == item), None)
                        if entry:
                            entry["export"] = (new_val == "☑")
                            self._schedule_autosave()

    def _toggle_all_exports(self):
        """切換目前工作區所有檔案的匯出勾選（全選/全不選）。"""
        items = self.file_table.get_children()
        if not items:
            return
        # 若有任何一個是勾選的，就全部取消；否則全部勾選
        any_checked = any(self.file_table.set(item, "匯出") == "☑" for item in items)
        new_val = "☐" if any_checked else "☑"
        for item in items:
            self.file_table.set(item, "匯出", new_val)
            entry = next((e for e in self.audio_files if e["path"] == item), None)
            if entry:
                entry["export"] = (new_val == "☑")
        self._schedule_autosave()

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
            ctk.CTkCheckBox(dialog, text=f"{ws.name}  ({len([f for f in ws.audio_files if f['status'] == '🟢 就緒'])} 個就緒)",
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
        items = self.file_table.get_children()
        if not items: return
        sel = self.file_table.selection()
        if not sel:
            self.file_table.selection_set(items[-1])
        else:
            idx = items.index(sel[0])
            if idx > 0:
                self.file_table.selection_set(items[idx - 1])
                self.file_table.see(items[idx - 1])
                self.on_table_select(None)

    def select_next_file(self, event=None):
        items = self.file_table.get_children()
        if not items: return
        sel = self.file_table.selection()
        if not sel:
            self.file_table.selection_set(items[0])
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

    def import_folder(self):
        folder_path = filedialog.askdirectory()
        if folder_path:
            ws = self.workspaces[self.active_ws_idx]
            self._populate_dir_tree_for_ws(ws, folder_path)
            self._schedule_autosave()

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
                    folder_name = os.path.basename(full_path)
                    for fname in sorted(os.listdir(full_path)):
                        fpath = os.path.join(full_path, fname)
                        if os.path.isfile(fpath) and fname.lower().endswith(AUDIO_EXTS):
                            if fpath not in existing_paths:
                                self.add_file_to_table(fpath, folder_prefix=folder_name)
                                existing_paths.add(fpath)

        self.drag_items = []

    def on_tree_double_click(self, event):
        source_tree = event.widget
        item = source_tree.identify_row(event.y)
        if not item:
            return
        # 找到對應的 workspace
        ws = next((w for w in self.workspaces if w.dir_tree == source_tree), None)
        if ws is None:
            return
        path = ws.tree_item_paths.get(item)
        if path:
            if os.path.isfile(path):
                if path not in [f["path"] for f in self.audio_files]:
                    self.add_file_to_table(path)
            elif os.path.isdir(path):
                AUDIO_EXTS = {'.wav', '.mp3', '.flac', '.aiff', '.aif', '.ogg', '.m4a'}
                folder_name = os.path.basename(path)
                for fname in sorted(os.listdir(path)):
                    fpath = os.path.join(path, fname)
                    if os.path.isfile(fpath) and os.path.splitext(fname)[1].lower() in AUDIO_EXTS:
                        if fpath not in [f["path"] for f in self.audio_files]:
                            self.add_file_to_table(fpath, folder_prefix=folder_name)

    def import_file(self):
        file_path = filedialog.askopenfilename(
            title="選擇音訊檔案",
            filetypes=[
                ("Audio Files", "*.wav"),
                ("Audio Files", "*.mp3"),
                ("Audio Files", "*.aiff"),
                ("Audio Files", "*.flac"),
                ("All Files", "*.*")
            ]
        )
        if file_path:
            self.current_folder = os.path.dirname(file_path)

            self.dir_tree.delete(*self.dir_tree.get_children())
            node = self.dir_tree.insert("", "end", text=os.path.basename(self.current_folder), open=True)
            self.tree_item_paths[node] = self.current_folder

            fname = os.path.basename(file_path)
            file_node = self.dir_tree.insert(node, "end", text=fname)
            self.tree_item_paths[file_node] = file_path

    def add_file_to_table(self, file_path, folder_prefix=None):
        fname = os.path.basename(file_path)
        # 顯示名稱帶資料夾前綴（僅用於 UI，不影響儲存路徑）
        display_name = f"{folder_prefix}/{fname}" if folder_prefix else fname
        entry = {"name": fname, "path": file_path, "duration": "--:--", "status": "🟡 載入中",
                 "lufs": "--", "target_lufs": None, "audio": None, "export": True}
        self.audio_files.append(entry)
        self.file_table.insert("", "end", iid=file_path,
                               values=("☑", display_name, entry["duration"], entry["status"], entry["lufs"], "--"))
        threading.Thread(target=self.analyze_single_file, args=(entry,), daemon=True).start()
        self.check_export_ready()
        self._schedule_autosave()

    def remove_selected_files(self):
        selected = self.file_table.selection()
        for iid in selected:
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
                self.lufs_compare_canvas.delete("all")

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
            self.after(0, self.update_table_row, entry["path"], entry["duration"], entry["status"],
                       f"{lufs:.1f} LUFS", target_display)
            self.after(0, self._schedule_autosave)

        except Exception as e:
            traceback.print_exc()
            entry["status"] = "🔴 失敗"
            self.after(0, self.update_table_row, entry["path"], "--:--", entry["status"], "Error", None)

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
                self.after(0, self.update_table_row, entry["path"], entry["duration"], entry["status"],
                           f"{lufs:.1f} LUFS", f"{lufs:.1f} LUFS")
                self.after(0, self._schedule_autosave)

            except Exception as e:
                traceback.print_exc()
                entry["status"] = "🔴 失敗"
                self.after(0, self.update_table_row, entry["path"], "--:--", entry["status"], "Error")

    def update_table_row(self, iid, dur, status, lufs, target_lufs=None):
        if self.file_table.exists(iid):
            self.file_table.set(iid, "Duration", dur)
            self.file_table.set(iid, "Status", status)
            self.file_table.set(iid, "原始 LUFS", lufs)
            if target_lufs is not None:
                self.file_table.set(iid, "目標 LUFS", target_lufs)

    def on_table_select(self, event):
        if event is not None and hasattr(event, 'widget'):
            event.widget.focus_set()  # 確保鍵盤 focus 在 file_table 上
        selected = self.file_table.selection()
        if selected:
            path = selected[0]
            fname = os.path.basename(path)
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
                self.draw_waveform(entry["audio"])

    def draw_waveform(self, audio):
        self.waveform_canvas.delete("all")
        width = self.waveform_canvas.winfo_width()
        height = self.waveform_canvas.winfo_height()

        if width <= 1 or height <= 1:
            width = 370
            height = 120

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
            self.waveform_canvas.create_line(x, center_y - line_height, x, center_y + line_height, fill="#D1D1D6")

    def draw_waveform_with_playhead(self):
        if hasattr(self, 'current_audio') and self.current_audio:
            self.draw_waveform(self.current_audio)

        if hasattr(self, 'playback_duration') and self.playback_duration > 0:
            progress = self.pause_position / self.playback_duration
            canvas_width = self.waveform_canvas.winfo_width()
            x = int(progress * canvas_width)
            self.waveform_canvas.create_line(
                x, 0, x, self.waveform_canvas.winfo_height(),
                fill="#00E5FF", width=2, tags="playhead"
            )

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

    def update_info_cards(self):
        if hasattr(self, 'original_lufs_val') and self.original_lufs_val is not None:
            self.lbl_info_current.configure(text=f"{self.original_lufs_val:.1f}")
            target = self.target_lufs_var.get()
            self.lbl_info_target.configure(text=f"{target:.1f}")
            gain = target - self.original_lufs_val
            sign = "+" if gain > 0 else ""
            self.lbl_info_gain.configure(text=f"{sign}{gain:.1f}")
            self.draw_lufs_compare()
        else:
            self.lbl_info_current.configure(text="--")
            self.lbl_info_target.configure(text="--")
            self.lbl_info_gain.configure(text="--")
            self.lufs_compare_canvas.delete("all")

    def draw_lufs_compare(self):
        self.lufs_compare_canvas.delete("all")
        if not hasattr(self, 'original_lufs_val') or self.original_lufs_val is None:
            return

        width = self.lufs_compare_canvas.winfo_width()
        height = self.lufs_compare_canvas.winfo_height()
        if width <= 1:
            width = self.lufs_compare_canvas.winfo_reqwidth()

        min_lufs = -35.0
        max_lufs = -5.0
        range_lufs = max_lufs - min_lufs

        curr = max(min(self.original_lufs_val, max_lufs), min_lufs)
        tgt = max(min(self.target_lufs_var.get(), max_lufs), min_lufs)

        pad_x = 20
        track_width = max(10, width - pad_x * 2)

        x_curr = pad_x + ((curr - min_lufs) / range_lufs) * track_width
        x_tgt = pad_x + ((tgt - min_lufs) / range_lufs) * track_width

        y_center = height / 2 - 5
        self.lufs_compare_canvas.create_line(pad_x, y_center, width - pad_x, y_center, fill="#2C2C2E", width=16)

        fill_color = "#104045" if tgt >= curr else "#503215"
        x_start = min(x_curr, x_tgt)
        x_end = max(x_curr, x_tgt)
        self.lufs_compare_canvas.create_line(x_start, y_center, x_end, y_center, fill=fill_color, width=16)

        self.lufs_compare_canvas.create_line(x_curr, y_center - 16, x_curr, y_center + 16, fill="white", width=3)
        self.lufs_compare_canvas.create_text(x_curr, y_center - 26, text=f"C {curr:.1f}", fill="white", font=("Arial", 10))

        self.lufs_compare_canvas.create_line(x_tgt, y_center - 16, x_tgt, y_center + 16, fill="#00E5FF", width=3)
        self.lufs_compare_canvas.create_text(x_tgt, y_center - 26, text=f"T {tgt:.1f}", fill="#00E5FF", font=("Arial", 10))

        for v in [-35, -25, -15, -5]:
            xv = pad_x + ((v - min_lufs) / range_lufs) * track_width
            self.lufs_compare_canvas.create_text(xv, y_center + 20, text=str(v), fill="#555555", font=("Arial", 9))

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
            canvas_width = self.waveform_canvas.winfo_width()
            x = int((self.pause_position / dur) * canvas_width)
            self.waveform_canvas.create_line(x, 0, x, self.waveform_canvas.winfo_height(), fill="#00E5FF", width=2, tags="playhead")

    def on_waveform_click(self, event):
        if not self.current_audio: return
        canvas_width = self.waveform_canvas.winfo_width()
        if canvas_width <= 1: return
        ratio = max(0.0, min(1.0, event.x / canvas_width))
        new_time = ratio * self.current_audio.duration_seconds
        self.pause_position = new_time
        self.scrub_var.set(new_time)
        if self.is_playing:
            self.jump_to(new_time)
        else:
            self.update_playhead_idle()

    def on_waveform_drag(self, event):
        self.on_waveform_click(event)

    def on_waveform_release(self, event):
        pass

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
        height = 160
        width = 28

        scales = [0, -6, -12, -18, -24, -30]
        for v in scales:
            y = int((abs(v) / 30.0) * height)
            if y == 0: y = 1
            elif y == height: y = height - 1
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
            canvas_width = self.waveform_canvas.winfo_width()
            playhead_x = int((current_time / self.playback_duration) * canvas_width)
            self.waveform_canvas.create_line(playhead_x, 0, playhead_x, self.waveform_canvas.winfo_height(), fill="#00E5FF", width=2, tags="playhead")

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
        self.target_lufs_var.set(val)
        self.update_target_lufs(val)

    def _reset_lufs_to_default(self):
        """↺ 一鍵恢復：依檔名語意判斷預設 LUFS，未命中則 -16.0。"""
        if self.current_file_path:
            val = self.suggest_target_lufs(os.path.basename(self.current_file_path))
        else:
            val = -16.0
        self._push_lufs_undo()
        self.target_lufs_var.set(val)
        self.update_target_lufs(val)

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

    def _apply_global_gain(self):
        """將所有選取檔案（或全部檔案）的目標 LUFS 整體平移 N dB。"""
        try:
            delta = float(self.gain_adj_var.get())
        except ValueError:
            return
        if delta == 0:
            return

        selected = self.file_table.selection()
        targets = list(selected) if selected else [e["path"] for e in self.audio_files]
        if not targets:
            return

        snapshot = [(p, next((e["target_lufs"] for e in self.audio_files if e["path"] == p), None))
                    for p in targets]
        self._undo_stack.append(("gain_adj", snapshot))
        if len(self._undo_stack) > 50:
            self._undo_stack = self._undo_stack[-50:]

        for path in targets:
            entry = next((e for e in self.audio_files if e["path"] == path), None)
            if entry and isinstance(entry.get("target_lufs"), float):
                new_val = max(-40.0, min(-1.0, entry["target_lufs"] + delta))
                entry["target_lufs"] = new_val
                if self.file_table.exists(path):
                    self.file_table.set(path, "目標 LUFS", f"{new_val:.1f} LUFS")

        if self.current_file_path and self.current_file_path in targets:
            cur = next((e for e in self.audio_files if e["path"] == self.current_file_path), None)
            if cur and isinstance(cur.get("target_lufs"), float):
                self.target_lufs_var.set(cur["target_lufs"])
                self.update_target_lufs(cur["target_lufs"], from_selection=True)

        self._schedule_autosave()

    # ─────────────────────────────────────────────────────────
    # 全選（Cmd+A）
    # ─────────────────────────────────────────────────────────

    def _select_all(self):
        focused = self.focus_get()
        for ws in self.workspaces:
            if focused == ws.dir_tree:
                all_items = self._get_all_tree_items(ws.dir_tree)
                if all_items:
                    ws.dir_tree.selection_set(all_items)
                return
        items = self.file_table.get_children()
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

    def select_export_folder(self):
        folder_path = filedialog.askdirectory(title="選擇輸出資料夾")
        if folder_path:
            self.export_folder = folder_path
            parts = os.path.normpath(folder_path).split(os.sep)
            if len(parts) > 2:
                display_path = ".../" + "/".join(parts[-2:])
            else:
                display_path = folder_path
            self.lbl_export_path.configure(text=display_path)
            self.check_export_ready()
            self._schedule_autosave()

    def start_export_thread(self):
        if not self.export_folder: return

        # 找出所有有可匯出檔案的工作區
        exportable = [ws for ws in self.workspaces if any(e["status"] == "🟢 就緒" for e in ws.audio_files)]
        if not exportable:
            return

        fmt = self.format_menu.get()
        sr  = self.sr_menu.get()
        br  = self.bit_menu.get()
        silence_remove = self.silence_var.get()

        if len(self.workspaces) == 1:
            selected_workspaces = exportable
        else:
            selected_workspaces = self._show_workspace_export_dialog(exportable)
            if not selected_workspaces:
                return

        self.btn_export.configure(state="disabled", text="⏳ 匯出中...")
        threading.Thread(target=self.export_process, args=(fmt, selected_workspaces, sr, br, silence_remove), daemon=True).start()

    def export_process(self, fmt, workspaces, sr="Original", br="Original", silence_remove=False):
        custom_name = self.folder_name_entry.get().strip()
        multi = len(workspaces) > 1
        use_ffmpeg = bool(FFMPEG_BIN) and fmt.lower() != "original"

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
                    save_path = os.path.join(target_dir, save_name)

                    if use_ffmpeg:
                        # ── Step 3a: FFmpeg 路徑 → 存暫存 WAV → FFmpeg 轉換 ──
                        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".wav")
                        os.close(tmp_fd)
                        try:
                            output_audio.export(tmp_path, format="wav")
                            fmt_key = fmt.lower()
                            codec = CODEC_MAP.get(fmt_key, fmt_key)
                            container = CONTAINER_MAP.get(fmt_key, fmt_key)

                            cmd = [FFMPEG_BIN, "-y", "-i", tmp_path]
                            if sr != "Original":
                                cmd += ["-ar", str(sr)]
                            if fmt_key in LOSSY_FORMATS and br != "Original":
                                cmd += ["-b:a", f"{br}k"]
                            if silence_remove:
                                cmd += ["-af", "silenceremove=stop_periods=-1:stop_duration=0.3:stop_threshold=-50dB"]
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
