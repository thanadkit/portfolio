#!/usr/bin/env python3
"""
ปรับ dist/index.html ให้โหลดเร็วสำหรับคนที่เปิดครั้งแรก

ทำตรงนี้แทนการแก้ public/index.html เพื่อให้ไฟล์ต้นฉบับยังเหมือนฝั่ง Docker เป๊ะ
(sync-from-docker.sh จะได้ใช้ได้ต่อ)

สิ่งที่ทำ:
  1. เปลี่ยน Tailwind Play CDN (120 KB บีบแล้ว / 398 KB ดิบ + คอมไพล์ CSS ในเบราว์เซอร์)
     เป็น tailwind.css ที่ build ไว้ล่วงหน้า (~5 KB) เลิกบล็อกการ render
  2. ฝังข้อมูลลงใน HTML เลย ตัด round trip ที่ต้องไปขอ /api/cv
  3. preload รูปโปรไฟล์ซึ่งเป็น LCP ของหน้า
  4. ไม่โหลด tsparticles (38.7 KB) ถ้าผู้ใช้เปิด Save-Data หรือเน็ตช้า
"""
import io
import json
import os
import re
import sys

DIST = sys.argv[1] if len(sys.argv) > 1 else "dist"
index_path = os.path.join(DIST, "index.html")
cv_path = os.path.join(DIST, "cv.json")

html = io.open(index_path, encoding="utf-8").read()
cv = json.load(io.open(cv_path, encoding="utf-8"))
applied = []


def replace_once(old, new, label):
    global html
    n = html.count(old)
    if n != 1:
        sys.exit(f"ERROR [{label}]: หาเจอ {n} ที่ (ต้องเจอ 1) — public/index.html เปลี่ยนไปจากที่ optimize.py คาดไว้")
    html = html.replace(old, new, 1)
    applied.append(label)


# ── 1) Tailwind: CDN -> CSS ที่ build ไว้ ──────────────────────────
replace_once(
    '    <script src="https://cdn.tailwindcss.com"></script>\n',
    '    <link rel="stylesheet" href="/tailwind.css">\n',
    "tailwind: CDN -> prebuilt CSS",
)

# บล็อก tailwind.config ไม่มีประโยชน์แล้วเมื่อไม่มี runtime ของ Play CDN
m = re.search(r'[ \t]*<script>\s*\n\s*tailwind\.config = \{.*?\n[ \t]*</script>\n', html, re.S)
if not m:
    sys.exit("ERROR: หาบล็อก tailwind.config ไม่เจอ")
html = html[:m.start()] + html[m.end():]
applied.append("tailwind: ลบบล็อก config ออก")


# ── 2) ฝังข้อมูลลง HTML ตัด round trip ของ /api/cv ─────────────────
#
# ฝังเป็นค่าในสคริปต์ที่ใช้มันเลย ไม่แยกเป็น <script type="application/json">
# เพราะสคริปต์ตัวนี้อยู่ใน <head> และทำงานตอน parse — element ที่อยู่ถัดลงไปยังไม่เกิด
# getElementById จะได้ null แล้วตกไปยิง API ทั้งที่ฝังข้อมูลมาให้แล้ว
#
# "</" ต้อง escape กัน </script> ที่อยู่ในข้อความไปปิด tag ก่อนเวลา
# ("<\/" เป็น escape ที่ถูกต้องในสตริงของ JavaScript)
payload = json.dumps(cv, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
replace_once(
    "        window.dataPromise = fetchJson(API_URL)\n",
    "        // ข้อมูลถูกฝังมากับ HTML ตอน build แล้ว ไม่ต้องยิงขอ /api/cv อีกรอบ\n"
    "        // (ไฟล์ /api/cv ยังอยู่ เผื่อมีอะไรพังจะได้ยังมีทางถอย)\n"
    "        window.dataPromise = Promise.resolve(" + payload + ")\n",
    "inline data: ฝังลงในสคริปต์ที่ใช้โดยตรง",
)


# ── 3) preload รูปโปรไฟล์ (เป็น LCP ของหน้า) ───────────────────────
pic = (cv.get("profile") or {}).get("pic_src") or ""
if pic.startswith("/media/"):
    srcset = (cv.get("profile") or {}).get("pic_srcset") or ""
    tag = f'    <link rel="preload" as="image" href="{pic}"'
    if srcset:
        tag += f' imagesrcset="{srcset}" imagesizes="(min-width: 768px) 280px, 224px"'
    tag += ' fetchpriority="high">\n'
    html = html.replace("</head>", tag + "</head>", 1)
    applied.append(f"preload รูปโปรไฟล์ ({pic})")


# ── 4) tsparticles: ข้ามไปถ้าเน็ตช้า/เปิด Save-Data ────────────────
replace_once(
    '    <script defer src="https://cdn.jsdelivr.net/npm/tsparticles-slim@2.12.0/tsparticles.slim.bundle.min.js"></script>\n',
    '    <script>\n'
    '        // ประกายพื้นหลังหนัก 38.7 KB และไม่ใช่เนื้อหา\n'
    '        // เน็ตช้าหรือผู้ใช้เปิดโหมดประหยัดเน็ตก็ข้ามไปเลย หน้าเว็บยังสวยเหมือนเดิม\n'
    '        (function () {\n'
    '            var c = navigator.connection || {};\n'
    '            var slow = c.saveData === true || /(^|-)(2g|slow-2g)$/.test(c.effectiveType || "");\n'
    '            if (slow || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;\n'
    '            var s = document.createElement("script");\n'
    '            s.src = "https://cdn.jsdelivr.net/npm/tsparticles-slim@2.12.0/tsparticles.slim.bundle.min.js";\n'
    '            s.defer = true;\n'
    '            document.head.appendChild(s);\n'
    '        })();\n'
    '    </script>\n',
    "tsparticles: โหลดเฉพาะเมื่อเน็ตไหว",
)

io.open(index_path, "w", encoding="utf-8").write(html)

size_kb = len(html.encode("utf-8")) / 1024
print(f"   optimize: {len(applied)} รายการ, index.html = {size_kb:.1f} KB")
for a in applied:
    print(f"     - {a}")
