"""สร้างรูป OG (1200x630) สำหรับตอนแชร์ลิงก์ ใช้ธีมทองเดียวกับหน้าเว็บ"""

import io
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1200, 630
BG        = (17, 17, 17)
GOLD      = (212, 175, 55)
GOLD_LT   = (253, 224, 71)
WHITE     = (255, 255, 255)
MUTED     = (140, 140, 140)

# ไล่หาฟอนต์ที่รองรับไทย เรียงจากที่อยากได้ก่อน
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSansThai-Bold.ttf",
    "/usr/share/fonts/truetype/tlwg/Waree-Bold.ttf",
    "/usr/share/fonts/truetype/tlwg/Loma-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Ayuthaya.ttf",
    "/System/Library/Fonts/Thonburi.ttc",
]
FONT_CANDIDATES_REG = [
    "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
    "/usr/share/fonts/truetype/tlwg/Waree.ttf",
    "/usr/share/fonts/truetype/tlwg/Loma.ttf",
    "/System/Library/Fonts/Supplemental/Ayuthaya.ttf",
    "/System/Library/Fonts/Thonburi.ttc",
]


def _font(size, bold=False):
    for path in (FONT_CANDIDATES if bold else FONT_CANDIDATES_REG):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _ink(font, ch, box=90):
    """วาดตัวอักษรลงผ้าใบเปล่าแล้วคืน (bbox, bytes) ไว้เทียบว่าฟอนต์วาดออกจริงไหม"""
    im = Image.new("L", (box, box), 0)
    ImageDraw.Draw(im).text((6, 6), ch, font=font, fill=255)
    return im.getbbox(), im.tobytes()


def check_thai_font():
    """
    ยืนยันว่าเรนเดอร์อักษรไทยได้จริง คืน (ok, รายละเอียด)

    เช็ก 3 ชั้น เพราะแต่ละชั้นหลุดได้:
      1. Pillow ใช้ฟอนต์สำรองในตัว (.path เป็น BytesIO ไม่ใช่ path ไฟล์) -> ไม่มีอักษรไทย
      2. ฟอนต์วาดอักษรไทยไม่ออกเลย (ผ้าใบว่าง)
      3. ฟอนต์วาดออกแต่เป็นกล่อง .notdef (เหมือนกับตัวอักษรที่ไม่มีอยู่จริง)
    """
    f = _font(48, bold=True)
    path = getattr(f, "path", None)
    if not isinstance(path, str):
        return False, "Pillow ใช้ฟอนต์สำรองในตัว ซึ่งไม่มีอักษรไทย"

    thai_bbox, thai_bytes = _ink(f, "ก")
    if not thai_bbox:
        return False, f"{path} วาดอักษรไทยไม่ออก"

    # U+E000 เป็นช่วง private use ไม่มีฟอนต์ไหนมีจริง ถ้าวาดออกมาเหมือนกันแปลว่าเป็นกล่อง .notdef
    _, notdef_bytes = _ink(f, "\uE000")
    if thai_bytes == notdef_bytes:
        return False, f"{path} ไม่มีอักษรไทย (วาดออกมาเป็นกล่อง)"

    return True, path


def _fit(draw, text, size, bold, max_w, min_size=28):
    """ลดขนาดฟอนต์ลงจนกว่าข้อความจะพอดีความกว้างที่กำหนด"""
    while size > min_size:
        f = _font(size, bold)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 3
    return _font(min_size, bold)


def _rounded(img, radius):
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.size[0] - 1, img.size[1] - 1],
                                           radius=radius, fill=255)
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def build(profile, photo_bytes=None):
    im = Image.new("RGB", (W, H), BG)

    # แสงทองจาง ๆ มุมขวาบน ให้อารมณ์เดียวกับ glow-blob ในหน้าเว็บ
    glow = Image.new("RGB", (W, H), BG)
    gd = ImageDraw.Draw(glow)
    gd.ellipse([W - 620, -300, W + 200, 420], fill=(46, 39, 15))
    gd.ellipse([-260, H - 220, 300, H + 240], fill=(30, 26, 12))
    im = Image.blend(im, glow.filter(ImageFilter.GaussianBlur(120)), 0.9)

    d = ImageDraw.Draw(im)

    # กรอบทองบาง ๆ
    d.rounded_rectangle([18, 18, W - 19, H - 19], radius=26, outline=(74, 62, 24), width=2)

    # รูปโปรไฟล์ด้านขวา
    photo_w = 0
    if photo_bytes:
        try:
            ph = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
            box_w, box_h = 300, 400
            scale = max(box_w / ph.width, box_h / ph.height)
            ph = ph.resize((max(1, round(ph.width * scale)), max(1, round(ph.height * scale))),
                           Image.LANCZOS)
            left = (ph.width - box_w) // 2
            top = (ph.height - box_h) // 2
            ph = ph.crop((left, top, left + box_w, top + box_h))
            ph = _rounded(ph, 34)
            px, py = W - box_w - 78, (H - box_h) // 2
            # เงาทองรอบรูป
            shadow = Image.new("RGB", (W, H), BG)
            ImageDraw.Draw(shadow).rounded_rectangle(
                [px - 10, py - 10, px + box_w + 10, py + box_h + 10], radius=44, fill=(64, 53, 20))
            im = Image.blend(im, shadow.filter(ImageFilter.GaussianBlur(28)), 0.55)
            d = ImageDraw.Draw(im)
            im.paste(ph, (px, py), ph)
            photo_w = box_w + 78 + 40
        except Exception:
            photo_w = 0

    x = 80
    text_w = W - x - max(photo_w, 80)

    # โมโนแกรม TL.
    f_logo = _font(34, True)
    d.text((x, 66), "TL", font=f_logo, fill=WHITE)
    logo_w = d.textlength("TL", font=f_logo)
    d.text((x + logo_w + 3, 66), ".", font=f_logo, fill=GOLD)

    # ป้ายหมวด
    f_kick = _font(21, True)
    d.text((x, 186), "PROFESSIONAL PROFILE", font=f_kick, fill=GOLD)

    # ชื่อ
    name = str(profile.get("fullname") or "").strip() or "Portfolio"
    nick = str(profile.get("Nickname") or "").strip()
    if nick:
        name = f"{name} ({nick})"
    f_name = _fit(d, name, 74, True, text_w)
    d.text((x, 232), name, font=f_name, fill=WHITE)

    # ตำแหน่ง
    pos = str(profile.get("position") or "").strip()
    if pos:
        f_pos = _fit(d, pos, 40, False, text_w, min_size=24)
        d.text((x, 232 + f_name.size + 26), pos, font=f_pos, fill=(190, 190, 190))

    # เส้นคั่นทอง
    d.rectangle([x, H - 148, x + 96, H - 145], fill=GOLD)

    # ติดต่อ
    bits = [str(profile.get(k) or "").strip() for k in ("email", "phone")]
    line = "   •   ".join(b for b in bits if b)
    if line:
        f_c = _fit(d, line, 25, False, text_w, min_size=16)
        d.text((x, H - 116), line, font=f_c, fill=MUTED)

    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
