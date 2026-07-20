# 一次性圖示產生腳本（build-time 工具，非 App 執行期依賴，不需納入 spec/hiddenimports）。
# 產出兩組：App 圖示（AudioMaster.iconset）與 .abproj 存檔文件圖示（AudioProject.iconset）。
import math
import os
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))

CYAN = (0, 229, 255)
BLUE = (77, 166, 255)
BG_TOP = (28, 28, 30)
BG_BOTTOM = (10, 10, 12)


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def vertical_gradient(size, top, bottom):
    img = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / max(1, size - 1)
        r = round(top[0] + (bottom[0] - top[0]) * t)
        g = round(top[1] + (bottom[1] - top[1]) * t)
        b = round(top[2] + (bottom[2] - top[2]) * t)
        img.putpixel((0, y), (r, g, b))
    return img.resize((size, size))


def bar_color(t):
    # t: 0(邊)→1(中央)，由藍到青的漸層，中間的柱子最亮
    r = round(BLUE[0] + (CYAN[0] - BLUE[0]) * t)
    g = round(BLUE[1] + (CYAN[1] - BLUE[1]) * t)
    b = round(BLUE[2] + (CYAN[2] - BLUE[2]) * t)
    return (r, g, b)


def draw_eq_glyph(canvas, cx, cy, total_w, max_h, bar_w, gap, heights_ratio, glow=True):
    """五柱等化器造型（矮-中-高-中-矮，對稱＝音量平衡），圓端條，帶青色輝光。"""
    n = len(heights_ratio)
    start_x = cx - total_w / 2
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for i, ratio in enumerate(heights_ratio):
        bx = start_x + i * (bar_w + gap)
        h = max_h * ratio
        color = bar_color(i / (n - 1) if n > 1 else 1.0)
        top = cy - h / 2
        bottom = cy + h / 2
        r = bar_w / 2
        draw.rounded_rectangle([bx, top, bx + bar_w, bottom], radius=r, fill=color + (255,))
    if glow:
        glow_layer = layer.filter(ImageFilter.GaussianBlur(total_w * 0.03))
        canvas.alpha_composite(glow_layer)
    canvas.alpha_composite(layer)


def make_app_icon(size=1024):
    radius = int(size * 0.225)  # macOS Big Sur+ squircle 比例
    bg = vertical_gradient(size, BG_TOP, BG_BOTTOM).convert("RGBA")
    mask = rounded_mask(size, radius)
    base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    base.paste(bg, (0, 0), mask)

    # 淡淡的內緣高光，增加立體感
    hi = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hi)
    hd.rounded_rectangle([size * 0.03, size * 0.03, size * 0.97, size * 0.97],
                          radius=int(radius * 0.92), outline=(255, 255, 255, 22), width=int(size * 0.006))
    base.alpha_composite(hi)

    draw_eq_glyph(base, cx=size / 2, cy=size / 2 + size * 0.02,
                  total_w=size * 0.62, max_h=size * 0.5,
                  bar_w=size * 0.085, gap=size * 0.045,
                  heights_ratio=[0.42, 0.72, 1.0, 0.72, 0.42])

    # 平衡橫槓：貫穿五柱中線，呼應「音量平衡」主題
    beam = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bd = ImageDraw.Draw(beam)
    beam_y = size / 2 + size * 0.02
    beam_half_w = size * 0.62 / 2 + size * 0.03
    bd.rounded_rectangle([size / 2 - beam_half_w, beam_y - size * 0.012,
                           size / 2 + beam_half_w, beam_y + size * 0.012],
                          radius=size * 0.012, fill=(255, 255, 255, 235))
    # 中央支點小三角
    pv = size * 0.028
    bd.polygon([(size / 2, beam_y + size * 0.012),
                (size / 2 - pv, beam_y + size * 0.012 + pv * 1.6),
                (size / 2 + pv, beam_y + size * 0.012 + pv * 1.6)], fill=(255, 255, 255, 235))
    base.alpha_composite(beam)

    return base.convert("RGBA")


def make_doc_icon(size=1024):
    # 文件造型：白紙 + 右上折角，中央疊上縮小版 EQ glyph，右下角小圓徽章標示副檔名意象
    base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    margin_x = size * 0.20
    top = size * 0.06
    bottom = size * 0.94
    left = margin_x
    right = size - margin_x
    fold = size * 0.16

    page = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pd = ImageDraw.Draw(page)
    poly = [(left, top), (right - fold, top), (right, top + fold), (right, bottom), (left, bottom)]
    # 陰影
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    off = size * 0.012
    sd.polygon([(x + off, y + off * 1.4) for x, y in poly], fill=(0, 0, 0, 90))
    shadow = shadow.filter(ImageFilter.GaussianBlur(size * 0.02))
    base.alpha_composite(shadow)

    pd.polygon(poly, fill=(244, 246, 248, 255), outline=(210, 214, 220, 255), width=int(size * 0.004))
    # 折角三角形（深一階的灰，呈現摺紙感）
    pd.polygon([(right - fold, top), (right, top + fold), (right - fold, top + fold)],
               fill=(214, 219, 226, 255))
    base.alpha_composite(page)

    cx = (left + right) / 2
    cy = (top + bottom) / 2 + size * 0.02
    draw_eq_glyph(base, cx=cx, cy=cy,
                  total_w=(right - left) * 0.56, max_h=(bottom - top) * 0.34,
                  bar_w=size * 0.05, gap=size * 0.026,
                  heights_ratio=[0.42, 0.72, 1.0, 0.72, 0.42], glow=False)

    return base


ICONSET_SIZES = [16, 32, 64, 128, 256, 512, 1024]


def export_iconset(img1024, out_dir, icns_name):
    for sz in ICONSET_SIZES:
        for scale, suffix in ((1, ""), (2, "@2x")):
            px = sz * scale
            if px > 1024:
                continue
            fname = f"icon_{sz}x{sz}{suffix}.png"
            img1024.resize((px, px), Image.LANCZOS).save(os.path.join(out_dir, fname))
    icns_path = os.path.join(HERE, icns_name)
    os.system(f'iconutil -c icns "{out_dir}" -o "{icns_path}"')
    return icns_path


if __name__ == "__main__":
    app_icon = make_app_icon()
    app_icon.save(os.path.join(HERE, "AudioMaster_preview.png"))
    export_iconset(app_icon, os.path.join(HERE, "AudioMaster.iconset"), "AudioMaster.icns")

    doc_icon = make_doc_icon()
    doc_icon.save(os.path.join(HERE, "AudioProject_preview.png"))
    export_iconset(doc_icon, os.path.join(HERE, "AudioProject.iconset"), "AudioProject.icns")

    print("done")
