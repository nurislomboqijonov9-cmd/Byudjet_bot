import express from 'express';
import path from 'path';
import fs from 'fs';
import crypto from 'crypto';
import { fileURLToPath } from 'url';
import TelegramBot from 'node-telegram-bot-api';
import ExcelJS from 'exceljs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/* ================= BAZA (oddiy JSON fayl, kompilyatsiyasiz) ================= */
const DATA_DIR = process.env.DATA_DIR || path.join(process.cwd(), 'data');
if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
const DB_FILE = path.join(DATA_DIR, 'temirchi.json');
const DB_FILE_2026 = path.join(DATA_DIR, 'temirchi_2026.json');

function emptyDB() {
  return {
    seq: 1, materials: [], batches: [], products: [], recipe: [],
    productions: [], consumptions: [], customers: [], customer_items: [],
    customer_payments: [], debts: [], debt_payments: [], kassa: [],
    settings: { kurs: 0, kassa_start: 0 }, allowed: [], pending: []
  };
}
function ensureShape(db) {
  if (!Array.isArray(db.allowed)) db.allowed = [];
  if (!Array.isArray(db.pending)) db.pending = [];
  if (!Array.isArray(db.materials)) db.materials = [];
  if (!Array.isArray(db.batches)) db.batches = [];
  if (!Array.isArray(db.products)) db.products = [];
  if (!Array.isArray(db.recipe)) db.recipe = [];
  if (!Array.isArray(db.productions)) db.productions = [];
  if (!Array.isArray(db.consumptions)) db.consumptions = [];
  if (!Array.isArray(db.customers)) db.customers = [];
  if (!Array.isArray(db.customer_items)) db.customer_items = [];
  if (!Array.isArray(db.customer_payments)) db.customer_payments = [];
  if (!Array.isArray(db.debts)) db.debts = [];
  if (!Array.isArray(db.debt_payments)) db.debt_payments = [];
  if (!Array.isArray(db.kassa)) db.kassa = [];
  if (!Array.isArray(db.husan)) db.husan = []; // Husan aka mini-kassa: {id, kind, amount, currency, note, created_at}
  if (!db.kassa_cats || typeof db.kassa_cats !== 'object') db.kassa_cats = { in: [], out: [] };
  if (!Array.isArray(db.kassa_cats.in)) db.kassa_cats.in = [];
  if (!Array.isArray(db.kassa_cats.out)) db.kassa_cats.out = [];
  if (!db.settings || typeof db.settings !== 'object') db.settings = { kurs: 0, kassa_start: 0 };
  if (db.settings.kassa_start == null) db.settings.kassa_start = 0;
  if (!db.seq) db.seq = 1;
  return db;
}

// Ikki baza xotirada
let DB_MAIN = emptyDB();
let DB_2026 = emptyDB();
// Joriy DB — har so'rovda almashadi (default: asosiy)
let DB = DB_MAIN;
let CUR_FILE = DB_FILE;

function loadOne(file, fallback) {
  try {
    if (fs.existsSync(file)) return ensureShape(JSON.parse(fs.readFileSync(file, 'utf8')));
  } catch (e) { console.error('Baza o\'qishda xato (' + file + '):', e.message); }
  return ensureShape(fallback);
}
function loadDB() {
  DB_MAIN = loadOne(DB_FILE, emptyDB());
  DB_2026 = loadOne(DB_FILE_2026, emptyDB());
  DB = DB_MAIN; CUR_FILE = DB_FILE;
}
// So'rov uchun bazani tanlash (which: 'main' yoki '2026')
function useDB(which) {
  if (which === '2026') { DB = DB_2026; CUR_FILE = DB_FILE_2026; }
  else { DB = DB_MAIN; CUR_FILE = DB_FILE; }
}
let saveTimer = null;
function saveDB() {
  const file = CUR_FILE, snapshot = DB;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try { fs.writeFileSync(file, JSON.stringify(snapshot)); }
    catch (e) { console.error('Baza saqlashda xato:', e.message); }
  }, 100);
}
function saveNow() {
  clearTimeout(saveTimer);
  try { fs.writeFileSync(CUR_FILE, JSON.stringify(DB)); } catch (e) { console.error(e.message); }
}
// Bot uchun — doim asosiy bazani saqlaydi
function saveMain() {
  try { fs.writeFileSync(DB_FILE, JSON.stringify(DB_MAIN)); } catch (e) { console.error(e.message); }
}
loadDB();

const nextId = () => DB.seq++;
const nowISO = () => new Date().toISOString();
const materialStock = (id) => {
  const i = DB.batches.filter(b => b.material_id === id).reduce((s, b) => s + (+b.qty), 0);
  const o = DB.consumptions.filter(c => c.material_id === id).reduce((s, c) => s + (+c.qty), 0);
  return i - o;
};
const materialAvgPrice = (id) => {
  const bs = DB.batches.filter(b => b.material_id === id);
  const q = bs.reduce((s, b) => s + (+b.qty), 0);
  if (!q) return 0;
  return bs.reduce((s, b) => s + (+b.qty) * (+b.price), 0) / q;
};

/* ================= TELEGRAM ================= */
function verifyInitData(initData, botToken) {
  try {
    const params = new URLSearchParams(initData);
    const hash = params.get('hash');
    if (!hash) return null;
    params.delete('hash');
    const dataCheck = [...params.entries()].sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => `${k}=${v}`).join('\n');
    const secret = crypto.createHmac('sha256', 'WebAppData').update(botToken).digest();
    const calc = crypto.createHmac('sha256', secret).update(dataCheck).digest('hex');
    if (calc !== hash) return null;
    const u = params.get('user');
    return { user: u ? JSON.parse(u) : null };
  } catch { return null; }
}

let botInstance = null;
function initBot(token) {
  const bot = new TelegramBot(token, { polling: true });
  botInstance = bot;
  const WEBAPP_URL = process.env.WEBAPP_URL || '';
  const OWNER_ID = String(process.env.OWNER_ID || '').trim();

  // Ruxsat bormi? (ega yoki tasdiqlangan ro'yxatda)
  const isAllowed = (id) => String(id) === OWNER_ID || DB_MAIN.allowed.includes(String(id));

  // Ilovani ochish tugmasi
  const openKb = WEBAPP_URL
    ? { reply_markup: { inline_keyboard: [[{ text: '⚒️ Omborni ochish', web_app: { url: WEBAPP_URL } }]] } }
    : {};

  bot.onText(/\/start/, (msg) => {
    const id = String(msg.chat.id);
    const name = [msg.from.first_name, msg.from.last_name].filter(Boolean).join(' ') || msg.from.username || id;

    if (isAllowed(id)) {
      bot.sendMessage(msg.chat.id,
        `⚒️ <b>TEMIRCHI — Ombor va Norma</b>\n\nSalom, ${name}!\nPastdagi tugmani bosing.`,
        { parse_mode: 'HTML', ...openKb });
      return;
    }

    // Ruxsat yo'q — egaga so'rov yuboramiz
    if (!OWNER_ID) {
      bot.sendMessage(msg.chat.id, 'Tizim hali sozlanmagan (ega belgilanmagan). Keyinroq urinib ko\'ring.');
      return;
    }

    // pending ro'yxatga qo'shamiz (takror bo'lmasin)
    if (!DB_MAIN.pending.find(p => p.id === id)) {
      DB_MAIN.pending.push({ id, name, at: nowISO() });
      saveMain();
    }

    bot.sendMessage(msg.chat.id,
      `Salom, ${name}!\nKirish uchun egadan ruxsat so'raldi. Tasdiqlangach, sizga xabar keladi. ⏳`);

    // Egaga tasdiqlash so'rovi (tugmalar bilan)
    bot.sendMessage(OWNER_ID,
      `🔔 <b>Yangi kirish so'rovi</b>\n\nIsm: ${name}\nUsername: ${msg.from.username ? '@' + msg.from.username : '—'}\nID: <code>${id}</code>\n\nRuxsat berasizmi?`,
      {
        parse_mode: 'HTML',
        reply_markup: {
          inline_keyboard: [[
            { text: '✅ Ruxsat', callback_data: `ok:${id}` },
            { text: '⛔ Rad etish', callback_data: `no:${id}` }
          ]]
        }
      });
  });

  bot.onText(/\/id/, (msg) => {
    bot.sendMessage(msg.chat.id, `Sizning ID: <code>${msg.chat.id}</code>`, { parse_mode: 'HTML' });
  });

  // Egaga: kutayotganlar ro'yxati
  bot.onText(/\/pending/, (msg) => {
    if (String(msg.chat.id) !== OWNER_ID) return;
    if (!DB_MAIN.pending.length) return bot.sendMessage(msg.chat.id, 'Kutayotgan so\'rov yo\'q.');
    DB_MAIN.pending.forEach(p => {
      bot.sendMessage(OWNER_ID,
        `⏳ ${p.name}\nID: <code>${p.id}</code>`,
        {
          parse_mode: 'HTML',
          reply_markup: { inline_keyboard: [[
            { text: '✅ Ruxsat', callback_data: `ok:${p.id}` },
            { text: '⛔ Rad', callback_data: `no:${p.id}` }
          ]] }
        });
    });
  });

  // Egaga: ruxsatlilar ro'yxati (o'chirish bilan)
  bot.onText(/\/users/, (msg) => {
    if (String(msg.chat.id) !== OWNER_ID) return;
    if (!DB_MAIN.allowed.length) return bot.sendMessage(msg.chat.id, 'Ruxsat berilganlar yo\'q (egadan tashqari).');
    DB_MAIN.allowed.forEach(uid => {
      bot.sendMessage(OWNER_ID, `👤 ID: <code>${uid}</code>`, {
        parse_mode: 'HTML',
        reply_markup: { inline_keyboard: [[{ text: '🗑 Chiqarish', callback_data: `rm:${uid}` }]] }
      });
    });
  });

  // Tugma bosilganda
  bot.on('callback_query', (q) => {
    const fromId = String(q.from.id);
    if (fromId !== OWNER_ID) { bot.answerCallbackQuery(q.id, { text: 'Faqat ega tasdiqlaydi' }); return; }

    const [action, uid] = q.data.split(':');

    if (action === 'ok') {
      if (!DB_MAIN.allowed.includes(uid)) DB_MAIN.allowed.push(uid);
      DB_MAIN.pending = DB_MAIN.pending.filter(p => p.id !== uid);
      saveMain();
      bot.editMessageText(`✅ Ruxsat berildi: <code>${uid}</code>`, {
        chat_id: q.message.chat.id, message_id: q.message.message_id, parse_mode: 'HTML'
      });
      bot.answerCallbackQuery(q.id, { text: 'Ruxsat berildi' });
      // Foydalanuvchiga xabar + tugma
      bot.sendMessage(uid, '✅ Sizga ruxsat berildi! Endi omborni ochishingiz mumkin.', openKb).catch(() => {});
    }
    else if (action === 'no') {
      DB_MAIN.pending = DB_MAIN.pending.filter(p => p.id !== uid);
      saveMain();
      bot.editMessageText(`⛔ Rad etildi: <code>${uid}</code>`, {
        chat_id: q.message.chat.id, message_id: q.message.message_id, parse_mode: 'HTML'
      });
      bot.answerCallbackQuery(q.id, { text: 'Rad etildi' });
      bot.sendMessage(uid, '⛔ Kirish so\'rovingiz rad etildi.').catch(() => {});
    }
    else if (action === 'rm') {
      DB_MAIN.allowed = DB_MAIN.allowed.filter(x => x !== uid);
      saveMain();
      bot.editMessageText(`🗑 Chiqarildi: <code>${uid}</code>`, {
        chat_id: q.message.chat.id, message_id: q.message.message_id, parse_mode: 'HTML'
      });
      bot.answerCallbackQuery(q.id, { text: 'Chiqarildi' });
    }
  });

  console.log('Telegram bot ishga tushdi.' + (OWNER_ID ? '' : ' (OGOHLANTIRISH: OWNER_ID yo\'q!)'));
}

