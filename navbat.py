# navbat.py
import asyncio
import logging
import os
import sys
import sqlite3
import uuid
from datetime import date, timedelta, datetime
from typing import Optional, Tuple, List

# Early Python version check
if sys.version_info >= (3, 13):
    print("Error: Python 3.13+ may be incompatible with the installed aiogram/pydantic versions.")
    print("Please run this bot with Python 3.10 - 3.12 (e.g. create a venv with python3.11).")
    raise SystemExit(1)

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

# ====== CONFIG ======
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_FILE = "queue.db"

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN topilmadi. .env faylga BOT_TOKEN=... kiriting.")

# ====== DB INIT ======
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
conn.execute("PRAGMA journal_mode=WAL;")
conn.execute("PRAGMA foreign_keys=ON;")
c = conn.cursor()

# Jadval strukturasini yaratish
c.execute(
    """
CREATE TABLE IF NOT EXISTS providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    ref_code TEXT UNIQUE NOT NULL
)
"""
)
c.execute(
    """
CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    UNIQUE(provider_id, name),
    FOREIGN KEY(provider_id) REFERENCES providers(id) ON DELETE CASCADE
)
"""
)
c.execute(
    """
CREATE TABLE IF NOT EXISTS queues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    service_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    date TEXT,
    time TEXT,
    FOREIGN KEY(provider_id) REFERENCES providers(id) ON DELETE CASCADE,
    FOREIGN KEY(service_id) REFERENCES services(id) ON DELETE CASCADE
)
"""
)
c.execute(
    """
CREATE TABLE IF NOT EXISTS busy_times (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    FOREIGN KEY(provider_id) REFERENCES providers(id) ON DELETE CASCADE
)
"""
)

# Eski jadvallarni yangilash (agar kerak bo'lsa)
c.execute("PRAGMA table_info(queues)")
columns = [col[1] for col in c.fetchall()]

if 'time' not in columns:
    print("Jadvalni yangilash: time ustuni qo'shilyapti...")
    c.execute("ALTER TABLE queues ADD COLUMN time TEXT")

if 'date' not in columns:
    print("Jadvalni yangilash: date ustuni qo'shilyapti...")
    c.execute("ALTER TABLE queues ADD COLUMN date TEXT")

conn.commit()

# ====== BOT INIT ======
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ====== FSM STATES ======
class RegisterProvider(StatesGroup):
    name = State()

class AddService(StatesGroup):
    name = State()

class BusyTimeStates(StatesGroup):
    date = State()
    time = State()

# ====== HELPERS (DB wrappers) ======
def fetchone(q: str, params: tuple = ()) -> Optional[tuple]:
    cur = conn.execute(q, params)
    return cur.fetchone()

def fetchall(q: str, params: tuple = ()) -> List[tuple]:
    cur = conn.execute(q, params)
    return cur.fetchall()

def execute(q: str, params: tuple = ()) -> None:
    conn.execute(q, params)
    conn.commit()

def create_provider(owner_id: int, name: str) -> str:
    ref_code = str(uuid.uuid4())[:8]
    execute(
    "INSERT INTO providers(owner_id, name, ref_code) VALUES (?,?,?)",
    (owner_id, name, ref_code),
    )
    return ref_code

def get_provider_by_ref(ref_code: str) -> Optional[Tuple[int, str]]:
    return fetchone("SELECT id, name FROM providers WHERE ref_code=?", (ref_code,))

def get_provider_by_owner(owner_id: int) -> Optional[Tuple[int, str, str]]:
    return fetchone("SELECT id, name, ref_code FROM providers WHERE owner_id=?", (owner_id,))

def get_services(provider_id: int) -> List[Tuple[int, str]]:
    return fetchall("SELECT id, name FROM services WHERE provider_id=? ORDER BY id", (provider_id,))

def add_service(provider_id: int, name: str) -> bool:
    try:
        execute("INSERT INTO services(provider_id, name) VALUES(?,?)", (provider_id, name))
        return True
    except sqlite3.IntegrityError:
        return False

