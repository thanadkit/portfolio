# Portfolio — Cloudflare Pages

เวอร์ชันสำหรับ Cloudflare Pages แยกจากตัวที่รันบน Synology NAS
(`../Portfolio` ยังรันต่อได้ตามปกติ ไม่กระทบกัน)

## หลักการ

ไม่มี cron ไม่มี GitHub Actions — **deploy เฉพาะตอนแก้ข้อมูลจริง**

```
แก้ Google Sheet
   └─ Apps Script trigger (รวบการแก้ทั้งรอบเป็นครั้งเดียว หน่วง 5 นาที)
        └─ POST → Cloudflare Deploy Hook
             └─ Pages build
                  ├─ pip install -r requirements.txt
                  ├─ python updater/update.py --once   ดึงข้อมูล + โหลดรูป + ย่อ + สร้าง og-image
                  └─ bash build.sh                     รวม public/ + data/ → dist/
             └─ deploy (~2 นาที)
```

Pages ฟรีให้ **500 builds/เดือน** การ deploy เฉพาะตอนแก้ Sheet จึงเหลือเฟือ
(ถ้า deploy ทุกชั่วโมงจะเป็น 720 ครั้ง — เกินโควตา)

ผลพลอยได้: ถ้า Apps Script ล่มตอน build → build fail → เว็บเดิมยังอยู่ ไม่มีทางได้หน้าเปล่า

## โครงสร้าง

```
Portfolio-cf/
├── public/                 ไฟล์คงที่ (index.html ต้นฉบับ, favicon, manifest, _headers)
├── updater/                ตัวดึงข้อมูล (ใช้ร่วมกับฝั่ง Docker ได้ โค้ดเดียวกัน)
├── build.sh                รวม public/ + data/ → dist/
├── requirements.txt
├── .python-version
├── apps-script-deploy-trigger.gs
└── .gitignore              data/ กับ dist/ ไม่ต้องเข้า git สร้างใหม่ทุก build
```

## ตั้งค่า Cloudflare Pages

1. ดันโฟลเดอร์นี้ขึ้น GitHub repo
2. Cloudflare Dashboard → Workers & Pages → Create → Pages → Connect to Git → เลือก repo
3. ตั้งค่า build:

   | ช่อง | ค่า |
   |---|---|
   | Build command | `npm ci && pip install -r requirements.txt && SITE_URL= python updater/update.py --once --strict && bash build.sh` |
   | Build output directory | `dist` |
   | Root directory | เว้นว่าง |

4. Settings → Environment variables (Production):

   | ตัวแปร | ค่า |
   |---|---|
   | `API_URL` | URL ของ Apps Script `/exec` |
   | `SITE_URL` | URL จริงของเว็บ เช่น `https://portfolio.pages.dev` |
   | `DATA_DIR` | `data` |
   | `PUBLIC_DIR` | `public` |

   `SITE_URL` จำเป็น — ถ้าไม่ตั้ง build จะ fail พร้อมบอกสาเหตุ
   เพราะ `og:image` ต้องเป็น URL เต็ม ไม่งั้น LINE กับ Facebook ไม่โชว์ thumbnail

   `--strict` ทำให้ build พังถ้ารูปโหลดไม่สำเร็จเกิน 20% — ดีกว่าปล่อย deploy
   ที่เว็บต้องกลับไปดึงรูปจาก postimg ตอนผู้ใช้เปิด (ช้าและพังง่าย) ของเดิมจะถูกคงไว้แทน

   สังเกต `SITE_URL=` ที่คร่อมหน้าคำสั่ง updater ใน build command — ตั้งใจล้างค่าให้ว่างเฉพาะคำสั่งนั้น
   เพื่อให้ updater ปล่อย placeholder ไว้ แล้วให้ `build.sh` เป็นเจ้าของการเติมโดเมนเพียงที่เดียว
   ถ้าปล่อยให้ updater เติมเอง เวลาเปลี่ยนโดเมนจะได้ URL เก่าติดมาโดยไม่มีใครจับได้
   (`build.sh` มีด่านตรวจจับกรณีนี้ไว้แล้ว จะ fail พร้อมบอกวิธีแก้)

5. Deploy ครั้งแรก แล้วค่อยกลับมาแก้ `SITE_URL` ให้ตรงโดเมนจริงถ้าใช้ custom domain

## ตั้งค่า deploy อัตโนมัติ

ดูขั้นตอนในหัวไฟล์ [apps-script-deploy-trigger.gs](apps-script-deploy-trigger.gs)
สรุปสั้น ๆ: สร้าง Deploy Hook ใน Pages → เก็บ URL ไว้ใน Apps Script → ตั้ง trigger 2 ตัว
(`onSheetChanged` แบบ On change และ `flushDeploy` แบบ Every 10 minutes)

หลังติดตั้งจะมีเมนู **Portfolio** โผล่ในแถบเครื่องมือของ Sheet กด "Deploy เว็บตอนนี้" ได้เอง
เผื่อเปลี่ยนรูปที่ postimg โดยไม่ได้แตะ Sheet

## รันทดสอบในเครื่อง

```bash
cd "/Users/thanadkit/Documents/VS Code/Portfolio-cf" && \
  API_URL="$(grep -o 'https://script.google.com[^"]*exec' ../Portfolio/docker-compose.yml | head -1)" \
  DATA_DIR="$PWD/data" PUBLIC_DIR="$PWD/public" SITE_URL="http://localhost:22432" \
  python3 updater/update.py --once && bash build.sh && \
  python3 -m http.server 22432 --directory dist
```

