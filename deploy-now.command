#!/usr/bin/env bash
# ดับเบิลคลิกไฟล์นี้เพื่อสั่งให้เว็บอัปเดตเดี๋ยวนี้
cd "$(dirname "$0")"
HOOK=$(cat DEPLOY_HOOK.local.txt 2>/dev/null)
if [ -z "$HOOK" ]; then echo "ไม่พบ DEPLOY_HOOK.local.txt"; read -n1; exit 1; fi
echo "กำลังสั่ง deploy ..."
code=$(curl -s -o /tmp/dep -w '%{http_code}' -X POST "$HOOK")
if [ "$code" = "200" ]; then
  echo "สั่งแล้ว อีกประมาณ 3 นาทีเว็บจะอัปเดต"
  echo "ดูสถานะ: https://dash.cloudflare.com/?to=/:account/pages/view/portfolio-thanadkit"
else
  echo "ไม่สำเร็จ (HTTP $code)"; cat /tmp/dep
fi
echo; echo "กดปุ่มใดก็ได้เพื่อปิด"; read -n1
