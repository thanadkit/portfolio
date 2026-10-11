#!/usr/bin/env bash
# รวม public/ (ไฟล์คงที่) กับ data/ (ของที่ updater เพิ่งสร้าง) เป็น dist/ ให้ Cloudflare Pages
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

DIST=dist
rm -rf "$DIST"
mkdir -p "$DIST"

# 1) ไฟล์คงที่ (index.html ตัวต้นฉบับ, favicon, manifest)
cp -R public/. "$DIST"/

# 2) ของที่ updater สร้าง — index.html ตัวนี้มี meta tags แล้ว จึงต้องทับของเดิม
if [ ! -f data/cv.json ]; then
  echo "ERROR: ไม่พบ data/cv.json — updater ยังไม่ได้รัน หรือรันไม่สำเร็จ" >&2
  exit 1
fi
cp -R data/. "$DIST"/

# 3) หน้าเว็บเรียกข้อมูลที่ /api/cv แต่ Pages ไม่มี nginx มาเขียน path ให้
#    ก๊อปเป็นไฟล์ที่ path นั้นตรง ๆ เลย ง่ายและไม่ต้องพึ่งกติกา redirect ของ host
mkdir -p "$DIST/api"
cp data/cv.json "$DIST/api/cv"

# 4) ไม่มี nginx มาแทน __BASE__ ให้ ต้องเติมตอน build
#    og:image / og:url ต้องเป็น URL เต็ม ไม่งั้น LINE กับ Facebook ไม่โชว์ thumbnail
#
#    สำคัญ: ต้องรัน updater โดย "ไม่ตั้ง" SITE_URL (ดู build command ใน README)
#    เพื่อให้ updater ปล่อย __BASE__ ไว้แล้วให้ไฟล์นี้เป็นเจ้าของการเติมเพียงที่เดียว
#    ถ้าปล่อยให้ updater เติมเอง เวลาเปลี่ยนโดเมนแล้ว data/ ยังเก่า จะได้ URL ผิดโดยไม่มีใครจับได้
if [ -z "${SITE_URL:-}" ]; then
  echo "ERROR: ต้องตั้ง SITE_URL (เช่น https://portfolio.pages.dev)" >&2
  echo "       ตั้งที่ Cloudflare Pages -> Settings -> Environment variables" >&2
  exit 1
fi
BASE="${SITE_URL%/}"
# ใช้ | เป็นตัวคั่นเพราะ URL มี /
find "$DIST" -name '*.html' -exec sed -i.bak "s|__BASE__|${BASE}|g" {} \;
find "$DIST" -name '*.bak' -delete

# ── ตรวจผลจริง ไม่ใช่แค่เช็กว่า placeholder หายไป ──
if grep -rq "__BASE__" "$DIST" 2>/dev/null; then
  echo "ERROR: ยังมี __BASE__ หลงเหลือใน dist/" >&2
  exit 1
fi

OG=$(grep -o 'property="og:image" content="[^"]*"' "$DIST/index.html" | head -1 | sed 's/.*content="//; s/"$//')
case "$OG" in
  "$BASE"/*) echo "   base URL: ${BASE}" ;;
  http*)
    echo "ERROR: og:image ชี้ไป '$OG' ซึ่งไม่ตรงกับ SITE_URL '$BASE'" >&2
    echo "       แปลว่า data/index.html ถูกสร้างโดยตั้ง SITE_URL ไว้ด้วย" >&2
    echo "       แก้ build command ให้เป็น: SITE_URL= python updater/update.py --once && bash build.sh" >&2
    exit 1 ;;
  *)
    echo "ERROR: og:image ไม่ใช่ URL เต็ม ('$OG') LINE/Facebook จะไม่โชว์ thumbnail" >&2
    exit 1 ;;
esac

# 5) build CSS ของ Tailwind ล่วงหน้า แทนที่จะให้เบราว์เซอร์คอมไพล์เองตอนเปิดหน้า
if [ ! -x node_modules/.bin/tailwindcss ]; then
  echo "ERROR: ไม่พบ tailwindcss — ต้องรัน npm ci ก่อน (ดู build command ใน README)" >&2
  exit 1
fi
node_modules/.bin/tailwindcss -c tailwind.config.js -i src/input.css -o "$DIST/tailwind.css" --minify 2>&1 | tail -1

# 6) ปรับ HTML: สลับไปใช้ CSS ที่ build แล้ว, ฝังข้อมูล, preload รูปแรก
python3 optimize.py "$DIST"


echo "build เสร็จ: $(find "$DIST" -type f | wc -l | tr -d ' ') ไฟล์, $(du -sh "$DIST" | cut -f1)"