/* ================= SERVER ================= */
const app = express();
app.use(express.json({ limit: '2mb' }));
const PORT = process.env.PORT || 3000;
const BOT_TOKEN = process.env.BOT_TOKEN || '';
const OWNER_ID = String(process.env.OWNER_ID || '').trim();
const ACCESS_KEY = String(process.env.ACCESS_KEY || '').trim(); // brauzer uchun maxfiy kalit
const APP_PASSWORD = String(process.env.APP_PASSWORD || '').trim(); // asosiy ilova paroli
const APP_PASSWORD_2026 = String(process.env.APP_PASSWORD_2026 || '2026').trim(); // soliq (ikkinchi baza) paroli
const APP_PASSWORD_HUSAN = String(process.env.APP_PASSWORD_HUSAN || 'Husan 2026').trim(); // Husan aka mini-kassa

// Parolga qarab qaysi baza — 'main', '2026', 'husan' yoki null
function whichDB(pw) {
  if (APP_PASSWORD && pw === APP_PASSWORD) return 'main';
  if (APP_PASSWORD_2026 && pw === APP_PASSWORD_2026) return '2026';
  if (APP_PASSWORD_HUSAN && pw === APP_PASSWORD_HUSAN) return 'husan';
  if (!APP_PASSWORD) return 'main'; // asosiy parol qo'yilmagan bo'lsa — asosiy baza
  return null;
}

function authGuard(req, res, next) {
  if (!BOT_TOKEN) { useDB('main'); return next(); } // dev rejimi
  // Parol tekshiruvi — qaysi baza ekanini aniqlaymiz
  const pw = req.get('X-App-Password') || '';
  const which = whichDB(pw);
  if (APP_PASSWORD && !which) return res.status(401).json({ error: 'parol', need_password: true });
  // Husan ham main baza ichida ishlaydi (siz hammasini ko'rasiz), faqat rejimi cheklangan
  useDB(which === '2026' ? '2026' : 'main');
  req.mode = which || 'main';

  // Brauzer uchun: maxfiy kalit (header yoki ?key=...)
  if (ACCESS_KEY) {
    const key = req.get('X-Access-Key') || req.query.key || '';
    if (key === ACCESS_KEY) return next();
  }
  // Telegram Mini App uchun
  const parsed = verifyInitData(req.get('X-Init-Data') || '', BOT_TOKEN);
  if (!parsed || !parsed.user) return res.status(401).json({ error: 'Ruxsat yo\'q. Botni oching.' });
  const uid = String(parsed.user.id);
  const ok = uid === OWNER_ID || DB_MAIN.allowed.includes(uid);
  if (!ok) return res.status(403).json({ error: 'Sizga ruxsat berilmagan. Botda /start bosing.' });
  next();
}

// parolni tekshirish uchun alohida endpoint (guardsiz)
app.get('/api/need-password', (req, res) => {
  res.json({ need: !!APP_PASSWORD });
});
app.post('/api/check-password', (req, res) => {
  const which = whichDB(req.body.password || '');
  if (!which) return res.json({ ok: false });
  res.json({ ok: true, db: which });
});

const api = express.Router();
api.use(authGuard);

// MATERIALLAR
// Nomdan diametrni aniqlaydi: "16 prut" → 16, "prut 12" → 12, "armatura 20" → 20
function diameterFromName(name) {
  if (!name) return 0;
  const low = name.toLowerCase();
  if (!/(prut|armatur|арматур|пруток)/.test(low)) return 0; // prut so'zi bo'lmasa — 0
  const nums = low.match(/\d+([.,]\d+)?/g);
  if (!nums) return 0;
  // eng katta 1-2 xonali butun sonni diametr deb olamiz (6..40 oralig'ida)
  for (const n of nums) {
    const v = parseFloat(n.replace(',', '.'));
    if (v >= 6 && v <= 40 && Number.isInteger(v)) return v;
  }
  return 0;
}

api.get('/materials', (req, res) => {
  const out = DB.materials.slice().sort((a, b) => a.name.localeCompare(b.name)).map(m => ({
    ...m,
    diameter: m.diameter > 0 ? m.diameter : diameterFromName(m.name), // nomdan avtomat
    stock: materialStock(m.id), avg_price: materialAvgPrice(m.id),
    batch_count: DB.batches.filter(b => b.material_id === m.id).length
  }));
  res.json(out);
});
api.post('/materials', (req, res) => {
  const { name, unit, min_qty, diameter } = req.body;
  if (!name) return res.status(400).json({ error: 'Nom kerak' });
  const dia = +diameter || diameterFromName(name); // berilmasa nomdan
  const m = { id: nextId(), name: name.trim(), unit: (unit || 'dona').trim(), min_qty: +min_qty || 0, diameter: dia };
  DB.materials.push(m); saveDB(); res.json({ id: m.id });
});
api.put('/materials/:id', (req, res) => {
  const m = DB.materials.find(x => x.id == req.params.id);
  if (!m) return res.status(404).json({ error: 'Topilmadi' });
  const { name, unit, min_qty, diameter } = req.body;
  m.name = name; m.unit = unit; m.min_qty = +min_qty || 0;
  if (diameter != null) m.diameter = +diameter || 0;
  saveDB(); res.json({ ok: true });
});
api.delete('/materials/:id', (req, res) => {
  const id = +req.params.id;
  DB.materials = DB.materials.filter(m => m.id !== id);
  DB.batches = DB.batches.filter(b => b.material_id !== id);
  DB.recipe = DB.recipe.filter(r => r.material_id !== id);
  saveDB(); res.json({ ok: true });
});

// KIRIMLAR
api.get('/materials/:id/batches', (req, res) => {
  const id = +req.params.id;
  res.json(DB.batches.filter(b => b.material_id === id).sort((a, b) => b.created_at.localeCompare(a.created_at)));
});
api.post('/materials/:id/batches', (req, res) => {
  const { qty, price, note, created_at, debt_name, as_debt, paid_by } = req.body;
  if (!qty || +qty <= 0) return res.status(400).json({ error: 'Miqdor kerak' });
  const b = { id: nextId(), material_id: +req.params.id, qty: +qty, price: +price || 0,
    note: note || '', created_at: created_at || nowISO() };
  // to'lov turlari (pulga olinganda): [{method, amount}, ...]
  if (Array.isArray(paid_by) && paid_by.length) {
    b.paid_by = paid_by.filter(p => +p.amount > 0).map(p => ({ method: p.method, amount: +p.amount }));
  }
  DB.batches.push(b);

  // Qarzga olingan bo'lsa -> "Biz qarzlar"ga o'sha odamga qo'shamiz
  if (as_debt && debt_name && debt_name.trim()) {
    const mat = DB.materials.find(m => m.id == req.params.id);
    const somTotal = (+qty) * (+price || 0);
    const kurs = (DB.settings && +DB.settings.kurs) || 0;
    const currency = kurs > 0 ? 'usd' : 'som';
    const debtAmount = currency === 'usd' ? Math.round((somTotal / kurs) * 100) / 100 : somTotal;
    DB.debts.push({
      id: nextId(), type: 'out', name: debt_name.trim(), phone: '',
      amount: debtAmount, currency,
      note: `Material: ${mat ? mat.name : ''} ${qty} × ${price}` + (currency === 'usd' ? ` (kurs ${kurs})` : ''),
      created_at: b.created_at
    });
    b.as_debt = true; // qarzga olingan — kassadan chiqim EMAS
  }
  saveDB(); res.json({ id: b.id });
});
api.put('/batches/:id', (req, res) => {
  const b = DB.batches.find(x => x.id == req.params.id);
  if (!b) return res.status(404).json({ error: 'Topilmadi' });
  const { qty, price, note, created_at } = req.body;
  if (qty != null) b.qty = +qty;
  if (price != null) b.price = +price;
  if (note != null) b.note = note;
  if (created_at != null) b.created_at = created_at;
  saveDB(); res.json({ ok: true });
});
api.delete('/batches/:id', (req, res) => {
  DB.batches = DB.batches.filter(b => b.id != req.params.id); saveDB(); res.json({ ok: true });
});

// MAHSULOTLAR + RETSEPT
api.get('/products', (req, res) => {
  const out = DB.products.slice().sort((a, b) => a.name.localeCompare(b.name)).map(p => ({
    ...p,
    extras: p.extras || [],
    recipe: DB.recipe.filter(r => r.product_id === p.id).map(r => {
      const m = DB.materials.find(x => x.id === r.material_id);
      return { material_id: r.material_id, qty: r.qty, material_name: m ? m.name : '(o\'chirilgan)', unit: m ? m.unit : '' };
    })
  }));
  res.json(out);
});
api.post('/products', (req, res) => {
  if (!req.body.name) return res.status(400).json({ error: 'Nom kerak' });
  const p = { id: nextId(), name: req.body.name.trim(), created_at: nowISO() };
  DB.products.push(p); saveDB(); res.json({ id: p.id });
});
api.put('/products/:id', (req, res) => {
  const p = DB.products.find(x => x.id == req.params.id);
  if (!p) return res.status(404).json({ error: 'Topilmadi' });
  p.name = req.body.name; saveDB(); res.json({ ok: true });
});
api.delete('/products/:id', (req, res) => {
  const id = +req.params.id;
  DB.products = DB.products.filter(p => p.id !== id);
  DB.recipe = DB.recipe.filter(r => r.product_id !== id);
  saveDB(); res.json({ ok: true });
});
api.post('/products/:id/recipe', (req, res) => {
  const pid = +req.params.id, { material_id, qty } = req.body;
  const ex = DB.recipe.find(r => r.product_id === pid && r.material_id == material_id);
  if (ex) ex.qty = +qty || 0;
  else DB.recipe.push({ id: nextId(), product_id: pid, material_id: +material_id, qty: +qty || 0, created_at: nowISO() });
  saveDB(); res.json({ ok: true });
});
api.delete('/products/:pid/recipe/:mid', (req, res) => {
  DB.recipe = DB.recipe.filter(r => !(r.product_id == req.params.pid && r.material_id == req.params.mid));
  saveDB(); res.json({ ok: true });
});

// QO'SHIMCHA XARAJATLAR (ishchi haqi, elektr, va h.k.) — mahsulot ichida
api.post('/products/:id/extras', (req, res) => {
  const p = DB.products.find(x => x.id == req.params.id);
  if (!p) return res.status(404).json({ error: 'Mahsulot topilmadi' });
  if (!p.extras) p.extras = [];
  const { name, amount } = req.body;
  if (!name) return res.status(400).json({ error: 'Nom kerak' });
  p.extras.push({ id: nextId(), name: name.trim(), amount: +amount || 0 });
  saveDB(); res.json({ ok: true });
});
api.put('/products/:id/extras/:eid', (req, res) => {
  const p = DB.products.find(x => x.id == req.params.id);
  if (!p || !p.extras) return res.status(404).json({ error: 'Topilmadi' });
  const e = p.extras.find(x => x.id == req.params.eid);
  if (!e) return res.status(404).json({ error: 'Topilmadi' });
  if (req.body.name != null) e.name = req.body.name.trim();
  if (req.body.amount != null) e.amount = +req.body.amount || 0;
  saveDB(); res.json({ ok: true });
});
api.delete('/products/:id/extras/:eid', (req, res) => {
  const p = DB.products.find(x => x.id == req.params.id);
  if (p && p.extras) p.extras = p.extras.filter(x => x.id != req.params.eid);
  saveDB(); res.json({ ok: true });
});

