#!/usr/bin/env python3
"""
ดึงข้อมูลจาก Google Apps Script มาเตรียมไว้ล่วงหน้า เพื่อให้ nginx เสิร์ฟเป็นไฟล์ static ล้วน

ทำ 3 อย่างต่อรอบ:
  1. ดึง JSON แล้วตัดแถวว่าง / แถวที่ status ไม่ใช่ TRUE ทิ้ง
  2. โหลดรูปที่ยังไม่มี แล้วย่อเป็น WebP หลายขนาด เก็บลง /data/media
  3. เขียน /data/cv.json แบบ atomic (เขียน .tmp แล้ว rename)

รูปที่โหลดไว้แล้วจะถูกถาม ETag/Last-Modified ก่อน ถ้าต้นทางไม่เปลี่ยนก็ข้าม ไม่โหลดซ้ำ
"""

import base64
import hashlib
import io
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone

import requests
from PIL import Image, ImageFile

import og

ImageFile.LOAD_TRUNCATED_IMAGES = True

STRICT    = "--strict" in sys.argv   # ใช้ตอน build: รูปหลุดเยอะให้ถือว่า build พัง
API_URL   = os.environ.get("API_URL", "").strip()
DATA_DIR  = os.environ.get("DATA_DIR", "/data")
INTERVAL  = int(os.environ.get("INTERVAL", "3600"))
TIMEOUT   = int(os.environ.get("TIMEOUT", "120"))
WIDTHS    = [int(w) for w in os.environ.get("WIDTHS", "400,600,800,1600").split(",")]
QUALITY   = int(os.environ.get("WEBP_QUALITY", "82"))
RETRIES   = int(os.environ.get("RETRIES", "3"))
IMG_TIMEOUT = int(os.environ.get("IMG_TIMEOUT", "30"))   # ต่อรูปหนึ่งใบ
IMG_RETRIES = int(os.environ.get("IMG_RETRIES", "3"))

PUBLIC_DIR  = os.environ.get("PUBLIC_DIR", "/public")   # index.html ต้นฉบับ (mount แบบ read-only)
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip()  # Discord / Telegram / Slack – ว่างไว้ = ไม่แจ้งเตือน
SITE_URL    = os.environ.get("SITE_URL", "").strip()     # เช่น https://cv.example.com ใช้ทำ og:url

MEDIA_DIR     = os.path.join(DATA_DIR, "media")
CV_PATH       = os.path.join(DATA_DIR, "cv.json")
INDEX_PATH    = os.path.join(DATA_DIR, "index.html")
OG_PATH       = os.path.join(DATA_DIR, "og-image.png")
STATUS_PATH   = os.path.join(DATA_DIR, "status.json")
MANIFEST_PATH = os.path.join(MEDIA_DIR, "manifest.json")

# ฟิลด์ที่เก็บลิงก์รูปในแต่ละ section
IMAGE_FIELDS = {"portfolio": "image_url", "profile": "pic"}

session = requests.Session()
session.headers["User-Agent"] = "portfolio-updater/1.0"


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}Z] {msg}", flush=True)


