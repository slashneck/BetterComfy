"""Draws the Better Comfy logo: a black squircle holding an open white ring (the "C" of Comfy) with a four-point
spark breaking out of its opening - generation. Writes assets/icon.ico, icon.png, logo.png (no tile) and arrow.png."""
import math
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter

here = os.path.dirname(os.path.abspath(__file__))
out = os.path.join(here, "assets")
os.makedirs(out, exist_ok=True)

SS = 4                     # supersampling
S = 1024 * SS


def gradient(size, top, bottom):
    g = Image.new("RGBA", (1, 256))
    for y in range(256):
        t = y / 255
        g.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(4)))
    return g.resize(size, Image.BICUBIC)


def mark(size):
    """The ring + spark, white with a soft vertical sheen, on transparency."""
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    c = size / 2
    ro, th = size * 0.355, size * 0.118
    cx = c - size * 0.035
    # open ring: a thick arc, the gap on the right
    gap = 46
    d.arc((cx - ro, c - ro, cx + ro, c + ro), gap, 360 - gap, fill=255, width=int(th))
    # round caps on the ring's ends
    rm = ro - th / 2
    for a in (gap, -gap):
        x, y = cx + rm * math.cos(math.radians(a)), c + rm * math.sin(math.radians(a))
        d.ellipse((x - th / 2, y - th / 2, x + th / 2, y + th / 2), fill=255)
    # four-point spark (an astroid) sitting in the opening
    sx, sy, R = cx + ro * 0.88, c, size * 0.200
    pts = []
    for i in range(720):
        t = 2 * math.pi * i / 720
        pts.append((sx + R * math.cos(t) ** 3, sy + R * math.sin(t) ** 3))
    d.polygon(pts, fill=255)
    # small companion spark
    sx2, sy2, R2 = cx + ro * 1.10, c - size * 0.205, size * 0.062
    d.polygon([(sx2 + R2 * math.cos(2 * math.pi * i / 360) ** 3, sy2 + R2 * math.sin(2 * math.pi * i / 360) ** 3)
               for i in range(360)], fill=255)
    fill = gradient((size, size), (255, 255, 255, 255), (176, 176, 182, 255))
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    img.paste(fill, (0, 0), m)
    return img


# --- the app icon: mark on a black squircle with a hairline border
tile = Image.new("RGBA", (S, S), (0, 0, 0, 0))
td = ImageDraw.Draw(tile)
pad = int(S * 0.04)
rad = int(S * 0.235)
td.rounded_rectangle((pad, pad, S - pad, S - pad), radius=rad, fill=(10, 10, 11, 255))
# faint top sheen inside the tile
sheen = gradient((S, S), (255, 255, 255, 20), (255, 255, 255, 0))
tm = Image.new("L", (S, S), 0)
ImageDraw.Draw(tm).rounded_rectangle((pad, pad, S - pad, S - pad), radius=rad, fill=255)
tile = Image.alpha_composite(tile, Image.composite(sheen, Image.new("RGBA", (S, S), (0, 0, 0, 0)), tm))
ImageDraw.Draw(tile).rounded_rectangle((pad, pad, S - pad, S - pad), radius=rad, outline=(58, 58, 64, 255),
                                       width=int(S * 0.006))
mk = mark(int(S * 0.70))
glow = mk.copy().filter(ImageFilter.GaussianBlur(S * 0.02))
glow.putalpha(ImageChops.multiply(glow.getchannel("A"), Image.new("L", glow.size, 70)))
off = (S - mk.width) // 2
tile.alpha_composite(glow, (off, off))
tile.alpha_composite(mk, (off, off))
icon = tile.resize((1024, 1024), Image.LANCZOS)
icon.save(os.path.join(out, "icon.png"))
icon.save(os.path.join(out, "icon.ico"), sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48),
                                                (64, 64), (128, 128), (256, 256)])

# --- the bare mark (for the sidebar / about)
mark(S // 2).resize((512, 512), Image.LANCZOS).save(os.path.join(out, "logo.png"))

# --- dropdown chevron used by the stylesheet
a = Image.new("RGBA", (40, 24), (0, 0, 0, 0))
ImageDraw.Draw(a).line([(4, 4), (20, 19), (36, 4)], fill=(170, 170, 178, 255), width=5, joint="curve")
a.resize((10, 6), Image.LANCZOS).save(os.path.join(out, "arrow.png"))
print("icon written")

# --- the setup's art: the mark with a gently twinkling spark, 33 frames of 192 px in rows of 13 (played at 30 fps)
FR, COLS, N = 192, 13, 33


def frame(t):
    size = FR * 4
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    c = size / 2
    ro, th = size * 0.355, size * 0.118
    cx = c - size * 0.035
    gap = 46
    d.arc((cx - ro, c - ro, cx + ro, c + ro), gap, 360 - gap, fill=255, width=int(th))
    rm = ro - th / 2
    for a in (gap, -gap):
        x, y = cx + rm * math.cos(math.radians(a)), c + rm * math.sin(math.radians(a))
        d.ellipse((x - th / 2, y - th / 2, x + th / 2, y + th / 2), fill=255)
    pulse = 0.5 - 0.5 * math.cos(2 * math.pi * t)                      # 0 -> 1 -> 0 over the loop
    big = size * 0.200 * (0.94 + 0.10 * pulse)
    small = size * 0.062 * (0.75 + 0.55 * (0.5 - 0.5 * math.cos(2 * math.pi * (t + 0.5))))
    rot = math.radians(18 * math.sin(2 * math.pi * t))
    for sx, sy, r, a0 in ((cx + ro * 0.88, c, big, 0.0), (cx + ro * 1.10, c - size * 0.205, small, rot)):
        pts = []
        for i in range(720):
            u = 2 * math.pi * i / 720
            x, y = r * math.cos(u) ** 3, r * math.sin(u) ** 3
            pts.append((sx + x * math.cos(a0) - y * math.sin(a0), sy + x * math.sin(a0) + y * math.cos(a0)))
        d.polygon(pts, fill=255)
    m = m.resize((FR, FR), Image.LANCZOS)
    img = Image.new("RGBA", (FR, FR), (0, 0, 0, 0))
    img.paste(gradient((FR, FR), (255, 255, 255, 255), (186, 186, 192, 255)), (0, 0), m)
    glow = img.filter(ImageFilter.GaussianBlur(FR * 0.035))
    glow.putalpha(ImageChops.multiply(glow.getchannel("A"), Image.new("L", glow.size, int(55 + 55 * pulse))))
    out = Image.new("RGBA", (FR, FR), (0, 0, 0, 0))
    out.alpha_composite(glow)
    out.alpha_composite(img)
    return out


sheet = Image.new("RGBA", (FR * COLS, FR * math.ceil(N / COLS)), (0, 0, 0, 0))
for i in range(N):
    sheet.paste(frame(i / N), (i % COLS * FR, i // COLS * FR))
sheet.save(os.path.join(out, "setup-art.png"), optimize=True)
print("setup art written")