// HISOB
api.get('/calc', (req, res) => {
  const p = DB.products.find(x => x.id == req.query.product_id);
  if (!p) return res.status(404).json({ error: 'Mahsulot topilmadi' });
  const qty = +req.query.qty || 0;
  const recipe = DB.recipe.filter(r => r.product_id === p.id);
  let enough = true, totalCost = 0;
  const items = recipe.map(r => {
    const m = DB.materials.find(x => x.id === r.material_id);
    const used = r.qty * qty, have = materialStock(r.material_id), price = materialAvgPrice(r.material_id);
    const cost = used * price, ok = have >= used;
    totalCost += cost; if (!ok) enough = false;
    return { material_id: r.material_id, material_name: m ? m.name : '', unit: m ? m.unit : '', qty: r.qty, used, have, price, cost, ok, need: ok ? 0 : used - have };
  });
  res.json({ product: p, qty, items, enough, totalCost });
});

// ISHLAB CHIQARISH
api.post('/produce', (req, res) => {
  const { product_id, qty } = req.body;
  const p = DB.products.find(x => x.id == product_id);
  if (!p) return res.status(404).json({ error: 'Mahsulot topilmadi' });
  const recipe = DB.recipe.filter(r => r.product_id === p.id);
  for (const r of recipe) {
    if (materialStock(r.material_id) < r.qty * qty) {
      const m = DB.materials.find(x => x.id === r.material_id);
      return res.status(400).json({ error: `Ombor yetmaydi: ${m ? m.name : ''}` });
    }
  }
  const now = nowISO();
  const prodId = nextId();
  DB.productions.push({ id: prodId, product_id: p.id, product_name: p.name, qty: +qty, created_at: now });
  for (const r of recipe) {
    const m = DB.materials.find(x => x.id === r.material_id);
    DB.consumptions.push({
      id: nextId(), production_id: prodId, material_id: r.material_id,
      material_name: m ? m.name : '', qty: r.qty * qty, price: materialAvgPrice(r.material_id), created_at: now
    });
  }
  saveNow();
  res.json({ ok: true, production_id: prodId });
});

// ================= QARZLAR (bizga / biz) =================
// type: 'in' = bizga qarzdor, 'out' = biz qarzdormiz
api.get('/debts/:type', (req, res) => {
  const type = req.params.type; // 'in' yoki 'out'
  const list = DB.debts.filter(d => d.type === type).sort((a, b) => b.created_at.localeCompare(a.created_at));
  const out = list.map(d => {
    const pays = DB.debt_payments.filter(p => p.debt_id === d.id);
    // to'lovlar valyuta bo'yicha
    const paidSom = pays.filter(p => (p.currency || 'som') === 'som').reduce((s, p) => s + (+p.amount || 0), 0);
    const paidUsd = pays.filter(p => p.currency === 'usd').reduce((s, p) => s + (+p.amount || 0), 0);
    const cur = d.currency || 'som';
    const paid = cur === 'usd' ? paidUsd : paidSom;
    return { ...d, currency: cur, paid, qoldiq: (+d.amount || 0) - paid };
  });
  // umumiy — som va dollar alohida
  const totalDebtSom = out.filter(d => d.currency === 'som').reduce((s, d) => s + (+d.amount || 0), 0);
  const totalDebtUsd = out.filter(d => d.currency === 'usd').reduce((s, d) => s + (+d.amount || 0), 0);
  const totalPaidSom = out.filter(d => d.currency === 'som').reduce((s, d) => s + d.paid, 0);
  const totalPaidUsd = out.filter(d => d.currency === 'usd').reduce((s, d) => s + d.paid, 0);
  res.json({
    items: out,
    totalDebtSom, totalDebtUsd, totalPaidSom, totalPaidUsd,
    totalQoldiqSom: totalDebtSom - totalPaidSom,
    totalQoldiqUsd: totalDebtUsd - totalPaidUsd,
    // eski moslik uchun
    totalDebt: totalDebtSom, totalPaid: totalPaidSom, totalQoldiq: totalDebtSom - totalPaidSom
  });
});
// Ism bo'yicha qidirish (yangi odammi yoki bormi)
api.get('/debts/:type/find', (req, res) => {
  const type = req.params.type;
  const q = (req.query.name || '').trim().toLowerCase();
  const matches = DB.debts.filter(d => d.type === type && d.name.toLowerCase() === q);
  // takroriy ismlarni birlashtiramiz (id bo'yicha guruh emas, ism bo'yicha)
  const uniqueNames = {};
  DB.debts.filter(d => d.type === type && d.name.toLowerCase().includes(q) && q).forEach(d => {
    if (!uniqueNames[d.name]) uniqueNames[d.name] = { name: d.name, phone: d.phone, count: 0 };
    uniqueNames[d.name].count++;
  });
  res.json({ exact: matches.length > 0, suggestions: Object.values(uniqueNames).slice(0, 5) });
});
api.post('/debts', (req, res) => {
  const { type, name, phone, amount, note, currency } = req.body;
  if (!['in', 'out'].includes(type)) return res.status(400).json({ error: 'Tur xato' });
  if (!name) return res.status(400).json({ error: 'Nom kerak' });
  const d = {
    id: nextId(), type, name: name.trim(), phone: (phone || '').trim(),
    amount: +amount || 0, note: (note || '').trim(),
    currency: currency === 'usd' ? 'usd' : 'som',
    created_at: req.body.created_at || nowISO()
  };
  DB.debts.push(d); saveDB(); res.json({ id: d.id });
});
api.put('/debts/:id', (req, res) => {
  const d = DB.debts.find(x => x.id == req.params.id);
  if (!d) return res.status(404).json({ error: 'Topilmadi' });
  if (req.body.name != null) d.name = req.body.name.trim();
  if (req.body.phone != null) d.phone = req.body.phone.trim();
  if (req.body.amount != null) d.amount = +req.body.amount || 0;
  if (req.body.note != null) d.note = req.body.note.trim();
  saveDB(); res.json({ ok: true });
});
api.delete('/debts/:id', (req, res) => {
  const id = +req.params.id;
  DB.debts = DB.debts.filter(d => d.id !== id);
  DB.debt_payments = DB.debt_payments.filter(p => p.debt_id !== id);
  saveDB(); res.json({ ok: true });
});
// qarz to'lovlari
api.get('/debts/:id/payments', (req, res) => {
  const id = +req.params.id;
  res.json(DB.debt_payments.filter(p => p.debt_id === id).sort((a, b) => b.created_at.localeCompare(a.created_at)));
});
api.post('/debts/:id/payments', (req, res) => {
  const d = DB.debts.find(x => x.id == req.params.id);
  if (!d) return res.status(404).json({ error: 'Topilmadi' });
  const amount = +req.body.amount;
  if (!amount || amount <= 0) return res.status(400).json({ error: 'Summa kerak' });
  DB.debt_payments.push({
    id: nextId(), debt_id: d.id, amount,
    currency: req.body.currency === 'usd' ? 'usd' : (d.currency || 'som'),
    note: (req.body.note || '').trim(), created_at: req.body.created_at || nowISO()
  });
  saveDB(); res.json({ ok: true });
});
api.delete('/debts/:did/payments/:pid', (req, res) => {
  DB.debt_payments = DB.debt_payments.filter(p => p.id != req.params.pid);
  saveDB(); res.json({ ok: true });
});

// ================= HUSAN AKA (umumiy kassaning cho'ntagi) =================
// Husan kassa umumiy kassa bilan bog'langan:
//  - Siz kassadan "Husan aka"ga chiqim qilsangiz -> Husan kassasiga kirim (src: from_kassa)
//  - Husan ichki rasxod qiladi (benzin, Mirfozilga) -> Husan kassasidan chiqim (src: expense)
//  - Husan pul qaytaradi -> tasdiq kutadi -> siz tasdiqlasangiz umumiy kassaga kirim (src: return)
api.get('/husan', (req, res) => {
  const rows = (DB.husan || []).slice();
  // Kassadan "Husan aka" kategoriyasi bilan chiqilgan pullar -> Husan kassasiga kirim
  const fromKassa = (DB.kassa || []).filter(k => k.kind === 'out' && (k.category === 'Husan aka' || (k.method === 'Husan aka')));
  fromKassa.forEach(k => {
    rows.push({ id: 'k' + k.id, kind: 'in', amount: +k.amount || 0,
      currency: (k.method === 'Naqd $' || k.note && /\$/.test(k.note)) ? 'usd' : 'som',
      note: 'Kassadan berildi' + (k.note ? ' · ' + k.note : ''), src: 'from_kassa', locked: true,
      created_at: k.created_at });
  });
  rows.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));

  const calc = { som_in: 0, som_out: 0, usd_in: 0, usd_out: 0 };
  rows.forEach(r => {
    if (r.pending) return;
    const a = +r.amount || 0;
    if (r.currency === 'usd') { if (r.kind === 'in') calc.usd_in += a; else calc.usd_out += a; }
    else { if (r.kind === 'in') calc.som_in += a; else calc.som_out += a; }
  });
  const pendingCount = rows.filter(r => r.pending).length;
  // maqsad bo'yicha jamlash (faqat rasxodlar)
  const byPurpose = {};
  rows.forEach(r => {
    if (r.kind === 'out' && !r.pending && r.purpose) {
      if (!byPurpose[r.purpose]) byPurpose[r.purpose] = { purpose: r.purpose, som: 0, usd: 0 };
      if (r.currency === 'usd') byPurpose[r.purpose].usd += r.amount; else byPurpose[r.purpose].som += r.amount;
    }
  });
  res.json({
    rows,
    som_balance: calc.som_in - calc.som_out,
    usd_balance: calc.usd_in - calc.usd_out,
    som_in: calc.som_in, som_out: calc.som_out, usd_in: calc.usd_in, usd_out: calc.usd_out,
    pendingCount,
    purposes: Object.values(byPurpose).sort((a, b) => (b.som + b.usd) - (a.som + a.usd))
  });
});
// Husan tomonidan: ichki rasxod yoki pul qaytarish
api.post('/husan', (req, res) => {
  const { kind, amount, currency, note, action, purpose } = req.body;
  const a = +amount;
  if (!a || a <= 0) return res.status(400).json({ error: 'Summa kerak' });
  if (!DB.husan) DB.husan = [];
  const cur = currency === 'usd' ? 'usd' : 'som';

  if (action === 'return') {
    DB.husan.push({ id: nextId(), kind: 'out', amount: a, currency: cur,
      note: (note || 'Kassaga qaytardi').trim(), src: 'return', pending: true,
      created_at: req.body.created_at || nowISO() });
    saveDB(); return res.json({ ok: true, pending: true });
  }
  if (!['in', 'out'].includes(kind)) return res.status(400).json({ error: 'Tur xato' });
  DB.husan.push({ id: nextId(), kind, amount: a, currency: cur,
    note: (note || '').trim(), purpose: (purpose || '').trim(), src: kind === 'out' ? 'expense' : 'manual',
    created_at: req.body.created_at || nowISO() });
  saveDB(); res.json({ ok: true });
});
api.delete('/husan/:id', (req, res) => {
  DB.husan = (DB.husan || []).filter(k => k.id != req.params.id);
  saveDB(); res.json({ ok: true });
});
// Siz qaytarishni tasdiqlaysiz -> pending olib tashlanadi, umumiy kassaga kirim qo'shiladi
api.post('/husan/:id/confirm', (req, res) => {
  const item = (DB.husan || []).find(k => k.id == req.params.id);
  if (!item || !item.pending) return res.status(404).json({ error: 'Topilmadi' });
  item.pending = false; // endi Husan balansidan chiqim bo'ldi
  // umumiy kassaga kirim (Husan qaytardi)
  const somAmount = item.currency === 'usd'
    ? Math.round(item.amount * ((DB.settings && +DB.settings.kurs) || 0))
    : item.amount;
  if (somAmount > 0) {
    DB.kassa.push({ id: nextId(), kind: 'in', amount: somAmount, category: 'Husan aka qaytardi',
      note: (item.note || '') + (item.currency === 'usd' ? ` (${item.amount}$)` : ''),
      method: item.currency === 'usd' ? 'Naqd $' : 'Naqd so\'m', created_at: nowISO() });
  }
  saveDB(); res.json({ ok: true });
});
api.post('/husan/:id/reject', (req, res) => {
  DB.husan = (DB.husan || []).filter(k => k.id != req.params.id);
  saveDB(); res.json({ ok: true });
});

