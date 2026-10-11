#!/usr/bin/env bash
# ดึง public/ กับ updater/ จากฝั่ง Docker มาทับ เพื่อไม่ให้สองที่หลุดจากกัน
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$HERE/../Portfolio" && pwd)"

echo "ก๊อปจาก: $SRC"
rsync -a --delete "$SRC/public/" "$HERE/public/" --exclude '_headers'
rsync -a --delete "$SRC/updater/" "$HERE/updater/" --exclude 'Dockerfile'
echo "เสร็จ — public/ กับ updater/ ตรงกับฝั่ง Docker แล้ว (_headers กับ Dockerfile ไม่ถูกแตะ)"
