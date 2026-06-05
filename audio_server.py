#!/usr/bin/env python3
"""
音量平衡輔助化工具 - Python 後端伺服器
供 Electron 前端呼叫
"""

import os
import sys
import json
import threading
import time
import numpy as np
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# Audio libraries
try:
    from pydub import AudioSegment
    import pyloudnorm as pyln
    import sounddevice as sd
except ImportError as e:
    print(f"Missing library: {e}", flush=True)
    sys.exit(1)

# ── 全域播放狀態 ──────────────────────────────────────────
playback_state = {
    "is_playing": False,
    "is_paused": False,
    "current_file": None,
    "current_pos": 0.0,
    "duration": 0.0,
    "start_sys_time": 0.0,
    "pause_pos": 0.0,
    "loop": False,
    "ab_mode": False,
    "target_lufs": -16.0,
    "original_lufs": None,
    "playback_data": None,
    "sample_rate": 44100,
    "meter_l": 0.0,
    "meter_r": 0.0,
}
state_lock = threading.Lock()

def get_audio_files(folder_path):
    """遞迴掃描資料夾，建立樹狀結構"""
    AUDIO_EXTS = {'.wav', '.mp3', '.flac', '.aiff', '.ogg', '.m4a'}
    
    def scan(path):
        node = {
            "name": os.path.basename(path),
            "path": path,
            "type": "folder",
            "children": [],
            "files": []
        }
        try:
            entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return node
        for entry in entries:
            if entry.is_dir():
                node["children"].append(scan(entry.path))
            elif os.path.splitext(entry.name)[1].lower() in AUDIO_EXTS:
                node["files"].append({
                    "name": entry.name,
                    "path": entry.path,
                    "type": "file"
                })
        return node
    
    return scan(folder_path)