// ================= KASSA (pul nazorati) =================
// Kassa harakatlari 3 manbadan yig'iladi:
//  KIRIM: mijoz to'lovlari + bizga qarzdorlar to'lovlari + qo'lda kirim
//  CHIQIM: material xaridlari + biz qarzni to'laganlar + qo'lda chiqim
const KASSA_ALL_CATS = [
  'Sotuv lesa', 'Sotuv tayrot', 'Arenda', 'Kovka', 'Arendadan sotilgan tovarlar',
  'Gazel(067)', 'Labo(240)', 'Labo(562)', 'Remont', 'Foyda balka', 'Foyda lyulka', 'Foyda',
  'Arendaga qo\'shilganlar', 'Kutilmagan harajatlar', 'Oziq ovqatlar', 'Doimiy harajat',
  'Oylik', 'Reklama', 'Cobalt(412)', 'Cobalt(521)', 'Cobalt(222)', 'Pulni muzlatish',
  'Nalog', 'Komissiya', 'Rivojlanish', 'Sherzod aka', 'Husan aka'
];
function buildKassa() {
  const rows = [];
  // 1. Mijoz to'lovlari (kirim) — som
  DB.customer_payments.forEach(p => {
    const c = DB.customers.find(x => x.id === p.customer_id);
    rows.push({ id: 'cp' + p.id, kind: 'in', amount: +p.amount || 0, category: 'Mijoz to\'lovi',
      name: c ? c.name : '', note: p.note || '', method: p.method || 'Naqd so\'m', currency: 'som', created_at: p.created_at, locked: true });
  });
  // 2. Bizga qarzdor to'lovlari (kirim) — type 'in'
  DB.debt_payments.forEach(p => {
    const d = DB.debts.find(x => x.id === p.debt_id);
    if (!d) return;
    if (d.type === 'in') {
      rows.push({ id: 'dp' + p.id, kind: 'in', amount: +p.amount || 0, category: 'Qarzdor to\'lovi',
        name: d.name, note: p.note || '', method: 'Naqd so\'m', currency: 'som', created_at: p.created_at, locked: true });
    } else {
      rows.push({ id: 'dp' + p.id, kind: 'out', amount: +p.amount || 0, category: 'Biz qarz to\'ladik',
        name: d.name, note: p.note || '', method: 'Naqd so\'m', currency: 'som', created_at: p.created_at, locked: true });
    }
  });
  // 3. Material xaridlari (chiqim) — qarzga olinmagan bo'lsa
  DB.materials.forEach(m => {
    (m.batches || []).forEach(b => {
      if (b.as_debt) return; // qarzga olingan — kassadan chiqim emas
      const summa = (+b.qty || 0) * (+b.price || 0);
      if (summa <= 0) return;
      if (Array.isArray(b.paid_by) && b.paid_by.length) {
        // bir nechta turdan to'langan — har birini alohida chiqim
        b.paid_by.forEach((p, i) => {
          rows.push({ id: 'mb' + b.id + '_' + i, kind: 'out', amount: +p.amount || 0, category: 'Material xaridi',
            name: m.name, note: `${fmtNum(b.qty)} ${m.unit} × ${fmtNum(b.price)}`,
            method: p.method || 'Naqd so\'m', currency: p.method === 'Naqd $' ? 'usd' : 'som',
            created_at: b.created_at, locked: true });
        });
      } else {
        rows.push({ id: 'mb' + b.id, kind: 'out', amount: summa, category: 'Material xaridi',
          name: m.name, note: `${fmtNum(b.qty)} ${m.unit} × ${fmtNum(b.price)}`,
          method: 'Naqd so\'m', currency: 'som', created_at: b.created_at, locked: true });
      }
    });
  });
  // 4. Qo'lda qo'shilgan kassa harakatlari — o'z valyutasi bilan
  DB.kassa.forEach(k => {
    rows.push({ id: 'k' + k.id, kind: k.kind, amount: +k.amount || 0, category: k.category || (k.kind === 'in' ? 'Qo\'lda kirim' : 'Qo\'lda chiqim'),
      name: '', note: k.note || '', method: k.method || 'Naqd so\'m', currency: k.currency || 'som', created_at: k.created_at, locked: false, exchange: k.exchange });
  });
  rows.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
  return rows;
}
function fmtNum(n) { return (Math.round((+n || 0) * 100) / 100).toLocaleString('ru-RU'); }

// ATCHOT — davr hisoboti (sana oralig'ida)
api.get('/atchot', (req, res) => {
  const all = buildKassa();
  const from = req.query.from || '1970-01-01';
  const to = (req.query.to || '2999-12-31') + 'T23:59:59';
  const start0 = +((DB.settings || {}).kassa_start) || 0;

  const before = all.filter(r => (r.created_at || '') < from);   // davrdan oldin (boshlang'ich qoldiq)
  const inRange = all.filter(r => (r.created_at || '') >= from && (r.created_at || '') <= to);

  // usullar
  const methods = ['Naqd so\'m', 'Naqd $', 'Plastik (p)', 'UMJ perechisleniya', 'MT perechisleniya'];
  const mkMethodObj = () => { const o = {}; methods.forEach(m => o[m] = 0); return o; };

  // Oy boshi qoldiq (har usul) — davrdan oldingi harakatlar yig'indisi
  const startBal = mkMethodObj();
  startBal['Naqd so\'m'] += start0; // boshlang'ich naqd
  before.forEach(r => {
    const m = r.method || 'Naqd so\'m';
    if (startBal[m] == null) startBal[m] = 0;
    startBal[m] += (r.kind === 'in' ? r.amount : -r.amount);
  });

  // Davr ichidagi kirim/chiqim — kategoriya bo'yicha (dollar -> som, kurs bilan)
  const kurs = (DB.settings && +DB.settings.kurs) || 0;
  const toSom = (r) => r.currency === 'usd' ? (r.amount * kurs) : r.amount; // hisobot so'mda
  const catIn = {}, catOut = {};
  const methIn = mkMethodObj(), methOut = mkMethodObj();
  inRange.forEach(r => {
    const m = r.method || 'Naqd so\'m';
    const somVal = toSom(r);
    if (r.kind === 'in') {
      catIn[r.category] = (catIn[r.category] || 0) + somVal;
      if (methIn[m] == null) methIn[m] = 0; methIn[m] += r.amount; // usul balansi o'z valyutasida
    } else {
      catOut[r.category] = (catOut[r.category] || 0) + somVal;
      if (methOut[m] == null) methOut[m] = 0; methOut[m] += r.amount;
    }
  });

  // Oy oxiri qoldiq = boshi + davr kirim - davr chiqim (har usul)
  const endBal = mkMethodObj();
  methods.forEach(m => endBal[m] = (startBal[m] || 0) + (methIn[m] || 0) - (methOut[m] || 0));
  Object.keys(startBal).forEach(m => { if (!methods.includes(m)) endBal[m] = (startBal[m] || 0) + (methIn[m] || 0) - (methOut[m] || 0); });

  const totalIn = Object.values(catIn).reduce((s, v) => s + v, 0);
  const totalOut = Object.values(catOut).reduce((s, v) => s + v, 0);

  // Hamma kategoriyalarni ko'rsatamiz (0 bo'lsa ham). Avtomat kategoriyalar + 27 tur.
  const autoInCats = ['Mijoz to\'lovi', 'Qarzdor to\'lovi', 'Husan aka qaytardi', 'Valyuta ayirboshlash'];
  const autoOutCats = ['Material xaridi', 'Biz qarz to\'ladik', 'Valyuta ayirboshlash'];
  const allInCats = [...new Set([...KASSA_ALL_CATS, ...autoInCats, ...Object.keys(catIn)])];
  const allOutCats = [...new Set([...KASSA_ALL_CATS, ...autoOutCats, ...Object.keys(catOut)])];

  res.json({
    from: req.query.from, to: req.query.to,
    startBal, endBal, methIn, methOut,
    catIn: allInCats.map(k => ({ category: k, amount: catIn[k] || 0 })).sort((a, b) => b.amount - a.amount),
    catOut: allOutCats.map(k => ({ category: k, amount: catOut[k] || 0 })).sort((a, b) => b.amount - a.amount),
    totalIn, totalOut, foyda: totalIn - totalOut
  });
});

api.get('/kassa', (req, res) => {
  const rows = buildKassa();
  const start = +((DB.settings || {}).kassa_start) || 0;
  // som va dollar alohida
  const somRows = rows.filter(r => (r.currency || 'som') === 'som');
  const usdRows = rows.filter(r => r.currency === 'usd');
  const totalIn = somRows.filter(r => r.kind === 'in').reduce((s, r) => s + r.amount, 0);
  const totalOut = somRows.filter(r => r.kind === 'out').reduce((s, r) => s + r.amount, 0);
  const balance = start + totalIn - totalOut;
  const usdIn = usdRows.filter(r => r.kind === 'in').reduce((s, r) => s + r.amount, 0);
  const usdOut = usdRows.filter(r => r.kind === 'out').reduce((s, r) => s + r.amount, 0);
  const usdBalance = usdIn - usdOut;

  // 5 to'lov usuli bo'yicha alohida balans
  const methods = ['Naqd so\'m', 'Naqd $', 'Plastik (p)', 'UMJ perechisleniya', 'MT perechisleniya'];
  const byMethod = {};
  methods.forEach(m => byMethod[m] = { method: m, in: 0, out: 0, balance: 0, currency: m === 'Naqd $' ? 'usd' : 'som' });
  rows.forEach(r => {
    const m = r.method || 'Naqd so\'m';
    if (!byMethod[m]) byMethod[m] = { method: m, in: 0, out: 0, balance: 0, currency: r.currency || 'som' };
    if (r.kind === 'in') byMethod[m].in += r.amount; else byMethod[m].out += r.amount;
  });
  // Naqd so'm ga boshlang'ich qoldiqni qo'shamiz
  if (byMethod['Naqd so\'m']) byMethod['Naqd so\'m'].in += start;
  Object.values(byMethod).forEach(m => m.balance = m.in - m.out);

  // kategoriya bo'yicha (som)
  const catMap = {};
  somRows.forEach(r => {
    const key = r.category + '|' + r.kind;
    if (!catMap[key]) catMap[key] = { category: r.category, kind: r.kind, total: 0, count: 0 };
    catMap[key].total += r.amount; catMap[key].count++;
  });
  const categories = Object.values(catMap).sort((a, b) => b.total - a.total);
  res.json({ start, totalIn, totalOut, balance, usdBalance, usdIn, usdOut,
    methods: Object.values(byMethod), rows, categories });
});
// Kassa turlari (default + qo'shilganlar)
const PAYMENT_METHODS = ['Naqd so\'m', 'Naqd $', 'Plastik (p)', 'UMJ perechisleniya', 'MT perechisleniya'];
api.get('/payment-methods', (req, res) => res.json(PAYMENT_METHODS));

