"""
Audio Master — UI 原型（Flet）
只是『右側播放/參數面板』的外觀＋手感原型，用來比較換框架後的質感。
跑法：/Users/patrickchiu/Python_Audio_Balancer/venv/bin/python ui_prototype_flet.py
"""
import flet as ft

BG    = "#14161B"
PANEL = "#1A1D24"
CARD  = "#22262E"
INSET = "#0E1014"
TX    = "#E6E8EC"
DIM   = "#868D98"
ACC   = "#4DA6FF"
GRN   = "#46D17F"
LINE  = "#2A2F38"
MONO  = "Roboto"

WAVE = [20,34,28,46,38,52,60,44,70,55,48,62,40,30,52,68,58,42,36,50,
        64,46,38,56,72,50,40,34,48,60,52,38,28,44,58,46,36,30,50,40]


def main(page: ft.Page):
    page.title = "Audio Master — UI 原型 (Flet)"
    page.bgcolor = BG
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 20
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    page.window.width = 470
    page.window.height = 860
    page.window.resizable = True

    def mono(v, size=15, color=TX, weight=ft.FontWeight.W_500):
        return ft.Text(v, size=size, color=color, weight=weight, font_family=MONO)

    # ── 波形 + 播放桿 ──
    bars = ft.Row(
        [ft.Container(width=4, height=h * 0.5, bgcolor=ACC, border_radius=1, opacity=0.92) for h in WAVE],
        spacing=2, alignment=ft.MainAxisAlignment.CENTER,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    waveform = ft.Container(
        content=ft.Stack([
            ft.Container(content=bars, padding=ft.padding.symmetric(horizontal=8)),
            ft.Container(left=196, top=8, width=2, height=48, bgcolor=ACC, border_radius=1),
        ]),
        bgcolor=INSET, border_radius=10, height=64,
        alignment=ft.alignment.center,
    )

    # ── 數值即時更新 ──
    gain_val = mono("0.0", size=15, color=ACC)
    lufs_val = mono("-12.0", size=15, color=ACC)
    target_card_val = mono("-12.0", size=16)
    gain_card_val = mono("+9.3", size=16, color=GRN)
    CURRENT = -21.3

    def on_gain(e):
        gain_val.value = f"{e.control.value:.1f}"
        page.update()

    def on_lufs(e):
        v = e.control.value
        lufs_val.value = f"{v:.1f}"
        target_card_val.value = f"{v:.1f}"
        g = v - CURRENT
        gain_card_val.value = f"{g:+.1f}"
        page.update()

    def slider(minv, maxv, val, fn, divisions=None):
        return ft.Slider(min=minv, max=maxv, value=val, on_change=fn,
                         active_color=ACC, inactive_color=LINE, thumb_color=ACC,
                         divisions=divisions, height=20)

    def transport_icon(name):
        return ft.Icon(name, color=TX, size=20)

    play_btn = ft.Container(
        content=ft.Icon(ft.Icons.PLAY_ARROW_ROUNDED, color="#0C2236", size=22),
        width=38, height=38, border_radius=19, bgcolor=ACC,
        alignment=ft.alignment.center,
    )

    def metric(label, value_ctrl):
        return ft.Container(
            content=ft.Column([ft.Text(label, size=10, color=DIM), value_ctrl], spacing=2),
            bgcolor=CARD, border_radius=8, padding=ft.padding.symmetric(horizontal=10, vertical=8),
            expand=True,
        )

    def meter(level):
        return ft.Container(
            width=16, height=120, bgcolor=INSET, border_radius=3,
            alignment=ft.alignment.bottom_center,
            content=ft.Container(width=16, height=120 * level, bgcolor=ACC, border_radius=3),
        )

    panel = ft.Container(
        bgcolor=PANEL, border_radius=14, padding=16, width=430,
        border=ft.border.all(1, "#23272F"),
        content=ft.Column(spacing=12, controls=[
            ft.Text("Sound_Base_Scoring.wav", size=14, weight=ft.FontWeight.W_500, color=TX),
            waveform,
            ft.Row([
                mono("00:13", size=11, color=DIM, weight=ft.FontWeight.W_400),
                ft.Container(slider(0, 28, 13, lambda e: None), expand=True),
                mono("00:28", size=11, color=DIM, weight=ft.FontWeight.W_400),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),

            ft.Row([
                transport_icon(ft.Icons.SKIP_PREVIOUS_ROUNDED),
                play_btn,
                transport_icon(ft.Icons.STOP_ROUNDED),
                transport_icon(ft.Icons.SKIP_NEXT_ROUNDED),
                ft.Icon(ft.Icons.REPEAT_ROUNDED, color=ACC, size=18),
            ], alignment=ft.MainAxisAlignment.CENTER, spacing=10),

            ft.Row([
                ft.Text("原始", size=12, color=TX),
                ft.Switch(value=False, active_color="#FF4D4D", inactive_track_color="#3A3F49", scale=0.85),
                ft.Text("目標", size=12, color=DIM),
            ], alignment=ft.MainAxisAlignment.CENTER, spacing=4),

            # 參數卡
            ft.Container(
                bgcolor=CARD, border_radius=10, padding=ft.padding.symmetric(horizontal=14, vertical=12),
                content=ft.Column(spacing=4, controls=[
                    ft.Text("批次 ±Gain", size=11, color=DIM),
                    slider(-20, 20, 0, on_gain),
                    ft.Row([
                        gain_val, ft.Text("dB", size=11, color=DIM),
                        ft.Container(expand=True),
                        ft.Container(content=ft.Text("套用", size=11, color=TX),
                                     bgcolor="#2E333D", border_radius=6,
                                     padding=ft.padding.symmetric(horizontal=10, vertical=4)),
                    ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Container(height=6),
                    slider(-30, -6, -12, on_lufs),
                    ft.Row([lufs_val, ft.Text("LUFS", size=11, color=DIM)]),
                ]),
            ),

            ft.Row([
                metric("Current", mono(f"{CURRENT:.1f}", size=16)),
                metric("Target", target_card_val),
                metric("Gain", gain_card_val),
            ], spacing=8),

            ft.Row([
                meter(0.58), meter(0.52),
                ft.Column([mono("0", 9, DIM, ft.FontWeight.W_400),
                           mono("-12", 9, DIM, ft.FontWeight.W_400),
                           mono("-24", 9, DIM, ft.FontWeight.W_400)],
                          spacing=44, alignment=ft.MainAxisAlignment.START),
                ft.Container(width=8),
                ft.Column(spacing=4, controls=[
                    ft.Text("PEAK", size=10, color=DIM),
                    mono("-8.5", 12, GRN, ft.FontWeight.W_400),
                    mono("-7.4", 12, GRN, ft.FontWeight.W_400),
                    ft.Container(height=4),
                    ft.Dropdown(value="SSL 2+ Mk II", text_size=11, dense=True,
                                width=120, border_color="#3A3F49",
                                options=[ft.dropdown.Option("SSL 2+ Mk II"),
                                         ft.dropdown.Option("MacBook Pro 喇叭")]),
                ], expand=True),
            ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.END),
        ]),
    )

    page.add(panel)


if __name__ == "__main__":
    ft.app(target=main, view=ft.AppView.WEB_BROWSER, port=8550)
