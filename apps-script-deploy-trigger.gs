/**
 * สั่งให้เว็บบน Cloudflare Pages อัปเดตอัตโนมัติเมื่อข้อมูลใน Sheet เปลี่ยน
 * เอาไปวางต่อท้ายโค้ดเดิมในหน้า Apps Script Editor
 *
 * ── หลักการ ─────────────────────────────────────────────────
 * Cloudflare Pages ฟรีให้ build เดือนละ 500 ครั้ง
 * ถ้าสั่ง deploy ทุกชั่วโมงจะเป็น 720 ครั้ง/เดือน — เกินโควตา
 * และส่วนใหญ่ก็ build ทิ้งเปล่าเพราะข้อมูลไม่ได้เปลี่ยน
 *
 * ตัวนี้จึงแยกสองเรื่องออกจากกัน:
 *   เช็กว่าข้อมูลเปลี่ยนไหม  -> ทุก 15 นาที (ฟรี ไม่กินโควตา Cloudflare)
 *   สั่ง build                -> เฉพาะตอนข้อมูลต่างจากเดิมจริง
 * เท่ากับจำนวน build ต่อเดือน = จำนวนครั้งที่แก้ Sheet จริง ๆ
 *
 * ── ติดตั้งครั้งเดียว ───────────────────────────────────────
 * 1. แก้ DEPLOY_HOOK ด้านล่างให้เป็น URL จริง
 *    (อยู่ในไฟล์ DEPLOY_HOOK.local.txt ในโฟลเดอร์โปรเจกต์)
 * 2. เลือกฟังก์ชัน initDeploy แล้วกด Run หนึ่งครั้ง
 * 3. เมนู Triggers (รูปนาฬิกา) -> Add Trigger
 *      Function        : checkAndDeploy
 *      Event source    : Time-driven
 *      Type            : Minutes timer
 *      Interval        : Every 15 minutes
 *    ตัวเดียวพอ ไม่ต้องตั้ง onChange
 */

const DEPLOY_HOOK = 'PASTE_DEPLOY_HOOK_URL_HERE';

const PROP_HOOK = 'CF_DEPLOY_HOOK';
const PROP_SIG  = 'DATA_SIGNATURE';
// ชีตที่มีผลต่อหน้าเว็บ ถ้าเพิ่มชีตใหม่ต้องใส่ชื่อตรงนี้ด้วย
const WATCHED_SHEETS = ['profile', 'experience', 'skills', 'services', 'portfolio'];


/** รันครั้งเดียวตอนติดตั้ง: เก็บ hook แล้วจำสภาพข้อมูลปัจจุบันไว้ */
function initDeploy() {
  if (DEPLOY_HOOK.indexOf('PASTE_') === 0) {
    throw new Error('ยังไม่ได้ใส่ Deploy Hook URL จริงในตัวแปร DEPLOY_HOOK');
  }
  const props = PropertiesService.getScriptProperties();
  props.setProperty(PROP_HOOK, DEPLOY_HOOK);
  // จำสภาพปัจจุบันไว้ก่อน จะได้ไม่ deploy รอบแรกทั้งที่เว็บยังตรงอยู่แล้ว
  props.setProperty(PROP_SIG, currentSignature());
  Logger.log('ติดตั้งเรียบร้อย — ตั้ง trigger checkAndDeploy แบบ Every 15 minutes ต่อได้เลย');
}


/** ติดตั้งเป็น trigger แบบ Time-driven -> Every 15 minutes */
function checkAndDeploy() {
  const props = PropertiesService.getScriptProperties();
  const now = currentSignature();
  const before = props.getProperty(PROP_SIG);

  if (now === before) return;           // ข้อมูลเหมือนเดิม ไม่ต้องทำอะไร

  props.setProperty(PROP_SIG, now);     // เก็บก่อนยิง กัน deploy ซ้ำถ้า hook ล้ม
  triggerDeploy('ข้อมูลใน Sheet เปลี่ยน');
}