const DEFAULT_KASSA_CATS = { in: KASSA_ALL_CATS, out: KASSA_ALL_CATS };
api.get('/kassa/cats', (req, res) => {
  const custom = DB.kassa_cats || { in: [], out: [] };
  res.json({
    in: [...DEFAULT_KASSA_CATS.in, ...custom.in.filter(c => !DEFAULT_KASSA_CATS.in.includes(c))],
    out: [...DEFAULT_KASSA_CATS.out, ...custom.out.filter(c => !DEFAULT_KASSA_CATS.out.includes(c))]
  });
});
api.post('/kassa/cats', (req, res) => {
  const { kind, name } = req.body;
  if (!['in', 'out'].includes(kind)) return res.status(400).json({ error: 'Tur xato' });
  const n = (name || '').trim();
  if (!n) return res.status(400).json({ error: 'Nom kerak' });
  if (!DB.kassa_cats) DB.kassa_cats = { in: [], out: [] };
  if (!DEFAULT_KASSA_CATS[kind].includes(n) && !DB.kassa_cats[kind].includes(n)) {
    DB.kassa_cats[kind].push(n);
    saveDB();
  }
  res.json({ ok: true });
});

// qo'lda kassa harakati qo'shish
api.post('/kassa', (req, res) => {
  const { kind, amount, category, note, method } = req.body;
  if (!['in', 'out'].includes(kind)) return res.status(400).json({ error: 'Tur xato' });
  const a = +amount;
  if (!a || a <= 0) return res.status(400).json({ error: 'Summa kerak' });
  const m = (method || 'Naqd so\'m').trim();
  const currency = (m === 'Naqd $') ? 'usd' : 'som'; // Naqd $ = dollar, boshqasi = som
  DB.kassa.push({ id: nextId(), kind, amount: a, category: (category || '').trim(),
    note: (note || '').trim(), method: m, currency, created_at: req.body.created_at || nowISO() });
  saveDB(); res.json({ ok: true });
});
// Kassa harakatini tahrirlash
api.put('/kassa/:id', (req, res) => {
  const k = (DB.kassa || []).find(x => x.id == req.params.id);
  if (!k) return res.status(404).json({ error: 'Topilmadi' });
  if (req.body.amount != null) k.amount = +req.body.amount || 0;
  if (req.body.note != null) k.note = req.body.note;
  if (req.body.category != null) k.category = req.body.category;
  if (req.body.method != null) { k.method = req.body.method; k.currency = req.body.method === 'Naqd $' ? 'usd' : 'som'; }
  saveDB(); res.json({ ok: true });
});
// VALYUTA AYIRBOSHLASH — bir usuldan boshqasiga (masalan 600k Naqd som -> 100$)
// from: {method, amount}  to: {method, amount}
api.post('/kassa/exchange', (req, res) => {
  const { from_method, from_amount, to_method, to_amount, note } = req.body;
  const fa = +from_amount, ta = +to_amount;
  if (!fa || fa <= 0 || !ta || ta <= 0) return res.status(400).json({ error: 'Summalar kerak' });
  const now = nowISO();
  const fromCur = from_method === 'Naqd $' ? 'usd' : 'som';
  const toCur = to_method === 'Naqd $' ? 'usd' : 'som';
  // chiqim (from usuldan)
  DB.kassa.push({ id: nextId(), kind: 'out', amount: fa, category: 'Valyuta ayirboshlash',
    note: (note || '') + ` (${from_method} → ${to_method})`, method: from_method, currency: fromCur, exchange: true, created_at: now });
  // kirim (to usulga)
  DB.kassa.push({ id: nextId(), kind: 'in', amount: ta, category: 'Valyuta ayirboshlash',
    note: (note || '') + ` (${from_method} → ${to_method})`, method: to_method, currency: toCur, exchange: true, created_at: now });
  saveDB(); res.json({ ok: true });
});
api.delete('/kassa/:id', (req, res) => {
  DB.kassa = DB.kassa.filter(k => k.id != req.params.id);
  saveDB(); res.json({ ok: true });
});
// boshlang'ich qoldiqni o'rnatish
api.put('/kassa/start', (req, res) => {
  if (!DB.settings) DB.settings = { kurs: 0, kassa_start: 0 };
  DB.settings.kassa_start = +req.body.amount || 0;
  saveDB(); res.json({ ok: true });
});

// ================= SOZLAMALAR (kunlik kurs) =================
api.get('/settings', (req, res) => {
  res.json(DB.settings || { kurs: 0 });
});
api.put('/settings', (req, res) => {
  if (!DB.settings) DB.settings = { kurs: 0 };
  if (req.body.kurs != null) DB.settings.kurs = +req.body.kurs || 0;
  saveDB(); res.json({ ok: true, settings: DB.settings });
});

// ================= MIJOZLAR =================
api.get('/customers', (req, res) => {
  const out = DB.customers.slice().sort((a, b) => a.name.localeCompare(b.name)).map(c => {
    const items = DB.customer_items.filter(i => i.customer_id === c.id);
    const payments = DB.customer_payments.filter(pp => pp.customer_id === c.id);
    const byProd = {};
    items.forEach(i => {
      if (!byProd[i.product_name]) byProd[i.product_name] = 0;
      byProd[i.product_name] += +i.qty;
    });
    const summary = Object.entries(byProd).map(([name, qty]) => ({ name, qty }));
    const totalOldi = items.reduce((s, i) => s + (+i.umumiy || 0), 0);
    const totalTolarPul = payments.reduce((s, pp) => s + (+pp.amount || 0), 0);
    const totalQarz = totalOldi - totalTolarPul;
    const totalFoyda = items.reduce((s, i) => s + (+i.foyda || 0), 0);
    return { ...c, item_count: items.length, summary, totalOldi, totalTolarPul, totalQarz, totalFoyda };
  });
  res.json(out);
});
api.post('/customers', (req, res) => {
  if (!req.body.name) return res.status(400).json({ error: 'Mijoz nomi kerak' });
  const c = { id: nextId(), name: req.body.name.trim(), phone: (req.body.phone || '').trim(), created_at: nowISO() };
  DB.customers.push(c); saveDB(); res.json({ id: c.id });
});
api.put('/customers/:id', (req, res) => {
  const c = DB.customers.find(x => x.id == req.params.id);
  if (!c) return res.status(404).json({ error: 'Topilmadi' });
  if (req.body.name != null) c.name = req.body.name;
  if (req.body.phone != null) c.phone = req.body.phone.trim();
  saveDB(); res.json({ ok: true });
});
api.delete('/customers/:id', (req, res) => {
  const id = +req.params.id;
  const returnStock = req.query.return_stock === '1' || req.body && req.body.return_stock;

  // shu mijozning sotuvlariga bog'liq ishlab chiqarishni topib, material qaytaramiz (agar so'ralsa)
  if (returnStock) {
    const items = DB.customer_items.filter(i => i.customer_id === id);
    items.forEach(it => {
      // shu sotuv paytida yozilgan production (created_at + product bo'yicha)
      const prod = DB.productions.find(p => p.product_id === it.product_id && p.qty === it.qty
        && Math.abs(new Date(p.created_at) - new Date(it.created_at)) < 5000);
      if (prod) {
        DB.consumptions = DB.consumptions.filter(c => c.production_id !== prod.id);
        DB.productions = DB.productions.filter(p => p.id !== prod.id);
      }
    });
  }

  DB.customers = DB.customers.filter(c => c.id !== id);
  DB.customer_items = DB.customer_items.filter(i => i.customer_id !== id);
  DB.customer_payments = DB.customer_payments.filter(p => p.customer_id !== id); // to'lovlar ham (kassadan ham ketadi)
  saveDB(); res.json({ ok: true });
});

// bitta mijozning to'liq tarixi
api.get('/customers/:id/items', (req, res) => {
  const id = +req.params.id;
  res.json(DB.customer_items.filter(i => i.customer_id === id)
    .sort((a, b) => b.created_at.localeCompare(a.created_at)));
});

// Mijozning to'langan/to'lanmagan bo'linishi (soni bo'yicha)
api.get('/customers/:id/split', (req, res) => {
  const id = +req.params.id;
  const items = DB.customer_items.filter(i => i.customer_id === id)
    .sort((a, b) => a.created_at.localeCompare(b.created_at)); // eski birinchi (FIFO)
  const payments = DB.customer_payments.filter(p => p.customer_id === id);
  let pulBor = payments.reduce((s, p) => s + (+p.amount || 0), 0); // jami to'langan pul

  // har sotuvni ketma-ket "yopamiz": pul yetgancha to'langan, qolgani qarz
  const paidLines = [], dueLines = [];
  let paidQty = 0, paidSum = 0, paidCost = 0, paidFoyda = 0;
  let dueQty = 0, dueSum = 0, dueCost = 0;

  items.forEach(it => {
    const qty = +it.qty || 0;
    const price = +it.price || 0;               // donasi narxi
    const unitCost = qty ? (+it.cost || 0) / qty : 0; // donasi tannarxi
    const lineTotal = +it.umumiy || (price * qty);

    // shu sotuvga qancha pul yetadi?
    let payHere = Math.min(pulBor, lineTotal);
    pulBor -= payHere;
    // nechta dona "to'langan" (pul ÷ narx) — kasrli bo'lishi mumkin (50.5 kabi)
    let qPaid = price ? (payHere / price) : (payHere >= lineTotal ? qty : 0);
    qPaid = Math.round(qPaid * 100) / 100; // 2 xonagacha (50.5, 33.33)
    if (qPaid > qty) qPaid = qty;
    let qDue = Math.round((qty - qPaid) * 100) / 100;

    if (qPaid > 0) {
      const sum = qPaid * price, cost = qPaid * unitCost, foyda = sum - cost;
      paidQty += qPaid; paidSum += sum; paidCost += cost; paidFoyda += foyda;
      paidLines.push({ product_name: it.product_name, qty: qPaid, price, sum, cost, foyda, created_at: it.created_at });
    }
    if (qDue > 0) {
      const sum = qDue * price, cost = qDue * unitCost;
      dueQty += qDue; dueSum += sum; dueCost += cost;
      dueLines.push({ product_name: it.product_name, qty: qDue, price, sum, cost, created_at: it.created_at });
    }
  });

  const kurs = (DB.settings && +DB.settings.kurs) || 0;
  res.json({
    kurs,
    due: { qty: dueQty, sum: dueSum, cost: dueCost, lines: dueLines },        // to'lanmagan (qarz)
    paid: { qty: paidQty, sum: paidSum, cost: paidCost, foyda: paidFoyda,     // to'langan
            foyda_usd: kurs ? paidFoyda / kurs : 0, lines: paidLines },
  });
});