def add_to_queue(provider_id: int, user_id: int, service_id: int, date: Optional[str] = None, time: Optional[str] = None) -> int:
    if date and time:
        execute(
            "INSERT INTO queues(provider_id, user_id, service_id, position, date, time) VALUES(?,?,?,?,?,?)",
            (provider_id, user_id, service_id, 0, date, time),
        )
        return 0
    else:
        row = fetchone("SELECT COALESCE(MAX(position),0) FROM queues WHERE provider_id=? AND position>0", (provider_id,))
        next_pos = (row[0] if row else 0) + 1
        execute(
            "INSERT INTO queues(provider_id, user_id, service_id, position, date, time) VALUES(?,?,?,?,NULL,NULL)",
            (provider_id, user_id, service_id, next_pos),
        )
        return next_pos

def pop_next_in_queue(provider_id: int, include_scheduled: bool = False):
    if include_scheduled:
        # Band qilingan vaqtlardan keyingisini topish
        row = fetchone(
            "SELECT id, user_id, service_id, date, time FROM queues WHERE provider_id=? ORDER BY date, time ASC LIMIT 1",
            (provider_id,),
        )
    else:
        # Faqat navbatdagilarni (position > 0) ko'rish
        row = fetchone(
            "SELECT id, user_id, service_id FROM queues WHERE provider_id=? AND position>0 ORDER BY position ASC LIMIT 1",
            (provider_id,),
        )
    
    if row:
        if include_scheduled:
            qid, user_id, service_id, date_val, time_val = row
        else:
            qid, user_id, service_id = row
            
        execute("DELETE FROM queues WHERE id=?", (qid,))
        
        if include_scheduled:
            return user_id, service_id, date_val, time_val
        else:
            return user_id, service_id
            
    return None

def get_queue(provider_id: int) -> List[Tuple[int, int, str]]:
    rows = fetchall(
        """
        SELECT q.position, q.user_id, s.name 
        FROM queues q 
        JOIN services s ON q.service_id = s.id 
        WHERE q.provider_id=? AND q.position > 0 
        ORDER BY q.position
        """, 
        (provider_id,)
    )
    return rows

def delete_provider(provider_id: int) -> None:
    execute("DELETE FROM providers WHERE id=?", (provider_id,))

def get_provider_owner(provider_id: int) -> Optional[int]:
    r = fetchone("SELECT owner_id FROM providers WHERE id=?", (provider_id,))
    return r[0] if r else None

def add_busy_time(provider_id: int, date: str, time: str) -> int:
    execute("INSERT INTO busy_times(provider_id, date, time) VALUES(?,?,?)", (provider_id, date, time))
    row = fetchone("SELECT last_insert_rowid()")
    return row[0] if row else 0

def get_busy_times(provider_id: int):
    return fetchall("SELECT id, date, time FROM busy_times WHERE provider_id=? ORDER BY date,time", (provider_id,))

def remove_busy_time(busy_id: int) -> None:
    execute("DELETE FROM busy_times WHERE id=?", (busy_id,))

def get_booked_slots(provider_id: int, date: str) -> List[str]:
    booked = [r[0] for r in fetchall("SELECT time FROM queues WHERE provider_id=? AND date=? AND time IS NOT NULL", (provider_id, date))]
    busy = [r[2] for r in fetchall("SELECT id, date, time FROM busy_times WHERE provider_id=? AND date=?", (provider_id, date))]
    return list(set(booked + busy))

# ====== KEYBOARDS ======
def start_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Roʻyxatdan oʻtish (salon/usta)")],
        ],
        resize_keyboard=True,
    )

def provider_main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Mening profilim"), KeyboardButton(text="🔗 Mening havolam")],
            [KeyboardButton(text="➕ Xizmat qoʻshish"), KeyboardButton(text="📋 Xizmatlarim")],
            [KeyboardButton(text="👥 Navbatdagilar"), KeyboardButton(text="📢 Keyingi mijozni chaqirish")],
            [KeyboardButton(text="⏳ Band vaqt qoʻshish"), KeyboardButton(text="🗓 Band vaqtlarni koʻrish")],
            [KeyboardButton(text="❌ Navbatni boʻshatish"), KeyboardButton(text="🚪 Roʻyxatdan chiqish")],
        ],
        resize_keyboard=True,
    )