/**
 * ย่อข้อมูลทุกชีตที่เกี่ยวข้องให้เหลือ hash สั้น ๆ
 * อ่านจาก Sheet ตรง ๆ ไม่ผ่าน API จึงเร็วและไม่โดนแคชของ Apps Script หลอก
 */
function currentSignature() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const parts = [];

  WATCHED_SHEETS.forEach(function (name) {
    const sh = ss.getSheetByName(name);
    if (!sh) { parts.push(name + ':ไม่มีชีต'); return; }
    const lastRow = sh.getLastRow();
    const lastCol = sh.getLastColumn();
    if (lastRow < 1 || lastCol < 1) { parts.push(name + ':ว่าง'); return; }
    parts.push(name + ':' + sh.getRange(1, 1, lastRow, lastCol).getValues().join('\u0001'));
  });

  const raw = parts.join('\u0002');
  const digest = Utilities.computeDigest(Utilities.DigestAlgorithm.MD5, raw, Utilities.Charset.UTF_8);
  return digest.map(function (b) { return ('0' + (b & 0xFF).toString(16)).slice(-2); }).join('');
}


/** เรียกเองได้จากเมนู เผื่อเปลี่ยนรูปที่ postimg โดยไม่ได้แตะ Sheet */
function deployNow() {
  const ok = triggerDeploy('สั่งเองจากเมนู');
  PropertiesService.getScriptProperties().setProperty(PROP_SIG, currentSignature());
  SpreadsheetApp.getActive().toast(
    ok ? 'สั่ง deploy แล้ว อีกประมาณ 3 นาทีเว็บจะอัปเดต' : 'สั่งไม่สำเร็จ ดูที่ Executions',
    'Portfolio', 5);
}


function triggerDeploy(reason) {
  const hook = PropertiesService.getScriptProperties().getProperty(PROP_HOOK);
  if (!hook) {
    Logger.log('ยังไม่ได้ตั้ง Deploy Hook — รันฟังก์ชัน initDeploy ก่อน');
    return false;
  }

  // ล้างแคชฝั่ง Apps Script ก่อน ไม่งั้น build อาจไปหยิบข้อมูลเก่าที่ค้างอยู่
  try { CacheService.getScriptCache().remove('cv_payload_v1'); } catch (err) {}

  try {
    const res = UrlFetchApp.fetch(hook, {
      method: 'post',
      contentType: 'application/json',
      payload: '{}',
      muteHttpExceptions: true
    });
    const code = res.getResponseCode();
    // 200 = รับคิวแล้ว, 304 = มี build คิวอยู่แล้วเลยไม่สั่งซ้ำ (ถือว่าสำเร็จทั้งคู่)
    const ok = (code >= 200 && code < 300) || code === 304;
    Logger.log('deploy (' + reason + ') -> HTTP ' + code + (ok ? ' สำเร็จ' : ' ล้มเหลว'));
    return ok;
  } catch (err) {
    Logger.log('deploy ไม่สำเร็จ: ' + err);
    return false;
  }
}


/** เมนูในแถบเครื่องมือของ Sheet */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Portfolio')
    .addItem('Deploy เว็บตอนนี้', 'deployNow')
    .addSeparator()
    .addItem('ดูสถานะ', 'showDeployStatus')
    .addToUi();
}


function showDeployStatus() {
  const props = PropertiesService.getScriptProperties();
  const hasHook = !!props.getProperty(PROP_HOOK);
  const same = props.getProperty(PROP_SIG) === currentSignature();
  SpreadsheetApp.getUi().alert(
    'Portfolio – สถานะ',
    'Deploy Hook : ' + (hasHook ? 'ตั้งแล้ว' : 'ยังไม่ได้ตั้ง (รัน initDeploy)') + '\n' +
    'ข้อมูลตอนนี้ : ' + (same ? 'ตรงกับที่เว็บเสิร์ฟอยู่' : 'เปลี่ยนแล้ว รอ trigger รอบถัดไป') + '\n\n' +
    'ตัวเช็กทำงานทุก 15 นาที และจะสั่ง build ก็ต่อเมื่อข้อมูลเปลี่ยนจริง',
    SpreadsheetApp.getUi().ButtonSet.OK);
}
