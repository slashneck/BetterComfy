"""Draws docs/banner.png (the top of the GitHub page) and docs/social-preview.png (the link preview).
Needs Pillow and the Segoe UI fonts that come with Windows."""
import math
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def font(size, weight="Bold"):
    try:
        f = ImageFont.truetype(os.path.join(FONTS, "SegUIVar.ttf"), size)
        f.set_variation_by_name({"Bold": "Display Bold", "Semibold": "Display Semibold", "Text": "Text"}.get(weight, weight))
        return f
    except (OSError, ValueError):
        name = {"Bold": "segoeuib.ttf", "Semibold": "seguisb.ttf"}.get(weight, "segoeui.ttf")
        return ImageFont.truetype(os.path.join(FONTS, name), size)


def background(w, h):
    """Near black, a little lighter towards the bottom middle."""
    img = Image.new("RGB", (w, h), (10, 10, 10))
    glow = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(glow)
    d.ellipse((w * 0.05, h * 0.55, w * 0.95, h * 1.75), fill=255)
    glow = glow.filter(ImageFilter.GaussianBlur(h * 0.28))
    light = Image.new("RGB", (w, h), (26, 26, 27))
    return Image.composite(light, img, glow)


def mark(size):
    """The ring and the spark, white with a soft sheen (the same shapes as the app icon)."""
    s = size * 4
    m = Image.new("L", (s, s), 0)
    d = ImageDraw.Draw(m)
    c = s / 2
    ro, th = s * 0.355, s * 0.118
    cx = c - s * 0.035
    gap = 46
    d.arc((cx - ro, c - ro, cx + ro, c + ro), gap, 360 - gap, fill=255, width=int(th))
    rm = ro - th / 2
    for a in (gap, -gap):
        x, y = cx + rm * math.cos(math.radians(a)), c + rm * math.sin(math.radians(a))
        d.ellipse((x - th / 2, y - th / 2, x + th / 2, y + th / 2), fill=255)
    for sx, sy, r in ((cx + ro * 0.88, c, s * 0.200), (cx + ro * 1.10, c - s * 0.205, s * 0.062)):
        d.polygon([(sx + r * math.cos(2 * math.pi * i / 720) ** 3, sy + r * math.sin(2 * math.pi * i / 720) ** 3)
                   for i in range(720)], fill=255)
    m = m.resize((size, size), Image.LANCZOS)
    grad = Image.new("RGB", (1, 256))
    for y in range(256):
        v = int(244 - 44 * y / 255)
        grad.putpixel((0, y), (v, v, v + 2))
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(grad.resize((size, size)), (0, 0), m)
    return out


def chip(draw, x, y, text, f, pad=30, h=64):
    tw = draw.textlength(text, font=f)
    w = tw + pad * 2
    draw.rounded_rectangle((x, y, x + w, y + h), radius=h / 2, fill=(20, 20, 21), outline=(58, 58, 62), width=3)
    draw.text((x + pad, y + h / 2), text, font=f, fill=(232, 232, 234), anchor="lm")
    return x + w


def compose(w, h, logo, title_size, tag_size, chip_size, chips=True):
    img = background(w, h).convert("RGBA")
    m = mark(logo)
    tf, gf, cf = font(title_size, "Bold"), font(tag_size, "Text"), font(chip_size, "Semibold")
    probe = ImageDraw.Draw(img)
    names = ("Pictures and videos", "Fast to best presets", "Free and open source")
    pad, ch = int(chip_size * 0.95), int(chip_size * 2.05)
    chips_w = sum(probe.textlength(t, font=cf) + pad * 2 for t in names) + chip_size * 0.6 * (len(names) - 1)
    text_w = max(probe.textlength("Better Comfy", font=tf), chips_w if chips else 0)
    gap = int(w * 0.05)
    x0 = int((w - (logo + gap + text_w)) / 2)
    # the text block: title, tagline, chips - centred on the middle of the banner, like the logo
    block = title_size * 0.95 + tag_size * 1.65 + ((ch + tag_size * 0.75) if chips else 0)
    top = h / 2 - block / 2
    ly = int(h / 2 - logo / 2)
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse((x0 + logo * 0.18, ly + logo * 0.98, x0 + logo * 0.78, ly + logo * 1.06),
                                   fill=(0, 0, 0, 170))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(logo * 0.04)))
    glow = m.copy().filter(ImageFilter.GaussianBlur(logo * 0.05))
    glow.putalpha(ImageChops.multiply(glow.getchannel("A"), Image.new("L", glow.size, 60)))
    img.alpha_composite(glow, (x0, ly))
    img.alpha_composite(m, (x0, ly))
    d = ImageDraw.Draw(img)
    tx = x0 + logo + gap
    d.text((tx - title_size * 0.04, top + title_size * 0.47), "Better Comfy", font=tf, fill=(244, 244, 245), anchor="lm")
    ty = top + title_size * 0.95 + tag_size * 0.85
    d.text((tx, ty), "ComfyUI, without the wires.", font=gf, fill=(150, 150, 156), anchor="lm")
    if chips:
        x, y = tx, ty + tag_size * 0.8 + tag_size * 0.55
        for t in names:
            x = chip(d, x, y, t, cf, pad=pad, h=ch) + chip_size * 0.6
    return img.convert("RGB")


os.makedirs(DOCS, exist_ok=True)
compose(2560, 840, 400, 196, 62, 34).save(os.path.join(DOCS, "banner.png"), optimize=True)
compose(1280, 640, 230, 92, 32, 18).save(os.path.join(DOCS, "social-preview.png"), optimize=True)
print("banner written")