// mijozga mahsulot qo'shish (ixtiyoriy: ombordan material ayirish)
api.post('/customers/:id/add', (req, res) => {
  const c = DB.customers.find(x => x.id == req.params.id);
  if (!c) return res.status(404).json({ error: 'Mijoz topilmadi' });
  const { product_id, qty, deduct, price, cost } = req.body;
  const p = DB.products.find(x => x.id == product_id);
  if (!p) return res.status(404).json({ error: 'Mahsulot topilmadi' });
  const n = +qty;
  if (!n || n <= 0) return res.status(400).json({ error: 'Soni kerak' });

  // ombordan ayirish kerak bo'lsa — avval yetarli tekshiramiz
  if (deduct) {
    const recipe = DB.recipe.filter(r => r.product_id === p.id);
    for (const r of recipe) {
      if (materialStock(r.material_id) < r.qty * n) {
        const m = DB.materials.find(x => x.id === r.material_id);
        return res.status(400).json({ error: `Ombor yetmaydi: ${m ? m.name : ''}` });
      }
    }
    const now = nowISO();
    const prodId = nextId();
    DB.productions.push({ id: prodId, product_id: p.id, product_name: p.name, qty: n, created_at: now });
    for (const r of recipe) {
      const m = DB.materials.find(x => x.id === r.material_id);
      DB.consumptions.push({
        id: nextId(), production_id: prodId, material_id: r.material_id,
        material_name: m ? m.name : '', qty: r.qty * n, price: materialAvgPrice(r.material_id), created_at: now
      });
    }
  }

  // pul hisob-kitobi (avans/qarz endi TO'LOVLAR orqali — bu yerda emas)
  const priceV = +price || 0;          // 1 dona sotuv narxi
  const umumiy = priceV * n;           // umumiy sotuv (mijoz shuncha qarzdor bo'ldi)
  const costV = +cost || 0;            // tannarx (jami)
  const foyda = umumiy - costV;        // kutilayotgan foyda

  // mijoz kartasiga yozish (sotuv = "oldi")
  DB.customer_items.push({
    id: nextId(), customer_id: c.id, product_id: p.id, product_name: p.name,
    qty: n, price: priceV, umumiy, cost: costV, foyda,
    note: deduct ? 'ombordan ayirildi' : '', created_at: nowISO()
  });
  saveNow();
  res.json({ ok: true });
});

// TO'LOVLAR (mijoz cho'zib to'laydi)
api.get('/customers/:id/payments', (req, res) => {
  const id = +req.params.id;
  res.json(DB.customer_payments.filter(p => p.customer_id === id)
    .sort((a, b) => b.created_at.localeCompare(a.created_at)));
});
api.post('/customers/:id/payments', (req, res) => {
  const c = DB.customers.find(x => x.id == req.params.id);
  if (!c) return res.status(404).json({ error: 'Mijoz topilmadi' });
  const amount = +req.body.amount;
  if (!amount || amount <= 0) return res.status(400).json({ error: 'Summa kerak' });
  DB.customer_payments.push({
    id: nextId(), customer_id: c.id, amount,
    note: (req.body.note || '').trim(), created_at: req.body.created_at || nowISO()
  });
  saveNow(); res.json({ ok: true });
});
api.delete('/customers/:cid/payments/:pid', (req, res) => {
  DB.customer_payments = DB.customer_payments.filter(p => p.id != req.params.pid);
  saveDB(); res.json({ ok: true });
});

// mahsulotning 1 dona tannarxi (normadan: material + qo'shimcha xarajat)
api.get('/products/:id/unitcost', (req, res) => {
  const p = DB.products.find(x => x.id == req.params.id);
  if (!p) return res.status(404).json({ error: 'Topilmadi' });
  const recipe = DB.recipe.filter(r => r.product_id === p.id);
  let matCost = 0;
  recipe.forEach(r => { matCost += (+r.qty) * materialAvgPrice(r.material_id); });
  let extraCost = 0;
  (p.extras || []).forEach(e => { extraCost += (+e.amount || 0); });
  res.json({ matCost, extraCost, unitCost: matCost + extraCost });
});

// mijoz kartasidan bitta yozuvni o'chirish
// sotuv yozuvini tahrirlash (narx, tannarx)
api.put('/customers/:cid/items/:iid', (req, res) => {
  const i = DB.customer_items.find(x => x.id == req.params.iid);
  if (!i) return res.status(404).json({ error: 'Topilmadi' });
  const { qty, price, cost } = req.body;
  if (qty != null) i.qty = +qty || 0;
  if (price != null) i.price = +price || 0;
  if (cost != null) i.cost = +cost || 0;
  // qayta hisoblash
  i.umumiy = (+i.price || 0) * (+i.qty || 0);
  i.foyda = i.umumiy - (+i.cost || 0);
  saveDB(); res.json({ ok: true });
});

api.delete('/customers/:cid/items/:iid', (req, res) => {
  DB.customer_items = DB.customer_items.filter(i => i.id != req.params.iid);
  saveDB(); res.json({ ok: true });
});

// SINOV MA'LUMOTINI TOZALASH (faqat ega)
api.post('/reset', (req, res) => {
  // faqat ega yoki ACCESS_KEY bilan
  const parsed = verifyInitData(req.get('X-Init-Data') || '', BOT_TOKEN);
  const isOwner = parsed && parsed.user && String(parsed.user.id) === OWNER_ID;
  const byKey = ACCESS_KEY && (req.get('X-Access-Key') === ACCESS_KEY);
  if (BOT_TOKEN && !isOwner && !byKey) return res.status(403).json({ error: 'Faqat ega tozalaydi' });

  const scope = req.body.scope || 'all';
  if (scope === 'all') {
    DB.materials = []; DB.batches = []; DB.products = []; DB.recipe = [];
    DB.productions = []; DB.consumptions = []; DB.customers = []; DB.customer_items = []; DB.customer_payments = [];
    DB.debts = []; DB.debt_payments = []; DB.kassa = [];
    if (DB.settings) DB.settings.kassa_start = 0; // kassa boshlang'ich qoldiq ham 0
  } else if (scope === 'sales') {
    // faqat sotuv/mijoz + ishlab chiqarish tarixi (ombor qoladi)
    DB.customers = []; DB.customer_items = []; DB.customer_payments = [];
    DB.productions = []; DB.consumptions = [];
  } else if (scope === 'stock') {
    // faqat ombor kirimlari (mahsulot/normalar qoladi)
    DB.batches = []; DB.consumptions = []; DB.productions = [];
  }
  saveNow();
  res.json({ ok: true });
});

// HISOBOT
function buildReport(from0, to0) {
  const from = from0 || '1970-01-01';
  const to = (to0 || '2999-12-31') + 'T23:59:59';
  const inRange = (d) => d >= from && d <= to;
  const incomes = DB.batches.filter(b => inRange(b.created_at)).map(b => {
    const m = DB.materials.find(x => x.id === b.material_id);
    return { ...b, material_name: m ? m.name : '', unit: m ? m.unit : '' };
  }).sort((a, b) => b.created_at.localeCompare(a.created_at));
  const outgoings = DB.consumptions.filter(c => inRange(c.created_at)).map(c => {
    const pr = DB.productions.find(x => x.id === c.production_id);
    return { ...c, product_name: pr ? pr.product_name : '' };
  }).sort((a, b) => b.created_at.localeCompare(a.created_at));
  const productions = DB.productions.filter(p => inRange(p.created_at)).sort((a, b) => b.created_at.localeCompare(a.created_at));
  const incomeTotal = incomes.reduce((s, r) => s + r.qty * r.price, 0);
  const outgoTotal = outgoings.reduce((s, r) => s + r.qty * r.price, 0);

  // Mijoz sotuvlari (oldi)
  const sales = DB.customer_items.filter(i => inRange(i.created_at)).map(i => {
    const c = DB.customers.find(x => x.id === i.customer_id);
    return { ...i, customer_name: c ? c.name : '' };
  }).sort((a, b) => b.created_at.localeCompare(a.created_at));
  // Mijoz to'lovlari (to'ladi)
  const custPays = DB.customer_payments.filter(p => inRange(p.created_at)).map(p => {
    const c = DB.customers.find(x => x.id === p.customer_id);
    return { ...p, customer_name: c ? c.name : '' };
  }).sort((a, b) => b.created_at.localeCompare(a.created_at));
  // Qarz to'lovlari
  const debtPays = DB.debt_payments.filter(p => inRange(p.created_at)).map(p => {
    const d = DB.debts.find(x => x.id === p.debt_id);
    return { ...p, debt_name: d ? d.name : '', debt_type: d ? d.type : '' };
  }).sort((a, b) => b.created_at.localeCompare(a.created_at));

  const salesTotal = sales.reduce((s, r) => s + (+r.umumiy || 0), 0);
  const foydaTotal = sales.reduce((s, r) => s + (+r.foyda || 0), 0);
  const custPayTotal = custPays.reduce((s, r) => s + (+r.amount || 0), 0);

  return { from: from0, to: to0, incomes, outgoings, productions, incomeTotal, outgoTotal,
    sales, custPays, debtPays, salesTotal, foydaTotal, custPayTotal };
}

api.get('/report', (req, res) => {
  res.json(buildReport(req.query.from, req.query.to));
});

// UMUMIY TARIX — hamma harakat bir joyda, sana bilan
// ISHLAB CHIQARISH TARIXI — ombor kirimlari, yasalganlar (material sarfi), mijozga chiqqan
api.get('/prod-history', (req, res) => {
  const from = req.query.from || '1970-01-01';
  const to = (req.query.to || '2999-12-31') + 'T23:59:59';
  const inRange = (d) => d && d >= from && d <= to;
  const events = [];

  // 1. Ombor kirimlari
  DB.materials.forEach(m => (m.batches || []).forEach(b => {
    if (inRange(b.created_at)) events.push({
      at: b.created_at, kind: 'in', title: m.name,
      detail: `${b.qty} ${m.unit} × ${Math.round(b.price)} so'm`,
      sum: Math.round(b.qty * b.price)
    });
  }));

  // 2. Yasalgan mahsulotlar — har biri uchun qancha material ketgani
  DB.productions.forEach(p => {
    if (!inRange(p.created_at)) return;
    const used = DB.consumptions.filter(c => c.production_id === p.id).map(c => {
      const m = DB.materials.find(x => x.id === c.material_id);
      return { name: m ? m.name : '?', qty: c.qty, unit: m ? m.unit : '' };
    });
    events.push({
      at: p.created_at, kind: 'made', title: p.product_name,
      detail: `${p.qty} dona yasaldi`,
      materials: used, sum: 0
    });
  });

  // 3. Mijozga chiqqan
  DB.customer_items.forEach(i => {
    if (!inRange(i.created_at)) return;
    const c = DB.customers.find(x => x.id === i.customer_id);
    events.push({
      at: i.created_at, kind: 'out', title: i.product_name,
      detail: `${i.qty} dona → ${c ? c.name : 'mijoz'}`,
      sum: Math.round(i.umumiy || 0)
    });
  });

  events.sort((a, b) => (b.at || '').localeCompare(a.at || ''));
  res.json({ events });
});