def analyze_file(file_path):
    """分析單一音檔的 LUFS 和時長"""
    try:
        audio = AudioSegment.from_file(file_path)
        samples = np.array(audio.get_array_of_samples())
        if audio.channels > 1:
            samples = samples.reshape((-1, audio.channels))
        max_val = float(2 ** (8 * audio.sample_width - 1))
        samples = samples.astype(np.float64) / max_val
        
        duration = audio.duration_seconds
        
        # 太短的檔案用 peak
        min_samples = int(audio.frame_rate * 0.4)
        n = samples.shape[0] if samples.ndim > 1 else len(samples)
        
        if n < min_samples:
            peak = float(np.max(np.abs(samples)))
            lufs = float(20 * np.log10(peak)) if peak > 0 else -99.0
            method = "peak"
        else:
            meter = pyln.Meter(audio.frame_rate)
            lufs = meter.integrated_loudness(samples)
            if lufs == float('-inf'):
                lufs = -99.0
            method = "lufs"
        
        # 波形資料（降採樣到 800 點）
        if audio.channels > 1:
            mono = samples.mean(axis=1)
        else:
            mono = samples.flatten()
        
        chunk = max(1, len(mono) // 800)
        peaks = []
        for i in range(0, len(mono), chunk):
            c = mono[i:i+chunk]
            if len(c) > 0:
                peaks.append(float(np.max(np.abs(c))))
        
        max_p = max(peaks) if peaks else 1
        waveform = [p / max_p for p in peaks]
        
        return {
            "ok": True,
            "lufs": round(float(lufs), 1),
            "duration": round(duration, 2),
            "method": method,
            "waveform": waveform,
            "channels": audio.channels,
            "sample_rate": audio.frame_rate
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

def do_play(file_path, target_lufs, ab_mode, original_lufs, start_pos=0.0):
    """在新執行緒播放音檔"""
    global playback_state
    
    try:
        audio = AudioSegment.from_file(file_path)
        
        if ab_mode and original_lufs is not None:
            gain = target_lufs - original_lufs
            audio = audio + gain
        
        samples = np.array(audio.get_array_of_samples())
        if audio.channels > 1:
            samples = samples.reshape((-1, audio.channels))
        max_val = float(2 ** (8 * audio.sample_width - 1))
        data = samples.astype(np.float32) / max_val
        
        sr = audio.frame_rate
        duration = len(data) / sr
        start_idx = int(start_pos * sr)
        if start_idx >= len(data):
            start_idx = 0
        
        with state_lock:
            playback_state["playback_data"] = data
            playback_state["sample_rate"] = sr
            playback_state["duration"] = duration
            playback_state["start_sys_time"] = time.time() - start_pos
            playback_state["is_playing"] = True
            playback_state["is_paused"] = False
        
        sd.play(data[start_idx:], samplerate=sr)
        
        # 電平表更新迴圈
        while True:
            with state_lock:
                if not playback_state["is_playing"]:
                    break
                cur_time = time.time() - playback_state["start_sys_time"]
                playback_state["current_pos"] = cur_time
                idx = int(cur_time * sr)
                
                if idx >= len(data):
                    if playback_state["loop"]:
                        playback_state["start_sys_time"] = time.time()
                        sd.play(data, samplerate=sr)
                        idx = 0
                    else:
                        playback_state["is_playing"] = False
                        playback_state["current_pos"] = 0.0
                        sd.stop()
                        break
                
                # 電平計算
                chunk_size = int(sr * 0.05)
                chunk = data[idx:idx+chunk_size]
                if len(chunk) > 0:
                    if chunk.ndim == 1:
                        rms = float(np.sqrt(np.mean(chunk**2)))
                        playback_state["meter_l"] = min(1.0, rms * 5)
                        playback_state["meter_r"] = min(1.0, rms * 5)
                    else:
                        rms_l = float(np.sqrt(np.mean(chunk[:,0]**2)))
                        rms_r = float(np.sqrt(np.mean(chunk[:,1]**2)))
                        playback_state["meter_l"] = min(1.0, rms_l * 5)
                        playback_state["meter_r"] = min(1.0, rms_r * 5)
            
            time.sleep(0.05)
        
        # 播放結束，電平歸零
        with state_lock:
            playback_state["meter_l"] = 0.0
            playback_state["meter_r"] = 0.0
            
    except Exception as e:
        print(f"Playback error: {e}", flush=True)
        with state_lock:
            playback_state["is_playing"] = False

def export_files(items, output_dir, fmt):
    """批次輸出音檔"""
    results = {}
    for item in items:
        try:
            audio = AudioSegment.from_file(item["path"])
            samples = np.array(audio.get_array_of_samples())
            if audio.channels > 1:
                samples = samples.reshape((-1, audio.channels))
            max_val = float(2 ** (8 * audio.sample_width - 1))
            s = samples.astype(np.float64) / max_val
            
            min_s = int(audio.frame_rate * 0.4)
            n = s.shape[0] if s.ndim > 1 else len(s)
            target = item["targetLufs"]
            
            if n < min_s:
                peak = np.max(np.abs(s))
                if peak > 0:
                    tp = 10 ** (target / 20)
                    s = s * (tp / peak)
            else:
                meter = pyln.Meter(audio.frame_rate)
                cur = meter.integrated_loudness(s)
                if cur != float('-inf'):
                    s = pyln.normalize.loudness(s, cur, target)
            
            s = np.clip(s, -1.0, 1.0)
            sw = audio.sample_width
            int_s = (s * (2**(sw*8-1))).astype(np.int16 if sw==2 else np.int32)
            if int_s.ndim > 1:
                int_s = int_s.flatten()
            
            rel = item.get("relativePath", os.path.basename(item["path"]))
            out_path = os.path.join(output_dir, rel)
            base = os.path.splitext(out_path)[0]
            out_path = base + "." + fmt
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            
            out_audio = AudioSegment(
                data=int_s.tobytes(),
                sample_width=sw,
                frame_rate=audio.frame_rate,
                channels=audio.channels
            )
            out_audio.export(out_path, format=fmt)
            results[item["path"]] = {"ok": True, "output": out_path}
        except Exception as e:
            results[item["path"]] = {"ok": False, "error": str(e)}
    return results

# ── HTTP Handler ──────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 靜音 log
    
    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
    
    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length))
        return {}
    
    def do_GET(self):
        path = urlparse(self.path).path
        
        if path == "/status":
            with state_lock:
                self.send_json({
                    "is_playing": playback_state["is_playing"],
                    "is_paused": playback_state["is_paused"],
                    "current_pos": playback_state["current_pos"],
                    "duration": playback_state["duration"],
                    "meter_l": playback_state["meter_l"],
                    "meter_r": playback_state["meter_r"],
                    "loop": playback_state["loop"],
                    "ab_mode": playback_state["ab_mode"],
                })
        elif path == "/ping":
            self.send_json({"ok": True})
        else:
            self.send_json({"error": "not found"}, 404)
    
    def do_POST(self):
        path = urlparse(self.path).path
        body = self.read_body()
        
        if path == "/scan":
            folder = body.get("folder")
            if not folder or not os.path.isdir(folder):
                self.send_json({"error": "invalid folder"}, 400)
                return
            tree = get_audio_files(folder)
            self.send_json({"ok": True, "tree": tree})
        
        elif path == "/analyze":
            file_path = body.get("path")
            if not file_path:
                self.send_json({"error": "no path"}, 400)
                return
            result = analyze_file(file_path)
            self.send_json(result)
        
        elif path == "/play":
            file_path = body.get("path")
            start_pos = body.get("start_pos", 0.0)
            target_lufs = body.get("target_lufs", -16.0)
            ab_mode = body.get("ab_mode", False)
            original_lufs = body.get("original_lufs")
            
            if not file_path:
                self.send_json({"error": "no path"}, 400)
                return
            
            # 停止當前播放
            sd.stop()
            with state_lock:
                playback_state["is_playing"] = False
                playback_state["current_file"] = file_path
                playback_state["target_lufs"] = target_lufs
                playback_state["ab_mode"] = ab_mode
                playback_state["original_lufs"] = original_lufs
            
            t = threading.Thread(
                target=do_play,
                args=(file_path, target_lufs, ab_mode, original_lufs, start_pos),
                daemon=True
            )
            t.start()
            self.send_json({"ok": True})
        
        elif path == "/pause":
            with state_lock:
                if playback_state["is_playing"]:
                    playback_state["pause_pos"] = time.time() - playback_state["start_sys_time"]
                    playback_state["is_playing"] = False
                    playback_state["is_paused"] = True
                    sd.stop()
            self.send_json({"ok": True, "pos": playback_state["pause_pos"]})
        
        elif path == "/resume":
            with state_lock:
                pos = playback_state["pause_pos"]
                file_path = playback_state["current_file"]
                target = playback_state["target_lufs"]
                ab = playback_state["ab_mode"]
                orig = playback_state["original_lufs"]
                playback_state["is_paused"] = False
            
            if file_path:
                t = threading.Thread(
                    target=do_play,
                    args=(file_path, target, ab, orig, pos),
                    daemon=True
                )
                t.start()
            self.send_json({"ok": True})
        
        elif path == "/stop":
            sd.stop()
            with state_lock:
                playback_state["is_playing"] = False
                playback_state["is_paused"] = False
                playback_state["current_pos"] = 0.0
                playback_state["pause_pos"] = 0.0
                playback_state["meter_l"] = 0.0
                playback_state["meter_r"] = 0.0
            self.send_json({"ok": True})
        
        elif path == "/seek":
            pos = body.get("pos", 0.0)
            with state_lock:
                was_playing = playback_state["is_playing"]
                file_path = playback_state["current_file"]
                target = playback_state["target_lufs"]
                ab = playback_state["ab_mode"]
                orig = playback_state["original_lufs"]
                playback_state["pause_pos"] = pos
            
            sd.stop()
            with state_lock:
                playback_state["is_playing"] = False
            
            if was_playing and file_path:
                t = threading.Thread(
                    target=do_play,
                    args=(file_path, target, ab, orig, pos),
                    daemon=True
                )
                t.start()
            self.send_json({"ok": True})
        
        elif path == "/loop":
            with state_lock:
                playback_state["loop"] = body.get("loop", False)
            self.send_json({"ok": True})
        
        elif path == "/ab":
            # 切換 A/B，如果正在播放就重新從當前位置播
            with state_lock:
                playback_state["ab_mode"] = body.get("ab_mode", False)
                was_playing = playback_state["is_playing"]
                pos = time.time() - playback_state["start_sys_time"] if was_playing else playback_state["pause_pos"]
                file_path = playback_state["current_file"]
                target = playback_state["target_lufs"]
                ab = playback_state["ab_mode"]
                orig = playback_state["original_lufs"]
            
            if was_playing and file_path:
                sd.stop()
                with state_lock:
                    playback_state["is_playing"] = False
                t = threading.Thread(
                    target=do_play,
                    args=(file_path, target, ab, orig, pos),
                    daemon=True
                )
                t.start()
            self.send_json({"ok": True})
        
        elif path == "/export":
            items = body.get("items", [])
            output_dir = body.get("output_dir", "")
            fmt = body.get("format", "wav")
            if not output_dir:
                self.send_json({"error": "no output dir"}, 400)
                return
            results = export_files(items, output_dir, fmt)
            ok_count = sum(1 for r in results.values() if r.get("ok"))
            self.send_json({"ok": True, "results": results, "ok_count": ok_count})
        
        else:
            self.send_json({"error": "not found"}, 404)

# ── 啟動伺服器 ─────────────────────────────────────────────
if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7788
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"READY:{port}", flush=True)
    server.serve_forever()