ต้องมี `requests` กับ `Pillow` ก่อน: `python3 -m pip install -r requirements.txt`

## ทำให้โหลดเร็วสำหรับคนที่เปิดครั้งแรก

`build.sh` เรียก `optimize.py` มาแก้ `dist/index.html` หลัง build
(ไม่แตะ `public/index.html` ไฟล์ต้นฉบับจึงยังเหมือนฝั่ง Docker เป๊ะ `sync-from-docker.sh` ใช้ได้ต่อ)

| | ก่อน | หลัง |
|---|---|---|
| Tailwind | Play CDN 120 KB บีบแล้ว (ดิบ 398 KB) บล็อก render + คอมไพล์ CSS ในเบราว์เซอร์ | `tailwind.css` ที่ build ไว้ **4.7 KB** ไม่บล็อกอะไร |
| ข้อมูล | ยิงขอ `/api/cv` อีก 1 round trip หลัง parse HTML | ฝังมาใน HTML แล้ว ไม่ต้องขอ |
| รูปแรก (LCP) | รอจนเจอ tag ตอน parse | `<link rel="preload">` ตั้งแต่ใน head |
| tsParticles | โหลดเสมอ 38.7 KB | ข้ามถ้าเน็ตช้า / เปิด Save-Data / ตั้ง reduce-motion |
| **รวม** | **~223 KB, 4 origin** | **~108 KB, 2 origin** |

### ทำไมไม่เอาฟอนต์มาเสิร์ฟเอง

ลองแล้ว ได้ 761 KB เพราะ Google ส่ง variable font มาทุก subset ทุกน้ำหนัก
ขณะที่ถ้าปล่อยให้เบราว์เซอร์คุยกับ Google เอง มันเลือกโหลดเฉพาะชุดที่ใช้จริง (~30 KB)
จะคุ้มก็ต่อเมื่อทำ subset ตามตัวอักษรที่ใช้จริงด้วย `pyftsubset` ซึ่งซับซ้อนและเสี่ยงตัวอักษรหาย
ตอนนี้ใช้ `preconnect` ซึ่งกินประโยชน์ส่วนใหญ่ไปแล้ว และฟอนต์ไม่ได้บล็อก render อยู่แล้ว (`display=swap`)

### ชื่อไฟล์รูปมี hash ของเนื้อไฟล์

`<key>-<ขนาด>-<hash 8 ตัว>.webp` โดย key มาจาก URL ต้นทาง ส่วน hash มาจากเนื้อไฟล์ที่ย่อแล้ว
ทำให้ `_headers` ตั้ง `immutable` ให้ `/media/*` ได้อย่างปลอดภัย — เนื้อหาเปลี่ยนเมื่อไหร่ชื่อเปลี่ยนตาม
ถ้าผูกกับ URL อย่างเดียว แล้ววันหนึ่งรูปที่ URL เดิมถูกสลับไฟล์ (เกิดได้กับ Google Drive)
คนที่เคยเข้าเว็บจะค้างรูปเก่าไว้เป็นปีโดยไม่มีทางรู้

### ข้อควรระวัง

`tailwind.config.js` ต้องตรงกับบล็อก `tailwind.config` ที่ฝังอยู่ใน `public/index.html`
ถ้าเพิ่มสีหรือฟอนต์ที่หนึ่งแล้วลืมอีกที่ จะเพี้ยนเฉพาะเวอร์ชันที่ build
และ Tailwind สแกนหา class จาก `public/index.html` เท่านั้น — ถ้าประกอบชื่อ class ด้วยตัวแปรใน JS (เช่น `'md:order-' + n`) มันจะหาไม่เจอแล้ว class นั้นจะหายไป

## ต่างจากฝั่ง Docker ตรงไหน

| | Docker / NAS | Cloudflare Pages |
|---|---|---|
| เสิร์ฟไฟล์ | nginx | Pages CDN ทั่วโลก |
| `/api/cv` | nginx เสิร์ฟจาก `/data/cv.json` | `build.sh` ก๊อปไฟล์ไปไว้ที่ path นั้นเลย |
| เติม `og:image` ให้เป็น URL เต็ม | nginx `sub_filter` ตอนเสิร์ฟ | `build.sh` เติมตอน build จาก `SITE_URL` |
| ดึงข้อมูลใหม่ | container รันทุก 1 ชม. | ตอน build ซึ่งเกิดเมื่อแก้ Sheet |
| ข้ามรูปที่ไม่เปลี่ยน | ได้ (manifest อยู่บนดิสก์) | ไม่ได้ ทุก build โหลดใหม่หมด (~40 วิ) |
| ทางถอยเมื่อไม่มีข้อมูล | nginx proxy ไป Apps Script | เบราว์เซอร์ยิงตรงเอง (Apps Script ส่ง CORS `*`) |

## ของที่ยังใช้ร่วมกัน

`public/index.html` กับ `updater/` เป็นสำเนาจากฝั่ง Docker **แก้ที่ไหนต้องก๊อปไปอีกที่ด้วย**
ถ้าเริ่มลืมบ่อย ค่อยยุบเป็นโฟลเดอร์เดียวกันแล้วแยกแค่ไฟล์ config ก็ได้ (ไฟล์ Docker กับ Pages อยู่ร่วมกันได้ ไม่ชนกัน)