# ====== HANDLERS ======
@dp.message(CommandStart())
async def on_start(message: Message, state: FSMContext):
    args = message.text.split()
    payload = args[1] if len(args) > 1 else ""
    
    if payload:
        provider = get_provider_by_ref(payload)
        if not provider:
            await message.answer("❌ Xizmat ko'rsatuvchi topilmadi.")
            return
        provider_id, provider_name = provider
        services = get_services(provider_id)
        if not services:
            await message.answer(f"ℹ️ {provider_name} hozircha xizmatlar qoʻshmagan.")
            return
        ikb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=name, callback_data=f"svc:{sid}")] for sid, name in services
            ]
        )
        await message.answer(f"👋 *{provider_name}* — xizmatni tanlang:", reply_markup=ikb, parse_mode="Markdown")
        return

    prov = get_provider_by_owner(message.from_user.id)
    if prov:
        pid, name, ref = prov
        link = f"https://t.me/{(await bot.get_me()).username}?start={ref}"
        await message.answer("👋 Xush kelibsiz! Quyidagi menyudan foydalaning:", reply_markup=provider_main_kb())
        await message.answer(f"🔗 Sizning taklif havolangiz:\n{link}")
    else:
        await message.answer(
            "👋 Xush kelibsiz! Agar salon/usta sifatida roʻyxatdan oʻtmoqchi boʻlsangiz, pastdagi tugmani bosing.",
            reply_markup=start_keyboard(),
        )

@dp.message(F.text == "📝 Roʻyxatdan oʻtish (salon/usta)")
async def register_start(message: Message, state: FSMContext):
    if get_provider_by_owner(message.from_user.id):
        await message.answer("❌ Siz allaqon roʻyxatdan oʻtgansiz. Profilni koʻrish uchun \"👤 Mening profilim\" tugmasini bosing.")
        return
    await state.set_state(RegisterProvider.name)
    await message.answer("✍️ Salon yoki ustaxona nomini yuboring:")

@dp.message(RegisterProvider.name)
async def register_finish(message: Message, state: FSMContext):
    name = message.text.strip()
    if not name:
        await message.answer("❗ Nom boʻsh boʻlishi mumkin emas. Iltimos, qayta yuboring.")
        return
    ref = create_provider(message.from_user.id, name)
    link = f"https://t.me/{(await bot.get_me()).username}?start={ref}"
    await state.clear()
    await message.answer(
        f"✅ Tabriklaymiz! Siz roʻyxatdan oʻtdingiz: *{name}*\n🔗 Mijozlaringiz uchun havola:\n{link}",
        parse_mode="Markdown",
        reply_markup=provider_main_kb(),
    )

@dp.message(F.text == "🔗 Mening havolam")
async def send_my_link(message: Message):
    p = get_provider_by_owner(message.from_user.id)
    if not p:
        await message.answer("❌ Avval roʻyxatdan oʻting.")
        return
    _, name, ref = p
    link = f"https://t.me/{(await bot.get_me()).username}?start={ref}"
    await message.answer(f"🔗 Havola ({name}):\n{link}")

@dp.message(F.text == "👤 Mening profilim")
async def show_profile(message: Message):
    p = get_provider_by_owner(message.from_user.id)
    if not p:
        await message.answer("❌ Avval roʻyxatdan oʻting.")
        return
    pid, name, ref = p
    services = get_services(pid)
    svc_text = "\n".join([f"• {sname}" for _, sname in services]) or "— Xizmatlar qoʻshilmagan"
    link = f"https://t.me/{(await bot.get_me()).username}?start={ref}"
    await message.answer(f"👤 *{name}*\n\n📋 Xizmatlar:\n{svc_text}\n\n🔗 Havola:\n{link}", parse_mode="Markdown")

@dp.message(F.text == "➕ Xizmat qoʻshish")
async def add_service_start(message: Message, state: FSMContext):
    p = get_provider_by_owner(message.from_user.id)
    if not p:
        await message.answer("❌ Avval roʻyxatdan oʻting.")
        return
    await state.set_state(AddService.name)
    await message.answer("✍️ Xizmat nomini yuboring (masalan: Soch olish):")