api.get('/history', (req, res) => {
  const from = req.query.from || '1970-01-01';
  const to = (req.query.to || '2999-12-31') + 'T23:59:59';
  const inRange = (d) => d && d >= from && d <= to;
  const ev = [];
  // material kirim
  DB.materials.forEach(m => (m.batches || []).forEach(b => {
    if (inRange(b.created_at)) ev.push({ at: b.created_at, section: 'Ombor', type: 'Material kirim',
      name: m.name, detail: `${b.qty} ${m.unit} × ${Math.round(b.price)}`, amount: Math.round(b.qty * b.price), sign: 'out',
      src: 'batch', sid: b.id, editable: true });
  }));
  // yangi mahsulot qo'shildi
  DB.products.forEach(p => { if (p.created_at && inRange(p.created_at)) ev.push({ at: p.created_at, section: 'Ishlab chiqarish',
    type: 'Yangi mahsulot', name: p.name, detail: 'mahsulot qo\'shildi', amount: 0, sign: '', src: 'product', sid: p.id }); });
  // norma (retsept) qo'shildi
  DB.recipe.forEach(r => { if (r.created_at && inRange(r.created_at)) {
    const p = DB.products.find(x => x.id === r.product_id);
    const m = DB.materials.find(x => x.id === r.material_id);
    ev.push({ at: r.created_at, section: 'Ishlab chiqarish', type: 'Norma qo\'shildi',
      name: p ? p.name : '', detail: `${m ? m.name : ''}: ${r.qty}`, amount: 0, sign: '', src: 'recipe', sid: r.id });
  } });
  // ishlab chiqarish
  DB.productions.forEach(p => { if (inRange(p.created_at)) ev.push({ at: p.created_at, section: 'Ishlab chiqarish',
    type: 'Mahsulot yasaldi', name: p.product_name, detail: `${p.qty} dona`, amount: 0, sign: '', src: 'production', sid: p.id }); });
  // mijoz sotuv
  DB.customer_items.forEach(i => { if (inRange(i.created_at)) {
    const c = DB.customers.find(x => x.id === i.customer_id);
    ev.push({ at: i.created_at, section: 'Mijozlar', type: 'Sotuv (oldi)', name: c ? c.name : '',
      detail: `${i.product_name} ${i.qty} dona`, amount: Math.round(i.umumiy || 0), sign: 'debt', src: 'sale', sid: i.id });
  } });
  // mijoz to'lov
  DB.customer_payments.forEach(p => { if (inRange(p.created_at)) {
    const c = DB.customers.find(x => x.id === p.customer_id);
    ev.push({ at: p.created_at, section: 'Mijozlar', type: 'To\'lov (kassaga)', name: c ? c.name : '',
      detail: p.note || '', amount: Math.round(p.amount || 0), sign: 'in', src: 'custpay', sid: p.id, editable: true });
  } });
  // qarz to'lovlari
  DB.debt_payments.forEach(p => { if (inRange(p.created_at)) {
    const d = DB.debts.find(x => x.id === p.debt_id);
    if (!d) return;
    ev.push({ at: p.created_at, section: d.type === 'in' ? 'Bizga qarzlar' : 'Biz qarzlar',
      type: d.type === 'in' ? 'Qarzdor to\'ladi' : 'Biz to\'ladik', name: d.name,
      detail: p.note || '', amount: Math.round(p.amount || 0), sign: d.type === 'in' ? 'in' : 'out', src: 'debtpay', sid: p.id, editable: true });
  } });
  // qo'lda kassa
  DB.kassa.forEach(k => { if (inRange(k.created_at)) ev.push({ at: k.created_at, section: 'Kassa',
    type: k.kind === 'in' ? 'Qo\'lda kirim' : 'Qo\'lda chiqim', name: k.category || '',
    detail: k.note || '', amount: Math.round(k.amount || 0), sign: k.kind, src: 'kassa', sid: k.id, editable: true }); });

  ev.sort((a, b) => (b.at || '').localeCompare(a.at || ''));
  res.json({ from: req.query.from, to: req.query.to, events: ev });
});

// TARIXDAN o'chirish — turini bilib, to'g'ri joydan o'chiradi
api.post('/history/delete', (req, res) => {
  const { src, sid, cascade } = req.body;
  const id = +sid;
  if (src === 'batch') {
    DB.materials.forEach(m => { m.batches = (m.batches || []).filter(b => b.id !== id); });
  } else if (src === 'product') {
    DB.products = DB.products.filter(p => p.id !== id);
    if (cascade) DB.recipe = DB.recipe.filter(r => r.product_id !== id);
  } else if (src === 'recipe') {
    DB.recipe = DB.recipe.filter(r => r.id !== id);
  } else if (src === 'production') {
    DB.productions = DB.productions.filter(p => p.id !== id);
    if (cascade) DB.consumptions = DB.consumptions.filter(c => c.production_id !== id);
  } else if (src === 'sale') {
    // sotuv — mijoz kartasidan o'chadi. cascade bo'lsa ombor qaytadi (consumption o'chadi)
    const item = DB.customer_items.find(i => i.id === id);
    DB.customer_items = DB.customer_items.filter(i => i.id !== id);
    // sotuvga bog'liq ishlab chiqarishni topib o'chirish qiyin — faqat yozuvni o'chiramiz
  } else if (src === 'custpay') {
    DB.customer_payments = DB.customer_payments.filter(p => p.id !== id);
  } else if (src === 'debtpay') {
    DB.debt_payments = DB.debt_payments.filter(p => p.id !== id);
  } else if (src === 'kassa') {
    DB.kassa = DB.kassa.filter(k => k.id !== id);
  } else {
    return res.status(400).json({ error: 'Noma\'lum tur' });
  }
  saveNow();
  res.json({ ok: true });
});

// TARIXDAN tahrirlash (summa/izoh)
api.post('/history/edit', (req, res) => {
  const { src, sid, amount, note } = req.body;
  const id = +sid;
  if (src === 'kassa') {
    const k = DB.kassa.find(x => x.id === id);
    if (k) { if (amount != null) k.amount = +amount || 0; if (note != null) k.note = note; }
  } else if (src === 'custpay') {
    const p = DB.customer_payments.find(x => x.id === id);
    if (p) { if (amount != null) p.amount = +amount || 0; if (note != null) p.note = note; }
  } else if (src === 'debtpay') {
    const p = DB.debt_payments.find(x => x.id === id);
    if (p) { if (amount != null) p.amount = +amount || 0; if (note != null) p.note = note; }
  } else if (src === 'batch') {
    for (const m of DB.materials) { const b = (m.batches || []).find(x => x.id === id); if (b && amount != null) { /* batch summa = qty×price, to'g'ridan-to'g'ri o'zgartirmaymiz */ } }
    return res.status(400).json({ error: 'Material kirimini Ombor bo\'limida tahrirlang' });
  } else {
    return res.status(400).json({ error: 'Bu turni tarixdan tahrirlab bo\'lmaydi' });
  }
  saveNow();
  res.json({ ok: true });
});

