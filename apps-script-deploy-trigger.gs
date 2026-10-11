/**
 * ตัวสั่ง deploy เว็บอัตโนมัติเมื่อแก้ Google Sheet
 * เอาไปวางต่อท้ายโค้ดเดิมในหน้า Apps Script Editor
 *
 * ── ติดตั้งครั้งเดียว ───────────────────────────────────────
 * 1. Cloudflare Pages -> Settings -> Builds & deployments -> Deploy hooks
 *    สร้าง hook ใหม่ แล้วก๊อป URL มา
 * 2. กลับมาที่ Apps Script เลือกฟังก์ชัน setDeployHook แล้วกด Run
 *    (แก้ URL ในฟังก์ชันให้เป็นของคุณก่อน)
 * 3. เมนู Triggers (รูปนาฬิกา) ทางซ้าย -> Add Trigger สองอัน:
 *      a) onSheetChanged  | From spreadsheet | On change
 *      b) flushDeploy     | Time-driven      | Minutes timer | Every 10 minutes
 *
 * ทำไมต้องสองอัน: การพิมพ์ใน Sheet ยิง event รัวมาก ถ้า deploy ทุก event
 * จะเปลือง build quota ฟรี (500 ครั้ง/เดือน) ตัวแรกแค่ปักธงว่า "มีของใหม่"
 * ตัวที่สองมาเก็บกวาดทีหลังเมื่อหยุดพิมพ์แล้ว รวบการแก้ทั้งรอบเป็น deploy ครั้งเดียว
 */

const PROP_HOOK    = 'CF_DEPLOY_HOOK';
const PROP_PENDING = 'DEPLOY_PENDING_AT';
const QUIET_MINUTES = 5;    // หยุดแก้นานเท่านี้ถึงจะ deploy

function setDeployHook() {
  const url = 'https://api.cloudflare.com/client/v4/pages/webhooks/deploy_hooks/XXXXXXXX';  // <-- แก้ตรงนี้
  if (url.indexOf('XXXX') !== -1) {
    throw new Error('ยังไม่ได้ใส่ Deploy Hook URL จริง');
  }
  PropertiesService.getScriptProperties().setProperty(PROP_HOOK, url);
  Logger.log('เก็บ Deploy Hook เรียบร้อย');
}

/** ติดตั้งเป็น trigger แบบ "From spreadsheet -> On change" */
function onSheetChanged(e) {
  // ปักธงไว้เฉย ๆ ยังไม่ deploy เพราะการแก้หนึ่งรอบยิง event มาหลายครั้ง
  PropertiesService.getScriptProperties()
    .setProperty(PROP_PENDING, String(Date.now()));
}

/** ติดตั้งเป็น trigger แบบ "Time-driven -> Every 10 minutes" */
function flushDeploy() {
  const props = PropertiesService.getScriptProperties();
  const pendingAt = Number(props.getProperty(PROP_PENDING) || 0);
  if (!pendingAt) return;                                   // ไม่มีอะไรเปลี่ยน

  const quietMs = Date.now() - pendingAt;
  if (quietMs < QUIET_MINUTES * 60 * 1000) return;          // ยังแก้อยู่ รอก่อน

  props.deleteProperty(PROP_PENDING);
  triggerDeploy('มีการแก้ข้อมูลใน Sheet');
}

/** เรียกเองได้จากเมนู เผื่อเปลี่ยนรูปที่ postimg โดยไม่ได้แตะ Sheet */
function deployNow() {
  PropertiesService.getScriptProperties().deleteProperty(PROP_PENDING);
  const ok = triggerDeploy('สั่งเองจากเมนู');
  SpreadsheetApp.getActive().toast(
    ok ? 'สั่ง deploy แล้ว อีกประมาณ 2 นาทีเว็บจะอัปเดต' : 'สั่ง deploy ไม่สำเร็จ ดู Executions',
    'Portfolio', 5);
}

function triggerDeploy(reason) {
  const hook = PropertiesService.getScriptProperties().getProperty(PROP_HOOK);
  if (!hook) {
    Logger.log('ยังไม่ได้ตั้ง Deploy Hook — รันฟังก์ชัน setDeployHook ก่อน');
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
    Logger.log('deploy (' + reason + ') -> HTTP ' + code);
    return code >= 200 && code < 300;
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
    .addItem('ดูสถานะการตั้งค่า', 'showDeployStatus')
    .addToUi();
}

function showDeployStatus() {
  const props = PropertiesService.getScriptProperties();
  const hook = props.getProperty(PROP_HOOK);
  const pendingAt = Number(props.getProperty(PROP_PENDING) || 0);
  SpreadsheetApp.getUi().alert(
    'Portfolio – สถานะ',
    'Deploy Hook: ' + (hook ? 'ตั้งแล้ว' : 'ยังไม่ได้ตั้ง (รัน setDeployHook)') + '\n' +
    'รอ deploy อยู่: ' + (pendingAt ? new Date(pendingAt).toLocaleString('th-TH') : 'ไม่มี') + '\n' +
    'หน่วงก่อน deploy: ' + QUIET_MINUTES + ' นาทีหลังหยุดแก้',
    SpreadsheetApp.getUi().ButtonSet.OK);
}