@dp.message(AddService.name)
async def add_service_save(message: Message, state: FSMContext):
    p = get_provider_by_owner(message.from_user.id)
    if not p:
        await state.clear()
        await message.answer("❌ Avval roʻyxatdan oʻting.")
        return
    provider_id, _, _ = p
    svc_name = message.text.strip()
    if not svc_name:
        await message.answer("❗ Boʻsh nom qabul qilinmaydi.")
        return
    ok = add_service(provider_id, svc_name)
    await state.clear()
    if ok:
        await message.answer(f"✅ Xizmat qoʻshildi: {svc_name}", reply_markup=provider_main_kb())
    else:
        await message.answer(f"ℹ️ '{svc_name}' allaqon mavjud.", reply_markup=provider_main_kb())

@dp.message(F.text == "📋 Xizmatlarim")
async def list_services(message: Message):
    p = get_provider_by_owner(message.from_user.id)
    if not p:
        await message.answer("❌ Avval roʻyxatdan oʻting.")
        return
    pid, name, _ = p
    rows = get_services(pid)
    if not rows:
        await message.answer("❌ Hali xizmat qoʻshilmagan.")
        return
    text = "\n".join([f"• {nm}" for _, nm in rows])
    await message.answer(f"💼 {name} — xizmatlar:\n{text}")

# 👥 Navbatdagilar tugmasi uchun handler
@dp.message(F.text == "👥 Navbatdagilar")
async def show_queue(message: Message):
    provider = get_provider_by_owner(message.from_user.id)
    if not provider:
        await message.answer("❌ Siz hali ro'yxatdan o'tmagansiz.")
        return

    provider_id, provider_name, _ = provider
    queue_rows = fetchall(
        "SELECT user_id, service_id, position, date, time FROM queues WHERE provider_id=? ORDER BY position ASC, date, time ASC",
        (provider_id,),
    )

    if not queue_rows:
        await message.answer("📭 Navbat boʻsh.")
        return

    text = f"👥 {provider_name} — navbatdagilar:\n\n"
    for user_id, service_id, pos, date_val, time_val in queue_rows:
        service = fetchone("SELECT name FROM services WHERE id=?", (service_id,))
        service_name = service[0] if service else "❓ Noma'lum xizmat"
        
        try:
            user = await bot.get_chat(user_id)
            user_name = user.full_name or user.first_name or f"User {user_id}"
        except Exception:
            user_name = f"User {user_id}"

        if pos > 0:
            text += f"{pos}. {user_name} — {service_name}\n"
        else:
            text += f"📅 {date_val} {time_val} — {user_name} — {service_name}\n"

    await message.answer(text)

@dp.message(F.text == "📢 Keyingi mijozni chaqirish")
async def call_next(message: Message):
    p = get_provider_by_owner(message.from_user.id)
    if not p:
        await message.answer("❌ Avval roʻyxatdan oʻting.")
        return
    pid, name, _ = p
    
    # Avval navbatdagilarni tekshiramiz
    nxt = pop_next_in_queue(pid, include_scheduled=False)
    
    # Agar navbat bo'sh bo'lsa, band qilingan vaqtlarni tekshiramiz
    if not nxt:
        nxt = pop_next_in_queue(pid, include_scheduled=True)
        if not nxt:
            await message.answer("📭 Navbat boʻsh.")
            return
            
        user_id, service_id, date_val, time_val = nxt
        row = fetchone("SELECT name FROM services WHERE id=?", (service_id,))
        service_name = row[0] if row else "Xizmat"
        
        try:
            await bot.send_message(user_id, f"📢 Sizning band qilgan vaqtingiz keldi! Xizmat: {service_name}, Vaqt: {date_val} {time_val}")
        except Exception:
            pass
        await message.answer(f"✅ Band qilingan mijoz chaqirildi: {date_val} {time_val}")
    else:
        user_id, service_id = nxt
        row = fetchone("SELECT name FROM services WHERE id=?", (service_id,))
        service_name = row[0] if row else "Xizmat"
        
        try:
            await bot.send_message(user_id, f"📢 Sizning navbatingiz keldi! Xizmat: {service_name}")
        except Exception:
            pass
        await message.answer("✅ Keyingi mijoz chaqirildi.")