def atomic_write(path, data, binary=False):
    """เขียนลง .tmp ก่อนแล้ว rename ทับ — ผู้อ่านจะไม่มีวันเจอไฟล์ที่เขียนค้างครึ่งทาง"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    mode = "wb" if binary else "w"
    with open(tmp, mode) as f:
        if binary:
            f.write(data)
        else:
            f.write(data)
    os.replace(tmp, path)


def load_manifest():
    try:
        with open(MANIFEST_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def is_blank(v):
    return str(v).strip() == ""


def clean_rows(rows):
    """ตัดแถวว่างและแถวที่ status ไม่ใช่ TRUE ออกตั้งแต่ฝั่งนี้ เบราว์เซอร์จะได้ไม่ต้องรับมาเปล่า ๆ"""
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if all(is_blank(v) for v in row.values()):
            continue
        if "status" in row:
            st = row["status"]
            if not (st is True or str(st).strip().upper() == "TRUE"):
                continue
        out.append(row)
    return out


def fetch_payload():
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            t0 = time.time()
            r = session.get(API_URL, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            log(f"fetched API in {time.time() - t0:.1f}s ({len(r.content)} bytes)")
            return data
        except Exception as e:
            last_err = e
            log(f"fetch attempt {attempt}/{RETRIES} failed: {e}")
            if attempt < RETRIES:
                time.sleep(5 * attempt)
    raise last_err


def url_key(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def variant_name(key, width, content_hash):
    """
    ใส่ hash ของ "เนื้อไฟล์" ไว้ในชื่อ ไม่ใช่แค่ hash ของ URL

    จำเป็นเพราะเราตั้ง Cache-Control: immutable ให้ /media/* (แคชหนึ่งปี ไม่ revalidate)
    ถ้าชื่อไฟล์ผูกกับ URL อย่างเดียว แล้ววันหนึ่งรูปที่ URL เดิมถูกเปลี่ยน
    (เกิดได้จริงกับ Google Drive ที่ลิงก์คงเดิมแต่สลับไฟล์ข้างในได้)
    คนที่เคยเข้าเว็บจะค้างรูปเก่าไว้เป็นปีโดยไม่มีทางรู้
    """
    return f"{key}-{width}-{content_hash}.webp"


def usable_widths(src_w):
    """
    ขนาดที่จะสร้างจริง: ทุกขนาดใน WIDTHS ที่เล็กกว่าต้นฉบับ บวกอีกหนึ่งตัวเท่าต้นฉบับ
    ไม่ขยายรูปเล็กให้ใหญ่เกินจริง และไม่สร้างไฟล์เนื้อหาซ้ำกันสองชื่อ
      ต้นฉบับ 1280 -> [400, 600, 800, 1280]
      ต้นฉบับ  574 -> [400, 574]
    """
    return sorted({w for w in WIDTHS if w < src_w} | {min(src_w, max(WIDTHS))})


ENTRY_VERSION = 3   # ขยับเลขนี้เมื่อรูปแบบข้อมูลใน manifest เปลี่ยน จะบังคับให้ทำรูปใหม่ทั้งหมด


def entry_ok(entry):
    """entry ใช้ต่อได้ไหม – ต้องเป็นเวอร์ชันปัจจุบันและไฟล์ครบ"""
    if not entry or entry.get("v") != ENTRY_VERSION:
        return False
    return variants_present(entry)


def variants_present(entry):
    files = (entry or {}).get("files") or {}
    if not files:
        return False
    return all(os.path.exists(os.path.join(MEDIA_DIR, f)) for f in files.values())


def download_and_resize(url, key, entry):
    """
    คืนค่า True ถ้าไฟล์พร้อมใช้งาน (ไม่ว่าจะเพิ่งโหลดหรือของเดิมยังใช้ได้)
    ใช้ ETag / Last-Modified ถามต้นทางก่อน ถ้าไม่เปลี่ยนก็ไม่โหลดซ้ำ
    """
    headers = {}
    if entry_ok(entry):
        if entry.get("etag"):
            headers["If-None-Match"] = entry["etag"]
        if entry.get("last_modified"):
            headers["If-Modified-Since"] = entry["last_modified"]

    # postimg หลุดเป็นครั้งคราว ลองซ้ำก่อนยอมแพ้ — ไม่งั้นรูปใบนั้นจะหลุดไปจาก snapshot
    # ทั้งรอบ แล้วหน้าเว็บต้องกลับไปดึงจาก postimg ตอนผู้ใช้เปิด ซึ่งช้ากว่ามาก
    # timeout สั้นกว่าของ API เพราะรูปใบเดียวไม่ควรดึง build ค้างเป็นนาที
    r = None
    for attempt in range(1, IMG_RETRIES + 1):
        try:
            r = session.get(url, timeout=IMG_TIMEOUT, headers=headers)
            break
        except Exception as e:
            if attempt == IMG_RETRIES:
                log(f"  ! download failed {url}: {e}")
                return entry_ok(entry)
            time.sleep(2 * attempt)

    if r.status_code == 304:
        return True
    if r.status_code != 200:
        log(f"  ! HTTP {r.status_code} for {url}")
        return entry_ok(entry)

    try:
        img = Image.open(io.BytesIO(r.content))
        img.load()
    except Exception as e:
        log(f"  ! not a readable image {url}: {e}")
        return False

    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    src_w = img.width

    # LQIP: ย่อเหลือ 20px ใช้เป็นภาพเบลอคั่นระหว่างรอรูปจริง (~300–600 bytes)
    try:
        tiny = img.convert("RGB").resize((20, max(1, round(img.height * 20 / src_w))), Image.LANCZOS)
        tbuf = io.BytesIO()
        tiny.save(tbuf, format="WEBP", quality=45, method=4)
        lqip = "data:image/webp;base64," + base64.b64encode(tbuf.getvalue()).decode("ascii")
    except Exception:
        lqip = ""

    widths = usable_widths(src_w)
    files = {}
    for w in widths:
        if w >= src_w:
            resized = img
        else:
            h = max(1, round(img.height * w / src_w))
            resized = img.resize((w, h), Image.LANCZOS)
        buf = io.BytesIO()
        save_img = resized.convert("RGB") if resized.mode == "RGBA" else resized
        save_img.save(buf, format="WEBP", quality=QUALITY, method=5)
        blob = buf.getvalue()
        name = variant_name(key, w, hashlib.sha1(blob).hexdigest()[:8])
        atomic_write(os.path.join(MEDIA_DIR, name), blob, binary=True)
        files[str(w)] = name

    return {
        "v": ENTRY_VERSION,
        "key": key,
        "url": url,
        "etag": r.headers.get("ETag"),
        "last_modified": r.headers.get("Last-Modified"),
        "source_width": src_w,
        "source_height": img.height,
        "widths": widths,
        "files": files,
        "lqip": lqip,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def process_images(payload, manifest):
    """เดินทุก section ที่มีรูป โหลด/ย่อ แล้วแปะ image_src + image_srcset กลับเข้าไปใน payload"""
    new_manifest = {}
    referenced = set()
    stats = {"reused": 0, "downloaded": 0, "failed": 0}

    def handle(item, field, prefix):
        raw = str(item.get(field) or "").strip()
        if not raw.startswith("http"):
            return
        key = url_key(raw)
        referenced.add(key)
        entry = manifest.get(key)

        if entry_ok(entry):
            result = download_and_resize(raw, key, entry)
            if result is True:
                new_manifest[key] = entry
                stats["reused"] += 1
            elif isinstance(result, dict):
                new_manifest[key] = result
                stats["downloaded"] += 1
            else:
                stats["failed"] += 1
                return
        else:
            result = download_and_resize(raw, key, None)
            if isinstance(result, dict):
                new_manifest[key] = result
                stats["downloaded"] += 1
            elif result is True:
                new_manifest[key] = entry or {"url": raw}
                stats["reused"] += 1
            else:
                stats["failed"] += 1
                return

        info = new_manifest[key]
        files = info.get("files") or {}
        widths = sorted(int(w) for w in files)
        if not widths:
            stats["failed"] += 1
            return
        # ตัวที่ใส่ใน src: ขนาดพอดีการ์ด (~600px) หรือตัวใหญ่สุดที่มีถ้ารูปต้นฉบับเล็กกว่านั้น
        default_w = min((w for w in widths if w >= 600), default=max(widths))
        item[f"{prefix}_src"] = f"/media/{files[str(default_w)]}"
        item[f"{prefix}_srcset"] = ", ".join(f"/media/{files[str(w)]} {w}w" for w in widths)
        item[f"{prefix}_full"] = f"/media/{files[str(max(widths))]}"
        if info.get("lqip"):
            item[f"{prefix}_lqip"] = info["lqip"]
        if info.get("source_width") and info.get("source_height"):
            # ส่งอัตราส่วนไปด้วย หน้าเว็บจะได้จองพื้นที่ถูกตั้งแต่แรก ไม่เกิดภาพกระตุก
            item[f"{prefix}_ratio"] = round(info["source_width"] / info["source_height"], 4)

    for row in payload.get("portfolio", []):
        handle(row, "image_url", "image")

    profile = payload.get("profile")
    if isinstance(profile, dict):
        handle(profile, "pic", "pic")

    # ลบไฟล์ที่ไม่มีใครอ้างถึงแล้ว กัน /data บวมไปเรื่อย ๆ
    #
    # ต้องเทียบ "ชื่อไฟล์จริง" ไม่ใช่แค่ key ของ URL เพราะตอนนี้ชื่อมี hash ของเนื้อไฟล์อยู่ด้วย
    # ถ้ารูปที่ URL เดิมถูกเปลี่ยน ไฟล์ hash เก่าจะกลายเป็นขยะที่ไม่มีใครอ้างถึงทันที
    keep = {f for e in new_manifest.values() for f in (e.get("files") or {}).values()}
    removed = 0
    if os.path.isdir(MEDIA_DIR):
        for fname in os.listdir(MEDIA_DIR):
            if not fname.endswith(".webp") or fname in keep:
                continue
            try:
                os.remove(os.path.join(MEDIA_DIR, fname))
                removed += 1
            except OSError:
                pass

    log(f"images: {stats['downloaded']} new, {stats['reused']} unchanged, "
        f"{stats['failed']} failed, {removed} pruned")
    process_images.last_stats = stats
    return new_manifest


def esc(v):
    """escape สำหรับใส่ใน attribute ของ HTML"""
    return (str(v or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def build_og_image(payload):
    """สร้าง og-image.png จากรูปโปรไฟล์ที่โหลดมาแล้ว"""
    profile = payload.get("profile") or {}
    photo = None
    full = profile.get("pic_full") or profile.get("pic_src")
    if full:
        candidate = os.path.join(MEDIA_DIR, os.path.basename(full))
        if os.path.exists(candidate):
            with open(candidate, "rb") as f:
                photo = f.read()
    try:
        atomic_write(OG_PATH, og.build(profile, photo), binary=True)
        log(f"wrote {OG_PATH} ({os.path.getsize(OG_PATH) / 1024:.0f} KB)")
        return True
    except Exception as e:
        log(f"  ! og image failed: {e}")
        return False


def build_index_html(payload):
    """
    เอา index.html ต้นฉบับมาแทรก meta tags ที่สร้างจากข้อมูลจริง แล้วเขียนลง /data
    crawler ของ LINE/Facebook ไม่รัน JavaScript จึงต้องฝัง tag ไว้ใน HTML ตั้งแต่ต้น
    """
    src = os.path.join(PUBLIC_DIR, "index.html")
    if not os.path.exists(src):
        log(f"  ! ไม่พบ {src} – ข้ามการแทรก meta (nginx จะเสิร์ฟตัวใน image แทน)")
        return False

    with open(src, encoding="utf-8") as f:
        html = f.read()
    if "<!--OG-->" not in html:
        log("  ! ไม่พบ marker <!--OG--> ใน index.html – ข้ามการแทรก meta")
        return False

    profile = payload.get("profile") or {}
    name = str(profile.get("fullname") or "Portfolio").strip()
    nick = str(profile.get("Nickname") or "").strip()
    title = f"{name} ({nick})" if nick else name
    position = str(profile.get("position") or "").strip()

    n_proj = len(payload.get("portfolio") or [])
    n_exp = len(payload.get("experience") or [])
    desc_bits = [b for b in (position, f"ผลงาน {n_proj} ชิ้น" if n_proj else "",
                             f"ประสบการณ์ {n_exp} ที่ทำงาน" if n_exp else "")]
    desc = " · ".join(b for b in desc_bits if b) or "Portfolio"

    # og:image / og:url ต้องเป็น URL เต็มเสมอ crawler ของ LINE ไม่ยอมรับ path แบบ relative
    # ถ้าไม่ได้ตั้ง SITE_URL ไว้ ก็ใส่ placeholder __BASE__ แล้วให้ nginx เติม scheme+host ให้ตอนเสิร์ฟ
    base = SITE_URL.rstrip("/") if SITE_URL else "__BASE__"
    og_url = f"{base}/og-image.png"
    page_url = base + "/"

    ld = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": name,
        "jobTitle": position,
        "email": profile.get("email") or "",
        "telephone": profile.get("phone") or "",
        "url": page_url,
        "image": og_url,
    }

    meta = f"""<meta name="description" content="{esc(desc)}">
    <meta property="og:type" content="profile">
    <meta property="og:title" content="{esc(title)}">
    <meta property="og:description" content="{esc(desc)}">
    <meta property="og:image" content="{esc(og_url)}">
    <meta property="og:image:width" content="1200">
    <meta property="og:image:height" content="630">
    <meta property="og:locale" content="th_TH">
    <meta property="og:url" content="{esc(page_url)}">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{esc(title)}">
    <meta name="twitter:description" content="{esc(desc)}">
    <meta name="twitter:image" content="{esc(og_url)}">
    <script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>"""

    html = html.replace("<!--OG-->", meta, 1)
    # meta img-proxy: ตั้งเป็น on ให้เลย เพราะไฟล์นี้จะถูกเสิร์ฟจาก nginx เท่านั้น
    html = html.replace('<meta name="img-proxy" content="off">',
                        '<meta name="img-proxy" content="on">', 1)
    atomic_write(INDEX_PATH, html)
    log(f"wrote {INDEX_PATH} (แทรก meta tags แล้ว)")
    return True


def notify(text):
    """ยิงแจ้งเตือนเข้า Discord / Slack / Telegram – รูปแบบ payload เดาจาก URL"""
    if not WEBHOOK_URL:
        return
    try:
        if "api.telegram.org" in WEBHOOK_URL:
            chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
            requests.post(WEBHOOK_URL, timeout=15,
                          json={"chat_id": chat_id, "text": text})
        else:
            # Discord และ Slack รับ field ต่างกัน ส่งไปทั้งคู่เลย อีกฝั่งจะเมินตัวที่ไม่รู้จัก
            requests.post(WEBHOOK_URL, timeout=15,
                          json={"content": text, "text": text})
        log("แจ้งเตือนออกไปแล้ว")
    except Exception as e:
        log(f"  ! แจ้งเตือนไม่สำเร็จ: {e}")


def read_status():
    try:
        with open(STATUS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def write_status(**kw):
    st = read_status()
    st.update(kw)
    atomic_write(STATUS_PATH, json.dumps(st, ensure_ascii=False, indent=1))
    return st


def run_once():
    if not API_URL:
        log("ERROR: API_URL is not set")
        return False

    os.makedirs(MEDIA_DIR, exist_ok=True)
    started = time.time()
    prev = read_status()
    fails = int(prev.get("consecutive_failures") or 0)

    try:
        payload = fetch_payload()
    except Exception as e:
        fails += 1
        has_snapshot = os.path.exists(CV_PATH)
        log(f"ERROR: giving up on fetch: {e}")
        log("ยังเสิร์ฟ snapshot เดิมต่อได้" if has_snapshot else "ยังไม่มี snapshot เลย")
        write_status(last_error=str(e),
                     last_error_at=datetime.now(timezone.utc).isoformat(),
                     consecutive_failures=fails,
                     serving_stale=has_snapshot)
        # แจ้งเฉพาะครั้งแรกที่พัง กันสแปมตอนล่มยาว
        if fails == 1:
            age = ""
            if prev.get("last_success_at"):
                age = f"\nsnapshot ล่าสุด: {prev['last_success_at']}"
            notify(f"⚠️ portfolio updater ดึงข้อมูลไม่สำเร็จ\n{e}{age}\n"
                   f"{'เว็บยังเสิร์ฟของเดิมอยู่' if has_snapshot else 'ยังไม่มีข้อมูลเลย เว็บจะ fallback ไป Apps Script'}")
        return False

    before = len(payload.get("portfolio", []))
    for section in ("portfolio", "experience", "skills", "services"):
        if isinstance(payload.get(section), list):
            payload[section] = clean_rows(payload[section])
    after = len(payload.get("portfolio", []))
    log(f"portfolio rows: {before} -> {after} (ตัดแถวว่าง/ปิดสถานะทิ้ง {before - after} แถว)")

    manifest = load_manifest()
    new_manifest = process_images(payload, manifest)
    img_stats = getattr(process_images, "last_stats", {"failed": 0, "downloaded": 0, "reused": 0})
    atomic_write(MANIFEST_PATH, json.dumps(new_manifest, ensure_ascii=False, indent=1))

    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    atomic_write(CV_PATH, body)
    size_kb = len(body.encode("utf-8")) / 1024
    log(f"wrote {CV_PATH} ({size_kb:.1f} KB)")

    build_og_image(payload)
    build_index_html(payload)

    media_files = [f for f in os.listdir(MEDIA_DIR) if f.endswith(".webp")]
    media_bytes = sum(os.path.getsize(os.path.join(MEDIA_DIR, f)) for f in media_files)

    write_status(
        last_success_at=payload["generated_at"],
        last_run_seconds=round(time.time() - started, 1),
        consecutive_failures=0,
        serving_stale=False,
        last_error=None,
        counts={
            "portfolio": len(payload.get("portfolio") or []),
            "experience": len(payload.get("experience") or []),
            "skills": len(payload.get("skills") or []),
            "services": len(payload.get("services") or []),
        },
        media={"files": len(media_files), "megabytes": round(media_bytes / 1048576, 1)},
        cv_json_kb=round(size_kb, 1),
        interval_seconds=INTERVAL,
    )

    # กลับมาปกติหลังเคยพัง – บอกให้รู้ด้วย
    if fails:
        notify(f"✅ portfolio updater กลับมาปกติแล้ว (พังไป {fails} รอบ)")

    # โหมด --strict (ใช้ตอน build เพื่อ deploy): ถ้ารูปหลุดเยอะแปลว่าต้นทางมีปัญหาจริง
    # ปล่อยให้ deploy ไปจะได้เว็บที่กลับไปดึงรูปจาก postimg ตอนผู้ใช้เปิด ซึ่งช้าและเปราะ
    # ยอมให้ build พังแล้วคงของเดิมไว้ดีกว่า
    if STRICT:
        total_imgs = img_stats["downloaded"] + img_stats["reused"] + img_stats["failed"]
        if img_stats["failed"] and total_imgs:
            ratio = img_stats["failed"] / total_imgs
            log(f"strict: รูปโหลดไม่สำเร็จ {img_stats['failed']}/{total_imgs} ({ratio:.0%})")
            if ratio > 0.2:
                log("strict: เกิน 20% — ถือว่าไม่ควร deploy")
                return False

    return True


def preflight():
    """เช็กให้ครบก่อนเริ่มวน จะได้ไม่พังเงียบแล้วงงว่าทำไมเว็บไม่มีข้อมูล"""
    ok = True

    if not API_URL:
        log("ERROR: ไม่ได้ตั้ง API_URL – ตั้งใน docker-compose.yml ที่ environment ของ service updater")
        ok = False

    try:
        os.makedirs(MEDIA_DIR, exist_ok=True)
        probe = os.path.join(DATA_DIR, ".write-test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except Exception as e:
        log(f"ERROR: เขียนลง {DATA_DIR} ไม่ได้: {e}")
        log("       bind mount ./data บน NAS มักเป็นของ user อื่น ลองสั่ง:")
        log("       sudo chown -R 1000:1000 /volume1/docker/portfolio/data")
        log("       หรือเอา user: ออกจาก docker-compose.yml เพื่อให้รันเป็น root")
        ok = False

    if not os.path.exists(os.path.join(PUBLIC_DIR, "index.html")):
        log(f"WARN: ไม่พบ {PUBLIC_DIR}/index.html – จะข้ามการแทรก meta tags")
        log("      เช็กว่า docker-compose.yml mount ./public:/public:ro ไว้แล้ว")

    return ok


def main():
    once = "--once" in sys.argv
    log(f"updater starting (interval={INTERVAL}s, once={once}, widths={WIDTHS})")
    log(f"DATA_DIR={DATA_DIR} PUBLIC_DIR={PUBLIC_DIR}")

    if not preflight():
        log("preflight ไม่ผ่าน – แก้ตามด้านบนแล้ว restart container")
        if once:
            return
        # ไม่ออกทันที ไม่งั้น restart policy จะวน crash loop รัว ๆ
        while True:
            time.sleep(60)
            if preflight():
                log("preflight ผ่านแล้ว เริ่มทำงานต่อ")
                break
    while True:
        ok = False
        try:
            ok = run_once()
        except Exception as e:
            log(f"ERROR: unexpected: {e}")
            log(traceback.format_exc())
            try:
                write_status(last_error=f"unexpected: {e}",
                             last_error_at=datetime.now(timezone.utc).isoformat())
            except Exception:
                pass
        if once:
            if STRICT and not ok:
                sys.exit(1)
            return
        log(f"sleeping {INTERVAL}s")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