// Excel yasash
async function makeAtchotExcel(from, to) {
  // /atchot endpoint hisobini takrorlaymiz
  const all = buildKassa();
  const f = from || '1970-01-01';
  const t = (to || '2999-12-31') + 'T23:59:59';
  const start0 = +((DB.settings || {}).kassa_start) || 0;
  const before = all.filter(r => (r.created_at || '') < f);
  const inRange = all.filter(r => (r.created_at || '') >= f && (r.created_at || '') <= t);
  const methods = ['Naqd so\'m', 'Naqd $', 'Plastik (p)', 'UMJ perechisleniya', 'MT perechisleniya'];
  const startBal = {}; methods.forEach(m => startBal[m] = 0);
  startBal['Naqd so\'m'] += start0;
  before.forEach(r => { const m = r.method || 'Naqd so\'m'; if (startBal[m] == null) startBal[m] = 0; startBal[m] += (r.kind === 'in' ? r.amount : -r.amount); });
  const catIn = {}, catOut = {}, methIn = {}, methOut = {};
  methods.forEach(m => { methIn[m] = 0; methOut[m] = 0; });
  inRange.forEach(r => {
    const m = r.method || 'Naqd so\'m';
    if (r.kind === 'in') { catIn[r.category] = (catIn[r.category] || 0) + r.amount; methIn[m] = (methIn[m] || 0) + r.amount; }
    else { catOut[r.category] = (catOut[r.category] || 0) + r.amount; methOut[m] = (methOut[m] || 0) + r.amount; }
  });
  const endBal = {}; methods.forEach(m => endBal[m] = (startBal[m] || 0) + (methIn[m] || 0) - (methOut[m] || 0));

  const wb = new ExcelJS.Workbook();
  wb.creator = 'TEMIRCHI';
  const head = (row) => { row.font = { bold: true, color: { argb: 'FFFFFFFF' } };
    row.eachCell(c => { c.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF2B3A4A' } }; }); };

  // 1) Umumiy sarhisob
  const ws = wb.addWorksheet('Sarhisob');
  ws.columns = [{ header: 'Ko\'rsatkich', key: 'k', width: 30 }, { header: 'Summa', key: 'v', width: 20 }];
  head(ws.getRow(1));
  ws.addRow({ k: 'Davr', v: `${from || 'boshidan'} — ${to || 'hozirgacha'}` });
  ws.addRow({});
  ws.addRow({ k: '=== OY BOSHI QOLDIQ ===', v: '' }).font = { bold: true };
  methods.forEach(m => ws.addRow({ k: m, v: Math.round(startBal[m]) }));
  ws.addRow({});
  ws.addRow({ k: '=== OY OXIRI QOLDIQ ===', v: '' }).font = { bold: true };
  methods.forEach(m => ws.addRow({ k: m, v: Math.round(endBal[m]) }));
  ws.addRow({});
  const tin = Object.values(catIn).reduce((s, v) => s + v, 0);
  const tout = Object.values(catOut).reduce((s, v) => s + v, 0);
  ws.addRow({ k: 'JAMI KIRIM', v: Math.round(tin) }).font = { bold: true };
  ws.addRow({ k: 'JAMI CHIQIM', v: Math.round(tout) }).font = { bold: true };
  ws.addRow({ k: 'FOYDA (kirim-chiqim)', v: Math.round(tin - tout) }).font = { bold: true };

  // 2) Kategoriya bo'yicha kirim
  const ws2 = wb.addWorksheet('Kirimlar (kategoriya)');
  ws2.columns = [{ header: 'Kategoriya', key: 'c', width: 28 }, { header: 'Summa', key: 'v', width: 18 }];
  head(ws2.getRow(1));
  Object.entries(catIn).sort((a, b) => b[1] - a[1]).forEach(([c, v]) => ws2.addRow({ c, v: Math.round(v) }));

  // 3) Kategoriya bo'yicha chiqim
  const ws3 = wb.addWorksheet('Chiqimlar (kategoriya)');
  ws3.columns = [{ header: 'Kategoriya', key: 'c', width: 28 }, { header: 'Summa', key: 'v', width: 18 }];
  head(ws3.getRow(1));
  Object.entries(catOut).sort((a, b) => b[1] - a[1]).forEach(([c, v]) => ws3.addRow({ c, v: Math.round(v) }));

  // 4) Usul bo'yicha
  const ws4 = wb.addWorksheet('Tolov usullari');
  ws4.columns = [{ header: 'Usul', key: 'm', width: 24 }, { header: 'Kirim', key: 'i', width: 16 },
    { header: 'Chiqim', key: 'o', width: 16 }, { header: 'Oy oxiri', key: 'e', width: 16 }];
  head(ws4.getRow(1));
  methods.forEach(m => ws4.addRow({ m, i: Math.round(methIn[m] || 0), o: Math.round(methOut[m] || 0), e: Math.round(endBal[m]) }));

  return wb.xlsx.writeBuffer();
}
api.get('/atchot/excel', async (req, res) => {
  try {
    const buf = await makeAtchotExcel(req.query.from, req.query.to);
    res.setHeader('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
    res.setHeader('Content-Disposition', 'attachment; filename="TEMIRCHI_atchot.xlsx"');
    res.send(Buffer.from(buf));
  } catch (e) { res.status(500).json({ error: e.message }); }
});
api.post('/atchot/excel-to-bot', async (req, res) => {
  try {
    const parsed = verifyInitData(req.get('X-Init-Data') || '', BOT_TOKEN);
    const byKey = ACCESS_KEY && (req.get('X-Access-Key') === ACCESS_KEY);
    let chatId = OWNER_ID;
    if (parsed && parsed.user) chatId = String(parsed.user.id);
    if (!chatId) return res.status(400).json({ error: 'Qabul qiluvchi topilmadi' });
    const from = req.body.from || req.query.from, to = req.body.to || req.query.to;
    const buf = await makeAtchotExcel(from, to);
    await bot.sendDocument(chatId, Buffer.from(buf), {}, { filename: `TEMIRCHI_atchot_${from || 'all'}.xlsx`, contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    res.json({ ok: true });
  } catch (e) { res.status(500).json({ error: e.message }); }
});

async function makeReportExcel(from, to) {
  const rep = buildReport(from, to);
  const wb = new ExcelJS.Workbook();
  wb.creator = 'TEMIRCHI';

  const money = 'ozk';
  const headStyle = (row) => {
    row.font = { bold: true, color: { argb: 'FFFFFFFF' } };
    row.eachCell(c => { c.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF2B3A4A' } }; });
  };

  // 1) Joriy ombor qoldig'i
  const ws0 = wb.addWorksheet('Ombor qoldigi');
  ws0.columns = [
    { header: 'Material', key: 'name', width: 24 },
    { header: 'Birlik', key: 'unit', width: 10 },
    { header: 'Qoldiq', key: 'stock', width: 14 },
    { header: "O'rtacha narx", key: 'avg', width: 16 },
    { header: 'Qiymat', key: 'val', width: 18 },
  ];
  headStyle(ws0.getRow(1));
  DB.materials.forEach(m => {
    const st = materialStock(m.id), av = materialAvgPrice(m.id);
    ws0.addRow({ name: m.name, unit: m.unit, stock: st, avg: Math.round(av), val: Math.round(st * av) });
  });

  // 2) Kirimlar
  const ws1 = wb.addWorksheet('Kirimlar');
  ws1.columns = [
    { header: 'Sana', key: 'date', width: 14 },
    { header: 'Material', key: 'name', width: 24 },
    { header: 'Miqdor', key: 'qty', width: 12 },
    { header: 'Birlik', key: 'unit', width: 10 },
    { header: 'Narx', key: 'price', width: 14 },
    { header: 'Summa', key: 'sum', width: 18 },
  ];
  headStyle(ws1.getRow(1));
  rep.incomes.forEach(r => ws1.addRow({
    date: r.created_at.slice(0, 10), name: r.material_name, qty: r.qty, unit: r.unit,
    price: Math.round(r.price), sum: Math.round(r.qty * r.price)
  }));
  ws1.addRow({});
  const t1 = ws1.addRow({ name: 'JAMI', sum: Math.round(rep.incomeTotal) });
  t1.font = { bold: true };

  // 3) Chiqimlar (ishlatilgan)
  const ws2 = wb.addWorksheet('Chiqimlar');
  ws2.columns = [
    { header: 'Sana', key: 'date', width: 14 },
    { header: 'Material', key: 'name', width: 24 },
    { header: 'Mahsulot', key: 'prod', width: 20 },
    { header: 'Miqdor', key: 'qty', width: 12 },
    { header: 'Summa', key: 'sum', width: 18 },
  ];
  headStyle(ws2.getRow(1));
  rep.outgoings.forEach(r => ws2.addRow({
    date: r.created_at.slice(0, 10), name: r.material_name, prod: r.product_name,
    qty: r.qty, sum: Math.round(r.qty * r.price)
  }));
  ws2.addRow({});
  const t2 = ws2.addRow({ name: 'JAMI', sum: Math.round(rep.outgoTotal) });
  t2.font = { bold: true };

  // 4) Ishlab chiqarish
  const ws3 = wb.addWorksheet('Ishlab chiqarish');
  ws3.columns = [
    { header: 'Sana', key: 'date', width: 14 },
    { header: 'Mahsulot', key: 'prod', width: 24 },
    { header: 'Soni', key: 'qty', width: 12 },
  ];
  headStyle(ws3.getRow(1));
  rep.productions.forEach(p => ws3.addRow({ date: p.created_at.slice(0, 10), prod: p.product_name, qty: p.qty }));

  // 5) Mijoz sotuvlari (oldi)
  const ws4 = wb.addWorksheet('Mijoz sotuvlar');
  ws4.columns = [
    { header: 'Sana', key: 'date', width: 14 },
    { header: 'Mijoz', key: 'cust', width: 22 },
    { header: 'Mahsulot', key: 'prod', width: 20 },
    { header: 'Soni', key: 'qty', width: 10 },
    { header: 'Narx', key: 'price', width: 14 },
    { header: 'Umumiy', key: 'sum', width: 16 },
    { header: 'Tannarx', key: 'cost', width: 16 },
    { header: 'Foyda', key: 'foyda', width: 16 },
  ];
  headStyle(ws4.getRow(1));
  rep.sales.forEach(r => ws4.addRow({
    date: r.created_at.slice(0, 10), cust: r.customer_name, prod: r.product_name,
    qty: r.qty, price: Math.round(r.price || 0), sum: Math.round(r.umumiy || 0),
    cost: Math.round(r.cost || 0), foyda: Math.round(r.foyda || 0)
  }));
  ws4.addRow({});
  const t4 = ws4.addRow({ prod: 'JAMI', sum: Math.round(rep.salesTotal), foyda: Math.round(rep.foydaTotal) });
  t4.font = { bold: true };

  // 6) Mijoz to'lovlari (to'ladi)
  const ws5 = wb.addWorksheet('Mijoz tolovlar');
  ws5.columns = [
    { header: 'Sana', key: 'date', width: 14 },
    { header: 'Mijoz', key: 'cust', width: 22 },
    { header: 'Summa', key: 'sum', width: 16 },
    { header: 'Izoh', key: 'note', width: 24 },
  ];
  headStyle(ws5.getRow(1));
  rep.custPays.forEach(r => ws5.addRow({
    date: r.created_at.slice(0, 10), cust: r.customer_name, sum: Math.round(r.amount || 0), note: r.note || ''
  }));
  ws5.addRow({});
  const t5 = ws5.addRow({ cust: 'JAMI', sum: Math.round(rep.custPayTotal) });
  t5.font = { bold: true };

  // 7) Qarz to'lovlari
  const ws6 = wb.addWorksheet('Qarz tolovlar');
  ws6.columns = [
    { header: 'Sana', key: 'date', width: 14 },
    { header: 'Tur', key: 'type', width: 16 },
    { header: 'Kim', key: 'name', width: 22 },
    { header: 'Summa', key: 'sum', width: 16 },
    { header: 'Izoh', key: 'note', width: 24 },
  ];
  headStyle(ws6.getRow(1));
  rep.debtPays.forEach(r => ws6.addRow({
    date: r.created_at.slice(0, 10),
    type: r.debt_type === 'in' ? 'Bizga qarzdor' : 'Biz qarzdor',
    name: r.debt_name, sum: Math.round(r.amount || 0), note: r.note || ''
  }));

  // 8) Kassa (pul harakati)
  const ws7 = wb.addWorksheet('Kassa');
  ws7.columns = [
    { header: 'Sana', key: 'date', width: 14 },
    { header: 'Turi', key: 'kind', width: 12 },
    { header: 'Kategoriya', key: 'cat', width: 20 },
    { header: 'Kim/nima', key: 'name', width: 22 },
    { header: 'Summa', key: 'sum', width: 16 },
    { header: 'Izoh', key: 'note', width: 22 },
  ];
  headStyle(ws7.getRow(1));
  const kassaAll = buildKassa().filter(r => {
    const d = r.created_at || '';
    return d >= (from || '1970-01-01') && d <= (to || '2999-12-31T23:59:59');
  });
  kassaAll.forEach(r => ws7.addRow({
    date: (r.created_at || '').slice(0, 10),
    kind: r.kind === 'in' ? 'Kirim +' : 'Chiqim −',
    cat: r.category, name: r.name || '', sum: Math.round(r.amount || 0), note: r.note || ''
  }));
  const kin = kassaAll.filter(r => r.kind === 'in').reduce((s, r) => s + r.amount, 0);
  const kout = kassaAll.filter(r => r.kind === 'out').reduce((s, r) => s + r.amount, 0);
  ws7.addRow({});
  const t7 = ws7.addRow({ cat: 'JAMI KIRIM', sum: Math.round(kin) });
  t7.font = { bold: true };
  const t7b = ws7.addRow({ cat: 'JAMI CHIQIM', sum: Math.round(kout) });
  t7b.font = { bold: true };
  const t7c = ws7.addRow({ cat: 'QOLDIQ (kirim−chiqim)', sum: Math.round(kin - kout) });
  t7c.font = { bold: true };

  return wb.xlsx.writeBuffer();
}

// Excelni Telegram botga yuborish
api.post('/report/excel-to-bot', async (req, res) => {
  try {
    if (!botInstance) return res.status(400).json({ error: 'Bot ishlamayapti' });
    // kimga yuborish: Telegram foydalanuvchi (Mini App) yoki egaga
    let chatId = null;
    const parsed = verifyInitData(req.get('X-Init-Data') || '', BOT_TOKEN);
    if (parsed && parsed.user) chatId = parsed.user.id;
    else if (OWNER_ID) chatId = OWNER_ID; // brauzerdan (ACCESS_KEY) kelsa — egaga
    if (!chatId) return res.status(400).json({ error: 'Qabul qiluvchi topilmadi' });

    const from = req.body.from || req.query.from;
    const to = req.body.to || req.query.to;
    const buf = await makeReportExcel(from, to);
    const fname = `TEMIRCHI_hisobot_${(from || 'boshidan')}_${(to || 'oxirigacha')}.xlsx`;
    await botInstance.sendDocument(chatId, Buffer.from(buf), {
      caption: `📊 Hisobot\n${from || '—'} — ${to || '—'}`
    }, { filename: fname, contentType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    res.json({ ok: true });
  } catch (e) {
    console.error('Excel xato:', e.message);
    res.status(500).json({ error: 'Excel yasashda xato: ' + e.message });
  }
});

// Excelni to'g'ridan-to'g'ri yuklab olish (brauzer uchun)
api.get('/report/excel', async (req, res) => {
  try {
    const buf = await makeReportExcel(req.query.from, req.query.to);
    res.setHeader('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
    res.setHeader('Content-Disposition', `attachment; filename="TEMIRCHI_hisobot.xlsx"`);
    res.send(Buffer.from(buf));
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.use('/api', api);
app.use(express.static(__dirname));
app.get('*', (req, res) => res.sendFile(path.join(__dirname, 'index.html')));

app.listen(PORT, () => {
  console.log(`TEMIRCHI server ishlayapti: ${PORT}`);
  if (BOT_TOKEN) initBot(BOT_TOKEN);
  else console.log('BOT_TOKEN yo\'q — bot ishga tushmadi (faqat web).');
});

process.on('SIGTERM', () => { saveNow(); process.exit(0); });
process.on('SIGINT', () => { saveNow(); process.exit(0); });