@dp.message(F.text == "❌ Navbatni boʻshatish")
async def clear_queue(message: Message):
    p = get_provider_by_owner(message.from_user.id)
    if not p:
        await message.answer("❌ Avval roʻyxatdan oʻting.")
        return
    pid, _, _ = p
    execute("DELETE FROM queues WHERE provider_id=?", (pid,))
    await message.answer("🗑️ Navbat tozalandi.")

@dp.message(F.text == "🚪 Roʻyxatdan chiqish")
async def unregister(message: Message):
    p = get_provider_by_owner(message.from_user.id)
    if not p:
        await message.answer("❌ Siz roʻyxatdan oʻtmagansiz.")
        return
    pid, _, _ = p
    delete_provider(pid)
    await message.answer("🚪 Siz muvaffaqiyatli roʻyxatdan chiqdingiz. Agar qayta roʻyxatdan oʻtmoqchi boʻlsangiz, /start ni bosing.", reply_markup=start_keyboard())

@dp.message(F.text == "⏳ Band vaqt qoʻshish")
async def admin_busy_start(message: Message, state: FSMContext):
    p = get_provider_by_owner(message.from_user.id)
    if not p:
        await message.answer("❌ Avval roʻyxatdan oʻting.")
        return
    await state.set_state(BusyTimeStates.date)
    await message.answer("Qaysi sanani band qilmoqchisiz? (YYYY-MM-DD)")

@dp.message(BusyTimeStates.date)
async def admin_busy_date(message: Message, state: FSMContext):
    txt = message.text.strip()
    try:
        dt = datetime.strptime(txt, "%Y-%m-%d")
    except Exception:
        await message.answer("Sana format notoʻgʻri. Iltimos: 2025-08-25 shaklida yuboring.")
        return
    await state.update_data(date=txt)
    await state.set_state(BusyTimeStates.time)
    await message.answer("Qaysi vaqtni band qilmoqchisiz? (HH:MM)")

@dp.message(BusyTimeStates.time)
async def admin_busy_time(message: Message, state: FSMContext):
    txt = message.text.strip()
    try:
        h, m = map(int, txt.split(":"))
        assert 0 <= h < 24 and 0 <= m < 60
        time_str = f"{h:02d}:{m:02d}"
    except Exception:
        await message.answer("Vaqt format notoʻgʻri. Iltimos: 10:00 shaklida yuboring.")
        return
    data = await state.get_data()
    date_val = data.get("date")
    p = get_provider_by_owner(message.from_user.id)
    if not p:
        await state.clear()
        await message.answer("❌ Avval roʻyxatdan oʻting.")
        return
    pid, _, _ = p
    add_busy_time(pid, date_val, time_str)
    await state.clear()
    await message.answer(f"✅ {date_val} {time_str} band qilindi.", reply_markup=provider_main_kb())

@dp.message(F.text == "🗓 Band vaqtlarni koʻrish")
async def admin_list_busy(message: Message):
    p = get_provider_by_owner(message.from_user.id)
    if not p:
        await message.answer("❌ Avval roʻyxatdan oʻting.")
        return
    pid, name, _ = p
    rows = get_busy_times(pid)
    if not rows:
        await message.answer("ℹ️ Hech qanday band vaqt yoʻq.")
        return
    ikb_rows = []
    lines = []
    for busy_id, date_val, time_val in rows:
        lines.append(f"• {date_val} {time_val} (id:{busy_id})")
        ikb_rows.append([InlineKeyboardButton(text=f"❌ {date_val} {time_val}", callback_data=f"delbusy:{busy_id}:{pid}")])
    await message.answer("🗓 Band vaqtlar:\n" + "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=ikb_rows))

@dp.callback_query(F.data.startswith("delbusy:"))
async def admin_delete_busy(cb: CallbackQuery):
    try:
        _, busy_id, pid = cb.data.split(":")
        busy_id = int(busy_id); pid = int(pid)
    except Exception:
        await cb.answer("Xato", show_alert=True); return
    owner = get_provider_owner(pid)
    if owner != cb.from_user.id:
        await cb.answer("Ruxsat yoʻq", show_alert=True); return
    remove_busy_time(busy_id)
    await cb.answer("Oʻchirildi")
    await admin_list_busy(cb.message)

def next_n_dates(n=7):
    today = date.today()
    return [(today + timedelta(days=i)).isoformat() for i in range(n)]

@dp.callback_query(F.data.startswith("svc:"))
async def client_pick_service(cb: CallbackQuery):
    try:
        svc_id = int(cb.data.split(":", 1)[1])
    except Exception:
        await cb.answer("Xato", show_alert=True); return
    row = fetchone("SELECT provider_id, name FROM services WHERE id=?", (svc_id,))
    if not row:
        await cb.answer("Xizmat topilmadi", show_alert=True); return
    provider_id, svc_name = row
    dates = next_n_dates(7)
    ikb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=d, callback_data=f"date:{provider_id}:{svc_id}:{d}")] for d in dates
    ])
    await cb.message.edit_text(f"📅 {svc_name} — sana tanlang:", reply_markup=ikb)
    await cb.answer()

@dp.callback_query(F.data.startswith("date:"))
async def client_pick_date(cb: CallbackQuery):
    try:
        _, provider_id, svc_id, sel_date = cb.data.split(":", 3)
        provider_id = int(provider_id); svc_id = int(svc_id)
    except Exception:
        await cb.answer("Xato", show_alert=True); return
    slots = []
    for h in range(9, 18):
        slots.append(f"{h:02d}:00")
        slots.append(f"{h:02d}:30")
    slots = slots[:-1]
    blocked = get_booked_slots(provider_id, sel_date)
    available = [s for s in slots if s not in blocked]
    if not available:
        await cb.answer("Bu kunda bo'sh vaqt topilmadi", show_alert=True)
        await cb.message.edit_text("📅 Tanlangan kunda boʻsh slotlar yoʻq.")
        return
    ikb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=f"book:{provider_id}:{svc_id}:{sel_date}:{t}")] for t in available
    ])
    await cb.message.edit_text(f"⏰ {sel_date} — mavjud vaqtlar:", reply_markup=ikb)
    await cb.answer()

@dp.callback_query(F.data.startswith("book:"))
async def client_book_slot(cb: CallbackQuery):
    try:
        _, provider_id, svc_id, sel_date, sel_time = cb.data.split(":", 4)
        provider_id = int(provider_id); svc_id = int(svc_id)
    except Exception:
        await cb.answer("Xato", show_alert=True); return
    blocked = get_booked_slots(provider_id, sel_date)
    if sel_time in blocked:
        await cb.answer("Bu vaqt allaqon band. Iltimos boshqa vaqtni tanlang.", show_alert=True)
        return
    add_to_queue(provider_id, cb.from_user.id, svc_id, date=sel_date, time=sel_time)
    owner = get_provider_owner(provider_id)
    svc_row = fetchone("SELECT name FROM services WHERE id=?", (svc_id,))
    svc_name = svc_row[0] if svc_row else "Xizmat"
    try:
        if owner:
            await bot.send_message(owner, f"🔔 Yangi buyurtma: {cb.from_user.full_name} — {svc_name} ({sel_date} {sel_time})")
    except Exception:
        pass
    await cb.message.edit_text(f"✅ Siz {svc_name} uchun {sel_date} {sel_time} ga buyurtma qildingiz.")
    await cb.answer()

# ====== MAIN ENTRY ======
async def main():
    import time
    max_retries = 5
    retry_delay = 10  # seconds
    
    for attempt in range(max_retries):
        try:
            print(f"Botni ishga tushirish urinishi {attempt + 1}/{max_retries}...")
            await dp.start_polling(bot)
            break
        except Exception as e:
            print(f"Xato: {e}")
            if attempt < max_retries - 1:
                print(f"{retry_delay} soniyadan keyin qayta uriniladi...")
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                print("Barcha urinishlar muvaffaqiyatsiz tugadi.")
                raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot to'xtatildi.")