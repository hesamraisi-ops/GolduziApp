import aiosqlite
import jdatetime
import os
import shutil
import jwt
import hashlib
import re
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, File, UploadFile, HTTPException, Depends, Header, Form
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Literal, Optional, List
from contextlib import asynccontextmanager

SECRET_KEY = "super_secret_golduzi_key_2026"
ALGORITHM = "HS256"
DB_NAME = "app_database.db"
PHOTO_DIR = "app_photos"

if not os.path.exists(PHOTO_DIR):
    os.makedirs(PHOTO_DIR)

def hash_password(password: str):
    return hashlib.sha256(password.encode()).hexdigest()

def get_day_name(date_str: str) -> str:
    try:
        ds = clean_date(date_str)
        y, m, d = map(int, ds.split('-'))
        days = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]
        return days[jdatetime.date(y, m, d).weekday()]
    except:
        return ""

def clean_date(d: str) -> str:
    if not d: return ""
    d = str(d)
    d = d.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    d = re.sub(r'[/\.]', '-', d.strip())
    parts = d.split('-')
    if len(parts) == 3:
        try: return f"{int(parts[0])}-{int(parts[1]):02d}-{int(parts[2]):02d}"
        except: pass
    return d

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        await db.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT UNIQUE, password_hash TEXT, 
            name TEXT, role TEXT DEFAULT 'operator', shift_type TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS shifts
                     (id INTEGER PRIMARY KEY, operator_id INTEGER, operator_name TEXT, 
                      date TEXT, shift_type TEXT, machine TEXT, counter INTEGER, 
                      design_code TEXT, set_number INTEGER, parts TEXT, photo_path TEXT,
                      status TEXT, zero_reason TEXT, admin_note TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS machines
                     (name TEXT PRIMARY KEY, reset_period INTEGER, start_counter INTEGER DEFAULT 0)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS monthly_prices
                     (year INTEGER, month INTEGER, price REAL, chador_price REAL DEFAULT 0, show_salary INTEGER, show_opposite_salary INTEGER DEFAULT 0, PRIMARY KEY (year, month))''')
        await db.execute('''CREATE TABLE IF NOT EXISTS monthly_shift_financials
                     (year INTEGER, month INTEGER, operator_name TEXT, shift_type TEXT, reward INTEGER DEFAULT 0, reward_title TEXT, penalty INTEGER DEFAULT 0, penalty_title TEXT, PRIMARY KEY (year, month, operator_name, shift_type))''')
        await db.execute('''CREATE TABLE IF NOT EXISTS monthly_machine_financials
                     (year INTEGER, month INTEGER, operator_name TEXT, shift_type TEXT, machine TEXT, reward INTEGER DEFAULT 0, reward_title TEXT, penalty INTEGER DEFAULT 0, penalty_title TEXT, PRIMARY KEY (year, month, operator_name, shift_type, machine))''')
        
        try: await db.execute("ALTER TABLE shifts ADD COLUMN zero_note TEXT;")
        except: pass
        try: await db.execute("ALTER TABLE shifts ADD COLUMN jig_bonus INTEGER DEFAULT 0;")
        except: pass
        try: await db.execute("ALTER TABLE shifts ADD COLUMN is_chador INTEGER DEFAULT 0;")
        except: pass
        try: await db.execute("ALTER TABLE shifts ADD COLUMN chador_count INTEGER DEFAULT 0;")
        except: pass
        try: await db.execute("ALTER TABLE shifts ADD COLUMN chador_invoice TEXT;")
        except: pass
        try: await db.execute("ALTER TABLE monthly_prices ADD COLUMN chador_price REAL DEFAULT 0;")
        except: pass
        try: await db.execute("ALTER TABLE monthly_prices ADD COLUMN show_opposite_salary INTEGER DEFAULT 0;")
        except: pass
        
        await db.execute("INSERT OR IGNORE INTO machines (name, reset_period, start_counter) VALUES ('10 کله', 5000000, 0)")
        await db.execute("INSERT OR IGNORE INTO machines (name, reset_period, start_counter) VALUES ('12 کله', 5000000, 0)")
        await db.execute("INSERT OR IGNORE INTO machines (name, reset_period, start_counter) VALUES ('چادری', 5000000, 0)")
        
        admin_pwd = hash_password("2864")
        await db.execute("INSERT OR IGNORE INTO users (phone, password_hash, name, role, shift_type) VALUES (?, ?, ?, ?, ?)",
                         ("09120143502", admin_pwd, "مدیر کل", "admin", None))
        await db.commit()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(title="Embroidery Workshop API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/app_photos", StaticFiles(directory=PHOTO_DIR), name="app_photos")

class LoginData(BaseModel):
    phone_number: str
    password: str

class CreateUserData(BaseModel):
    phone_number: str
    name: str
    role: Literal["admin", "operator"]
    password: str = Field(..., min_length=4)
    shift_type: Optional[Literal["صبح", "شب"]] = None

class UpdateUserData(BaseModel):
    name: str
    shift_type: Optional[Literal["صبح", "شب"]] = None
    password: Optional[str] = None

class MachineData(BaseModel):
    name: str
    reset_period: int = 5000000
    start_counter: int = 0

class UpdateMachineData(BaseModel):
    reset_period: int
    start_counter: int

def normalize_phone(phone: str) -> str:
    phone = phone.replace(" ", "").replace("-", "")
    if phone.startswith("+98"): return "0" + phone[3:]
    if phone.startswith("98"): return "0" + phone[2:]
    return phone

def verify_token(authorization: str = Header(...)):
    try:
        token = authorization.split(" ")[1]
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except:
        raise HTTPException(status_code=401, detail="نشست شما منقضی شده است.")

def verify_admin(payload: dict = Depends(verify_token)):
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="دسترسی مدیریتی ندارید.")
    return payload

async def get_machine_info(machine_name):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT reset_period, start_counter FROM machines WHERE name=?", (machine_name,)) as cursor:
            row = await cursor.fetchone()
            return row if row else (5000000, 0)

async def get_immediate_previous_shift(machine, date_str, shift_type):
    target_date = clean_date(date_str)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT * FROM shifts WHERE machine=? AND status='approved' ORDER BY id DESC", (machine,)) as cursor:
            rows = await cursor.fetchall()
    if shift_type == 'شب':
        for r in rows:
            if clean_date(r[3]) == target_date and r[4] == 'صبح':
                return r
    else:
        try:
            y, m, d = map(int, target_date.split('-'))
            prev_date_str = (jdatetime.date(y, m, d) - jdatetime.timedelta(days=1)).strftime("%Y-%m-%d")
        except: return None
        for r in rows:
            if clean_date(r[3]) == prev_date_str and r[4] == 'شب':
                return r
    return None

async def get_last_valid_shift_before(machine, date_str, shift_type):
    target_date = clean_date(date_str)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT * FROM shifts WHERE machine=? AND status='approved' AND zero_reason IS NULL", (machine,)) as cursor:
            rows = await cursor.fetchall()
    valid_rows = []
    for r in rows:
        valid_rows.append((clean_date(r[3]), r[4], r))
    valid_rows.sort(key=lambda x: (x[0], '2' if x[1]=='شب' else '1'), reverse=True)
    for r_date, r_shift, r in valid_rows:
        if r_date < target_date:
            return r
        elif r_date == target_date:
            if shift_type == 'شب' and r_shift == 'صبح':
                return r
    return None

def calculate_work(prev_counter, curr_counter, reset_period, is_first_shift, start_counter):
    if prev_counter is None:
        if is_first_shift: prev_counter = start_counter
        else: return None 
    if curr_counter >= prev_counter: work = curr_counter - prev_counter
    else: work = (reset_period - prev_counter) + curr_counter
    if work > 600000: return None
    return work

async def calculate_work_logic(machine, date_str, shift_type, counter, set_number, parts_str, stored_bonus=0, is_chador=0):
    if stored_bonus is None: stored_bonus = 0
    if is_chador: return 0, False
    
    immediate_prev = await get_immediate_previous_shift(machine, date_str, shift_type)
    last_valid = await get_last_valid_shift_before(machine, date_str, shift_type)
    reset, start = await get_machine_info(machine)
    
    if immediate_prev:
        if immediate_prev[12]: 
            if last_valid: work = calculate_work(last_valid[6], counter, reset, False, start)
            else: work = calculate_work(None, counter, reset, True, start)
        else:
            work = calculate_work(immediate_prev[6], counter, reset, False, start)
        if work is not None: work += stored_bonus
        return work, False
    else:
        async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
            async with db.execute("SELECT count(*) FROM shifts WHERE machine=?", (machine,)) as cursor:
                total_machine_shifts = (await cursor.fetchone())[0]
        
        if total_machine_shifts == 0:
            work = calculate_work(None, counter, reset, True, start)
            if work is not None: work += stored_bonus
            return work, True
            
        if last_valid:
            try:
                y, m, d = map(int, clean_date(date_str).split('-'))
                curr_d = jdatetime.date(y, m, d)
                ly, lm, ld = map(int, clean_date(last_valid[3]).split('-'))
                lv_date = jdatetime.date(ly, lm, ld)
                
                if shift_type == 'شب':
                    if curr_d.weekday() == 6: 
                        if lv_date == curr_d - jdatetime.timedelta(days=1) and last_valid[4] == 'شب':
                            work = calculate_work(last_valid[6], counter, reset, False, start)
                            if work is not None: work += stored_bonus
                            return work, False
                    if curr_d.weekday() == 0: 
                        if lv_date == curr_d - jdatetime.timedelta(days=2) and last_valid[4] == 'شب':
                            work = calculate_work(last_valid[6], counter, reset, False, start)
                            if work is not None: work += stored_bonus
                            return work, False
                if shift_type == 'صبح':
                    if curr_d.weekday() == 0: 
                        if lv_date == curr_d - jdatetime.timedelta(days=2) and last_valid[4] == 'شب':
                            work = calculate_work(last_valid[6], counter, reset, False, start)
                            if work is not None: work += stored_bonus
                            return work, False
            except: pass
        return None, False

async def get_set_monthly_rank(machine, date_str, design_code=None, set_number=None):
    last_date = clean_date(date_str)
    if not last_date: return 1
    try:
        y, m, d = map(int, last_date.split('-'))
        first_day_str = f"{y}-{m:02d}-01"
    except: return 1
        
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT date, jig_bonus FROM shifts WHERE machine=? AND status='approved'", (machine,)) as cursor:
            rows = await cursor.fetchall()
            
    count = 0
    for r in rows:
        c_date = clean_date(r[0])
        if not c_date: continue
        if first_day_str <= c_date <= last_date:
            bonus_val = 0
            if r[1] is not None:
                try: bonus_val = int(r[1])
                except: bonus_val = 0
            if bonus_val > 0: count += 1
                
    return count if count > 0 else 1

async def check_missing_shifts(operator_id: int, new_date_str: str, machine: str):
    new_date_clean = clean_date(new_date_str)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT date FROM shifts WHERE operator_id=? AND machine=?", (operator_id, machine)) as cursor:
            rows = await cursor.fetchall()
    recorded_dates = set()
    for r in rows:
        if r[0]: recorded_dates.add(clean_date(r[0]))
    valid_past_dates = [d for d in recorded_dates if d < new_date_clean]
    if not valid_past_dates: return
    last_registered_date_str = max(valid_past_dates)
    try:
        y, m, d = map(int, last_registered_date_str.split('-'))
        curr = jdatetime.date(y, m, d) + jdatetime.timedelta(days=1)
        y2, m2, d2 = map(int, new_date_clean.split('-'))
        target_date = jdatetime.date(y2, m2, d2)
    except: return
    while curr < target_date:
        curr_str = curr.strftime("%Y-%m-%d")
        if curr.weekday() != 6:
            raise HTTPException(400, f"شما شیفت تاریخ {curr.strftime('%Y-%m-%d')} را ثبت نکرده‌اید. لطفا ابتدا آن را تعیین تکلیف کنید.")
        curr += jdatetime.timedelta(days=1)

@app.get("/api/today")
async def get_today(): return {"date": jdatetime.date.today().strftime("%Y-%m-%d")}

@app.post("/api/login")
async def login(data: LoginData):
    clean_phone = normalize_phone(data.phone_number)
    pwd_hash = hash_password(data.password)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT id, name, role, shift_type FROM users WHERE phone=? AND password_hash=?", (clean_phone, pwd_hash)) as cursor:
            row = await cursor.fetchone()
            if not row: raise HTTPException(status_code=401, detail="شماره یا رمز اشتباه است")
            user_id, name, role, shift_type = row
            exp_time = datetime.now(timezone.utc) + timedelta(days=3650)
            payload = {"sub": str(user_id), "name": name, "role": role, "exp": exp_time}
            if role == 'operator': payload["shift_type"] = shift_type
            token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
            return {"status": "ok", "token": token, "name": name, "role": role, "shift_type": shift_type}

@app.get("/api/admin/stats")
async def get_admin_stats(payload: dict = Depends(verify_admin)):
    today = jdatetime.date.today()
    start_date = (today - jdatetime.timedelta(days=6)).strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT date, shift_type, machine, counter, set_number, parts FROM shifts WHERE status='approved' AND date >= ?", (start_date,)) as cursor:
            rows = await cursor.fetchall()
    stats = {}
    for i in range(7):
        d = (today - jdatetime.timedelta(days=6-i)).strftime("%Y-%m-%d")
        stats[d] = {"صبح": 0, "شب": 0}
    for r in rows:
        d_str, shift, machine, counter, set_num, parts = r
        if d_str in stats:
            work, _ = await calculate_work_logic(machine, d_str, shift, counter, set_num, parts)
            if work: stats[d_str][shift] += work
    return {"labels": list(stats.keys()), "morning": [stats[d]["صبح"] for d in stats], "night": [stats[d]["شب"] for d in stats]}

@app.get("/api/admin/notifications")
async def get_admin_notifications(payload: dict = Depends(verify_admin)):
    alert_count = 0
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT machine, date, shift_type, counter, design_code, set_number, parts, zero_reason FROM shifts WHERE status='pending'") as cursor:
            rows = await cursor.fetchall()
            for r in rows:
                if r[7]: continue
                work, _ = await calculate_work_logic(r[0], r[1], r[2], r[3], r[5], r[6])
                if work is not None and (work < 0 or work > 400000): alert_count += 1
    return {"suspicious_alerts": alert_count}

@app.get("/api/admin/all_shifts")
async def get_all_shifts(start_date: str, end_date: str, payload: dict = Depends(verify_admin)):
    start_date = clean_date(start_date)
    end_date = clean_date(end_date)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT * FROM shifts WHERE date BETWEEN ? AND ? ORDER BY date DESC", (start_date, end_date)) as cursor:
            rows = await cursor.fetchall()
            
    shifts = []
    for r in rows:
        work_val = None
        if not r[12]:
            w, _ = await calculate_work_logic(r[5], r[3], r[4], r[6], r[8], r[9], r[15] if len(r)>15 else 0, r[16] if len(r)>16 else 0)
            work_val = w
            
        shifts.append({
            "id": r[0], "op_id": r[1], "op_name": r[2], "date": r[3], "day_name": get_day_name(r[3]), "shift": r[4], "machine": r[5], 
            "counter": r[6], "design": r[7], "set_num": r[8], "parts": r[9], "photo": r[10], "status": r[11],
            "zero_reason": r[12], "zero_note": r[14] if len(r)>14 else "", "is_chador": r[16] if len(r)>16 else 0, 
            "chador_count": r[17] if len(r)>17 else 0, "chador_invoice": r[18] if len(r)>18 else "", "work": work_val
        })
    return {"status": "ok", "shifts": shifts}

@app.post("/api/admin/approve_shift/{shift_id}")
async def approve_shift(shift_id: int, apply_bonus: bool = False, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        bonus_val = 60000 if apply_bonus else 0
        await db.execute("UPDATE shifts SET status='approved', jig_bonus=? WHERE id=?", (bonus_val, shift_id))
        await db.commit()
        return {"status": "ok"}

@app.post("/api/admin/reject_shift/{shift_id}")
async def reject_shift(shift_id: int, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        await db.execute("UPDATE shifts SET status='rejected' WHERE id=?", (shift_id,))
        await db.commit()
        return {"status": "ok"}

@app.delete("/api/admin/delete_shift/{shift_id}")
async def delete_shift(shift_id: int, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        await db.execute("DELETE FROM shifts WHERE id=?", (shift_id,))
        await db.commit()
        return {"status": "ok"}

@app.post("/api/admin/manual_shift")
async def manual_shift(
    operator_id: int = Form(...), date: str = Form(...), shift_type: str = Form(...),
    machine: str = Form(...), counter: int = Form(...), design_code: str = Form(""),
    set_number: int = Form(0), parts: str = Form(""), is_chador: str = Form("false"),
    chador_count: int = Form(0), chador_invoice: Optional[str] = Form(None),
    zero_reason: Optional[str] = Form(None), zero_note: Optional[str] = Form(None),
    request_bonus: str = Form("false"), photo: Optional[UploadFile] = File(None),
    payload: dict = Depends(verify_admin)
):
    try:
        date = clean_date(date)
        async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
            async with db.execute("SELECT name FROM users WHERE id=?", (operator_id,)) as cursor:
                row = await cursor.fetchone()
                op_name = row[0] if row else "نامشخص"
            
            final_photo = ""
            if photo:
                safe_name = machine.replace(" ", "-")
                photo_path = f"{PHOTO_DIR}/{date}_{shift_type}_{operator_id}_{safe_name}.jpg"
                contents = await photo.read()
                if len(contents) > 5 * 1024 * 1024: raise HTTPException(400, "حجم عکس زیاد است")
                with open(photo_path, "wb") as buffer: buffer.write(contents)
                final_photo = photo_path
            
            is_chador_val = 1 if is_chador == "true" else 0
            bonus_val = 60000 if request_bonus == "true" else 0
            
            await db.execute(
                "INSERT INTO shifts (operator_id, operator_name, date, shift_type, machine, counter, design_code, set_number, parts, photo_path, status, admin_note, zero_reason, zero_note, jig_bonus, is_chador, chador_count, chador_invoice) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (operator_id, op_name, date, shift_type, machine, counter, design_code, set_number, parts, final_photo, "approved", "ثبت دستی", zero_reason, zero_note, bonus_val, is_chador_val, chador_count, chador_invoice)
            )
            await db.commit()
            return {"status": "ok"}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.put("/api/admin/edit_shift/{shift_id}")
async def admin_edit_shift(
    shift_id: int, operator_id: int = Form(...), date: str = Form(...), shift_type: str = Form(...),
    machine: str = Form(...), counter: int = Form(...), design_code: str = Form(""),
    set_number: int = Form(0), parts: str = Form(""), is_chador: str = Form("false"),
    chador_count: int = Form(0), chador_invoice: Optional[str] = Form(None),
    zero_reason: Optional[str] = Form(None), zero_note: Optional[str] = Form(None),
    request_bonus: str = Form("false"), photo: Optional[UploadFile] = File(None),
    payload: dict = Depends(verify_admin)
):
    try:
        date = clean_date(date)
        async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
            async with db.execute("SELECT name FROM users WHERE id=?", (operator_id,)) as cursor:
                row = await cursor.fetchone()
                op_name = row[0] if row else "نامشخص"
                
            async with db.execute("SELECT photo_path FROM shifts WHERE id=?", (shift_id,)) as c:
                old_row = await c.fetchone()
                final_photo = old_row[0] if old_row and old_row[0] else ""
                
            if photo:
                safe_name = machine.replace(" ", "-")
                photo_path = f"{PHOTO_DIR}/{date}_{shift_type}_{operator_id}_{safe_name}.jpg"
                contents = await photo.read()
                if len(contents) > 5 * 1024 * 1024: raise HTTPException(400, "حجم عکس زیاد است")
                with open(photo_path, "wb") as buffer: buffer.write(contents)
                final_photo = photo_path

            is_chador_val = 1 if is_chador == "true" else 0
            bonus_val = 60000 if request_bonus == "true" else 0
            
            await db.execute(
                "UPDATE shifts SET operator_id=?, operator_name=?, machine=?, date=?, shift_type=?, counter=?, design_code=?, set_number=?, parts=?, photo_path=?, is_chador=?, chador_count=?, chador_invoice=?, zero_reason=?, zero_note=?, jig_bonus=?, status='approved' WHERE id=?",
                (operator_id, op_name, machine, date, shift_type, counter, design_code, set_number, parts, final_photo, is_chador_val, chador_count, chador_invoice, zero_reason, zero_note, bonus_val, shift_id)
            )
            await db.commit()
            return {"status": "ok"}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/admin/machines")
async def get_machines_admin(payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT name, reset_period, start_counter FROM machines") as cursor:
            rows = await cursor.fetchall()
            return {"machines": [{"name": r[0], "reset_period": r[1], "start_counter": r[2]} for r in rows]}

@app.post("/api/admin/machines")
async def add_machine(data: MachineData, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        try:
            await db.execute("INSERT INTO machines (name, reset_period, start_counter) VALUES (?, ?, ?)", (data.name, data.reset_period, data.start_counter))
            await db.commit()
            return {"status": "ok"}
        except: raise HTTPException(400, "چرخ تکراری است")

@app.put("/api/admin/machines/{machine_name}")
async def update_machine(machine_name: str, data: UpdateMachineData, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        await db.execute("UPDATE machines SET reset_period=?, start_counter=? WHERE name=?", (data.reset_period, data.start_counter, machine_name))
        await db.commit()
        return {"status": "ok"}

@app.get("/api/admin/duration_analysis")
async def get_duration_analysis(year: int, month: int, payload: dict = Depends(verify_admin)):
    # v16.6: duration analysis without shift separation
    start_str = f"{year}-{month:02d}-01"
    days_in_month = 31 if month <= 6 else (30 if month <= 11 else (29 if not jdatetime.date(year, 12, 1).isleap() else 30))
    end_str = jdatetime.date(year, month, days_in_month).strftime("%Y-%m-%d")
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT machine, design_code, set_number, COUNT(id), MIN(date), MAX(date) FROM shifts WHERE status='approved' AND zero_reason IS NULL AND is_chador=0 AND date >= ? AND date <= ? GROUP BY machine, design_code, set_number", (start_str, end_str)) as cursor:
            rows = await cursor.fetchall()
            
    stats = {}
    for r in rows:
        mach, design, set_num, shifts, min_d, max_d = r
        try:
            y1, m1, d1 = map(int, min_d.split('-'))
            y2, m2, d2 = map(int, max_d.split('-'))
            days_taken = (jdatetime.date(y2, m2, d2) - jdatetime.date(y1, m1, d1)).days + 1
        except: days_taken = 1
        
        if mach not in stats: stats[mach] = {}
        if design not in stats[mach]: stats[mach][design] = {'shifts': 0, 'days': 0, 'sets': 0}
        
        stats[mach][design]['shifts'] += shifts
        stats[mach][design]['days'] += days_taken
        stats[mach][design]['sets'] += 1
        
    result = {}
    for mach, designs in stats.items():
        result[mach] = {}
        for design, d in designs.items():
            result[mach][design] = {
                "avg_shifts": round(d['shifts'] / d['sets'], 1), 
                "avg_days": round(d['days'] / d['sets'], 1), 
                "total_sets": d['sets'],
                "total_shifts": d['shifts']
            }
    return {"status": "ok", "data": result}

@app.get("/api/machines")
async def get_machines_public(payload: dict = Depends(verify_token)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT name FROM machines") as cursor:
            return {"machines": [r[0] for r in await cursor.fetchall()]}

@app.get("/api/operator/check_shift")
async def check_shift(
    date: str,
    machine: str,
    shift_type: Optional[str] = None,
    target_operator_id: Optional[int] = None,
    exclude_shift_id: Optional[int] = None,
    payload: dict = Depends(verify_token)
):
    date = clean_date(date)
    op_id = target_operator_id if target_operator_id else int(payload["sub"])
    effective_shift = shift_type if shift_type else payload.get("shift_type")
    
    if not effective_shift:
        raise HTTPException(400, "نوع شیفت مشخص نیست.")
    
    query = "SELECT id, operator_id, operator_name, status, date, shift_type FROM shifts WHERE machine=? AND shift_type=? AND status IN ('approved', 'pending', 'rejected')"
    params = [machine, effective_shift]
    
    if exclude_shift_id:
        query += " AND id != ?"
        params.append(exclude_shift_id)
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
    
    for row in rows:
        if clean_date(row[4]) == date:
            owner_id = row[1]
            is_mine = (str(owner_id) == str(op_id))
            return {
                "status": "exists",
                "shift_id": row[0],
                "owner_id": owner_id,
                "owner_name": row[2],
                "is_mine": is_mine,
                "shift_status": row[3]
            }
    return {"status": "new"}

@app.get("/api/operator/shift/{shift_id}")
async def get_shift_detail(shift_id: int, payload: dict = Depends(verify_token)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)) as cursor:
            row = await cursor.fetchone()
            if row: return {"status": "ok", "data": {"id": row[0], "machine": row[5], "counter": row[6], "design": row[7], "set_number": row[8], "parts": row[9], "photo_path": row[10], "date": row[3], "zero_reason": row[12], "zero_note": row[14] if len(row)>14 else "", "request_bonus": row[15] if len(row)>15 else 0, "is_chador": row[16] if len(row)>16 else 0, "chador_count": row[17] if len(row)>17 else 0, "chador_invoice": row[18] if len(row)>18 else ""}}
            raise HTTPException(404, "یافت نشد")

@app.put("/api/operator/edit_shift/{shift_id}")
async def edit_shift(
    shift_id: int, machine: str = Form(...), counter: int = Form(...), set_number: int = Form(0), 
    design_code: str = Form(""), parts: str = Form(""), zero_reason: Optional[str] = Form(None),
    zero_note: Optional[str] = Form(None), photo: Optional[UploadFile] = File(None), request_bonus: str = Form("false"), is_chador: str = Form("false"), chador_count: int = Form(0), chador_invoice: Optional[str] = Form(None), payload: dict = Depends(verify_token)
):
    try:
        async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
            async with db.execute("SELECT photo_path, date, shift_type, status FROM shifts WHERE id=? AND operator_id=?", (shift_id, payload["sub"])) as cursor:
                row = await cursor.fetchone()
                if not row: raise HTTPException(404, "یافت نشد")
                old_photo, date_str, shift_type, current_status = row
                if current_status not in ['pending', 'rejected']:
                    raise HTTPException(400, "شما فقط مجاز به ویرایش کارکردهای در انتظار تایید یا رد شده هستید.")
                
                async with db.execute("SELECT id, date FROM shifts WHERE machine=? AND shift_type=? AND id!=? AND status IN ('pending', 'approved')", (machine, shift_type, shift_id)) as c:
                    rows = await c.fetchall()
                for r in rows:
                    if clean_date(r[1]) == clean_date(date_str):
                        raise HTTPException(400, "شما نمی‌توانید این رکورد را ویرایش کنید چون یک رکورد دیگر برای این شیفت وجود دارد.")

            final_photo = old_photo
            if photo:
                safe_name = machine.replace(" ", "-")
                photo_path = f"{PHOTO_DIR}/{date_str}_{shift_type}_{payload['sub']}_{safe_name}_edit.jpg"
                contents = await photo.read()
                if len(contents) > 5 * 1024 * 1024: raise HTTPException(400, "حجم عکس زیاد است (حداکثر ۵ مگابایت)")
                with open(photo_path, "wb") as buffer: buffer.write(contents)
                final_photo = photo_path

            if zero_reason:
                await db.execute(
                    "UPDATE shifts SET machine=?, counter=0, design_code='000', set_number=0, parts='', zero_reason=?, zero_note=?, status='pending', jig_bonus=0, is_chador=0, chador_count=0, chador_invoice=NULL WHERE id=?",
                    (machine, zero_reason, zero_note, shift_id)
                )
            else:
                is_chador_val = 1 if is_chador == "true" else 0
                if not final_photo and not is_chador_val:
                    raise HTTPException(400, "برای تبدیل کارکرد صفر به عادی، آپلود عکس الزامی است.")
                work, _ = await calculate_work_logic(machine, date_str, shift_type, counter, set_number, parts, is_chador=is_chador_val)
                if work is not None and work < 0: raise HTTPException(400, "کارکرد منفی است!")
                if work is not None and work > 600000: raise HTTPException(400, "کارکرد محاسبه شده غیرعادی است (بیشتر از ۶۰۰,۰۰۰). لطفا شمارنده را بررسی کنید.")
                
                bonus_val = 60000 if request_bonus == "true" else 0
                await db.execute(
                    "UPDATE shifts SET machine=?, counter=?, design_code=?, set_number=?, parts=?, photo_path=?, zero_reason=NULL, zero_note=NULL, status='pending', jig_bonus=?, is_chador=?, chador_count=?, chador_invoice=? WHERE id=?",
                    (machine, counter, design_code, set_number, parts, final_photo, bonus_val, is_chador_val, chador_count, chador_invoice, shift_id)
                )
            await db.commit()
            return {"status": "ok"}
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(500, f"خطای سرور: {str(e)}")

@app.post("/api/operator/submit_zero")
async def submit_zero(data: dict, payload: dict = Depends(verify_token)):
    try:
        date = clean_date(data.get("date"))
        machine = data.get("machine")
        zero_reason = data.get("zero_reason")
        zero_note = data.get("zero_note", "")
        
        await check_missing_shifts(int(payload["sub"]), date, machine)
        async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
            async with db.execute("SELECT id, date FROM shifts WHERE machine=? AND shift_type=? AND status IN ('approved', 'pending', 'rejected')", (machine, payload.get("shift_type"))) as cursor:
                rows = await cursor.fetchall()
            for r in rows:
                if clean_date(r[1]) == date:
                    raise HTTPException(400, "برای این چرخ و تاریخ قبلا ثبت شده است.")
            await db.execute(
                "INSERT INTO shifts (operator_id, operator_name, date, shift_type, machine, counter, design_code, set_number, parts, photo_path, status, zero_reason, zero_note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (payload["sub"], payload["name"], date, payload.get("shift_type"), machine, 0, "000", 0, "", "", "pending", zero_reason, zero_note)
            )
            await db.commit()
        return {"status": "ok"}
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(500, str(e))

@app.get("/api/operator/last_shifts")
async def get_last_shifts(machine: Optional[str] = None, target_operator_id: Optional[int] = None, target_shift_type: Optional[str] = None, payload: dict = Depends(verify_token)):
    op_id = target_operator_id if target_operator_id else int(payload["sub"])
    shift = target_shift_type if target_shift_type else payload.get("shift_type")
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        query_same = "SELECT date, shift_type, machine, counter, design_code, set_number, parts, status, zero_reason FROM shifts WHERE operator_id=? AND shift_type=? "
        params_same = [op_id, shift]
        if machine: query_same += "AND machine=? "; params_same.append(machine)
        query_same += "ORDER BY date DESC LIMIT 1"
        
        opp_shift = 'شب' if shift == 'صبح' else 'صبح'
        query_opp = "SELECT date, shift_type, machine, counter, design_code, set_number, parts, status, zero_reason, operator_name FROM shifts WHERE shift_type=? AND status IN ('approved', 'pending') "
        params_opp = [opp_shift]
        if machine: query_opp += "AND machine=? "; params_opp.append(machine)
        query_opp += "ORDER BY date DESC LIMIT 1"
        
        async with db.execute(query_same, tuple(params_same)) as cursor: row_same = await cursor.fetchone()
        async with db.execute(query_opp, tuple(params_opp)) as cursor: row_opp = await cursor.fetchone()
        
        def format_row(r):
            if not r: return {"exists": False}
            return {"exists": True, "data": {"date": r[0], "day_name": get_day_name(r[0]), "shift_type": r[1], "machine": r[2], "counter": r[3], "design": r[4], "set_number": r[5], "parts": r[6], "status": r[7], "zero_reason": r[8], "op_name": r[9] if len(r)>9 else ""}}
        return {"same": format_row(row_same), "opposite": format_row(row_opp)}

@app.get("/api/operator/immediate_prev_info")
async def get_immediate_prev_info(date: str, machine: str, shift_type: str, target_operator_id: Optional[int] = None, payload: dict = Depends(verify_token)):
    date = clean_date(date)
    try:
        y, m, d = map(int, date.split('-'))
        curr_date = jdatetime.date(y, m, d)
        if 'شب' in shift_type: expected_date = date; expected_shift = 'صبح'
        else: expected_date = (curr_date - jdatetime.timedelta(days=1)).strftime("%Y-%m-%d"); expected_shift = 'شب'
    except: return {"exists": False}
        
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT date, shift_type, machine, counter, design_code, set_number, parts, status, zero_reason, operator_name, jig_bonus FROM shifts WHERE machine LIKE ? AND shift_type LIKE ?", (f"%{machine.strip()}%", f"%{expected_shift.strip()}%")) as cursor:
            rows = await cursor.fetchall()
            
    all_dates = []
    try:
        ey, em, ed = map(int, expected_date.split('-'))
        expected_tuple = (ey, em, ed)
    except: expected_tuple = None
    for r in rows:
        db_date = clean_date(r[0])
        all_dates.append(db_date)
        is_match = (db_date == expected_date)
        if not is_match and expected_tuple:
            try:
                dy, dm, dd = map(int, db_date.split('-'))
                if (dy, dm, dd) == expected_tuple: is_match = True
            except: pass
        if is_match:
            if r[7] == 'rejected': return {"exists": False, "rejected": True, "expected_date": expected_date, "expected_shift": expected_shift}
            bonus_val = r[10] if len(r) > 10 and r[10] else 0
            return {"exists": True, "data": {"date": r[0], "day_name": get_day_name(r[0]), "shift_type": r[1], "machine": r[2], "counter": r[3], "design": r[4], "set_number": r[5], "parts": r[6], "status": r[7], "zero_reason": r[8], "operator_name": r[9], "jig_bonus": bonus_val}}
    return {"exists": False, "expected_date": expected_date, "expected_shift": expected_shift}

@app.get("/api/operator/suggest_date")
async def suggest_date(machine: Optional[str] = None, target_operator_id: Optional[int] = None, target_shift_type: Optional[str] = None, payload: dict = Depends(verify_token)):
    op_id = target_operator_id if target_operator_id else int(payload["sub"])
    shift = target_shift_type if target_shift_type else payload.get("shift_type")
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        query = "SELECT date FROM shifts WHERE operator_id=? AND shift_type=? "
        params = [op_id, shift]
        if machine: query += "AND machine=? "; params.append(machine)
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
    
    valid_dates = []
    for r in rows:
        if not r[0]: continue
        ds = clean_date(r[0])
        try:
            y, m, d = map(int, ds.split('-'))
            valid_dates.append(jdatetime.date(y, m, d))
        except: pass
            
    if not valid_dates:
        return {"date": jdatetime.date.today().strftime("%Y-%m-%d")}
        
    max_date = max(valid_dates)
    next_date = max_date + jdatetime.timedelta(days=1)
    
    while next_date.weekday() == 6:
        next_date += jdatetime.timedelta(days=1)
        
    return {"date": next_date.strftime("%Y-%m-%d")}

@app.get("/api/operator/notifications")
async def get_operator_notifications(payload: dict = Depends(verify_token)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT COUNT(*), MAX(date) FROM shifts WHERE operator_id=? AND status='rejected'", (payload["sub"],)) as cursor:
            row = await cursor.fetchone()
    return {"rejected_count": row[0], "last_rejected_date": row[1]}

@app.post("/api/operator/preview_work")
async def preview_work(machine: str = Form(...), date: str = Form(...), counter: int = Form(...), set_number: int = Form(0), parts: str = Form(""), is_chador: str = Form("false"), request_bonus: str = Form("false"), target_operator_id: Optional[int] = None, target_shift_type: Optional[str] = None, payload: dict = Depends(verify_token)):
    try:
        date = clean_date(date)
        op_id = target_operator_id if target_operator_id else int(payload["sub"])
        shift = target_shift_type if target_shift_type else payload.get("shift_type")
        
        await check_missing_shifts(op_id, date, machine)
        is_chador_val = 1 if is_chador == "true" else 0
        work, is_first = await calculate_work_logic(machine, date, shift, counter, set_number, parts, is_chador=is_chador_val)
        if work is not None and work < 0: raise HTTPException(400, "کارکرد منفی است!")
        if work is not None and work > 600000: raise HTTPException(400, "کارکرد محاسبه شده غیرعادی است (بیشتر از ۶۰۰,۰۰۰). لطفا شمارنده را بررسی کنید.")
        immediate_prev = await get_immediate_previous_shift(machine, date, shift)
        
        pending_warning = False
        if not immediate_prev:
            async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
                if shift == 'شب': q, p = "SELECT id FROM shifts WHERE machine=? AND date=? AND shift_type='صبح' AND status='pending'", (machine, date)
                else:
                    try:
                        y, m, d = map(int, date.split('-'))
                        prev_d = (jdatetime.date(y, m, d) - jdatetime.timedelta(days=1)).strftime("%Y-%m-%d")
                        q, p = "SELECT id FROM shifts WHERE machine=? AND date=? AND shift_type='شب' AND status='pending'", (machine, prev_d)
                    except: q, p = "", ()
                if q:
                    async with db.execute(q, p) as cursor:
                        if await cursor.fetchone(): pending_warning = True
        return {"work": work, "pending_warning": pending_warning}
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(500, f"خطای داخلی سرور: {str(e)}")

@app.get("/api/operator/stats")
async def get_operator_stats(payload: dict = Depends(verify_token)):
    today = jdatetime.date.today()
    start_date = (today - jdatetime.timedelta(days=6)).strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT date, shift_type, machine, counter, set_number, parts, zero_reason FROM shifts WHERE operator_id=? AND status='approved' AND date >= ?", (payload["sub"], start_date)) as cursor:
            rows = await cursor.fetchall()
    daily_work = {}
    for r in rows:
        if r[6]: work = 0
        else: work, _ = await calculate_work_logic(r[2], r[0], r[1], r[3], r[4], r[5])
        if work:
            if r[0] not in daily_work: daily_work[r[0]] = 0
            daily_work[r[0]] += work
    return {"status": "ok", "data": [{"date": d, "work": daily_work[d]} for d in sorted(daily_work.keys())]}

@app.get("/api/operator/reports")
async def get_operator_reports(start_date: str, end_date: str, machine: str = "all", payload: dict = Depends(verify_token)):
    start_date = clean_date(start_date)
    end_date = clean_date(end_date)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT id, date, shift_type, machine, counter, design_code, set_number, parts, photo_path, zero_reason, status, zero_note, jig_bonus, is_chador, chador_count, chador_invoice FROM shifts WHERE operator_id=?", (payload["sub"],)) as cursor:
            rows = await cursor.fetchall()
    
    filtered_rows = []
    for r in rows:
        r_date = clean_date(r[1])
        if start_date <= r_date <= end_date:
            if machine == "all" or r[3] == machine: filtered_rows.append(r)
    filtered_rows.sort(key=lambda x: (clean_date(x[1]), '2' if x[2]=='شب' else '1'), reverse=True)
    rows = filtered_rows
    
    total_work = 0; detailed_list = []; machine_sets_dict = {}
    for r in rows:
        prev_work = None; prev_counter = None
        if not r[9]:
            immediate_prev = await get_immediate_previous_shift(r[3], r[1], r[2])
            if immediate_prev:
                prev_counter = immediate_prev[6]
                if immediate_prev[12]: prev_work = 0
                else: prev_work, _ = await calculate_work_logic(immediate_prev[5], immediate_prev[3], immediate_prev[4], immediate_prev[6], immediate_prev[8], immediate_prev[9], immediate_prev[15] if len(immediate_prev)>15 else 0)

        if r[9]: work_val = 0; rank = 0
        else:
            work, _ = await calculate_work_logic(r[3], r[1], r[2], r[4], r[6], r[7], r[12], r[13])
            work_val = work if work is not None else None
            rank = await get_set_monthly_rank(r[3], r[1], r[5], r[6])
            
        mach = r[3]
        if mach not in machine_sets_dict: machine_sets_dict[mach] = {'bonus': 0}
        if r[12] and r[12] > 0: machine_sets_dict[mach]['bonus'] += 1
            
        if work_val is not None: total_work += work_val
        detailed_list.append({"id": r[0], "date": r[1], "day_name": get_day_name(r[1]), "shift_type": r[2], "machine": r[3], "counter": r[4], "design_code": r[5], "set_number": r[6], "parts": r[7], "photo_path": r[8], "work": work_val, "monthly_set_rank": rank, "zero_reason": r[9], "status": r[10], "zero_note": r[11], "jig_bonus": r[12] if len(r)>12 else 0, "is_chador": r[13] if len(r)>13 else 0, "chador_count": r[14] if len(r)>14 else 0, "chador_invoice": r[15] if len(r)>15 else "", "prev_work": prev_work, "prev_counter": prev_counter})
        
    final_machine_sets = {}
    total_sets = 0
    for mach, data in machine_sets_dict.items():
        cnt = data['bonus']
        final_machine_sets[mach] = cnt
        total_sets += cnt
    return {"summary": {"total_work": total_work, "total_sets": total_sets, "machine_sets": final_machine_sets}, "details": detailed_list}

@app.post("/api/submit_shift")
async def submit_shift(machine: str = Form(...), date: str = Form(...), design_code: str = Form(""), counter: int = Form(...), set_number: int = Form(0), parts: str = Form(""), photo: UploadFile = File(...), request_bonus: str = Form("false"), is_chador: str = Form("false"), chador_count: int = Form(0), chador_invoice: Optional[str] = Form(None), payload: dict = Depends(verify_token)):
    if payload.get("role") != "operator": raise HTTPException(403, "دسترسی غیرمجاز")
    try:
        date = clean_date(date)
        await check_missing_shifts(int(payload["sub"]), date, machine)
        contents = await photo.read()
        if len(contents) > 5 * 1024 * 1024: raise HTTPException(400, "حجم عکس زیاد است (حداکثر ۵ مگابایت)")
            
        safe_name = machine.replace(" ", "-")
        photo_path = f"{PHOTO_DIR}/{date}_{payload['shift_type']}_{payload['sub']}_{safe_name}.jpg"
        with open(photo_path, "wb") as buffer: buffer.write(contents)
        
        is_chador_val = 1 if is_chador == "true" else 0
        work, _ = await calculate_work_logic(machine, date, payload["shift_type"], counter, set_number, parts, is_chador=is_chador_val)
        if work is not None and work < 0: raise HTTPException(400, "کارکرد منفی است!")
        if work is not None and work > 600000: raise HTTPException(400, "کارکرد محاسبه شده غیرعادی است (بیشتر از ۶۰۰,۰۰۰). لطفا شمارنده را بررسی کنید.")
        
        bonus_val = 60000 if request_bonus == "true" else 0
        async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
            async with db.execute("SELECT id, date FROM shifts WHERE machine=? AND shift_type=? AND status IN ('approved', 'pending', 'rejected')", (machine, payload["shift_type"])) as cursor:
                rows = await cursor.fetchall()
            for r in rows:
                if clean_date(r[1]) == date: raise HTTPException(400, "برای این چرخ و تاریخ قبلا ثبت شده است.")
            await db.execute("INSERT INTO shifts (operator_id, operator_name, date, shift_type, machine, counter, design_code, set_number, parts, photo_path, status, jig_bonus, is_chador, chador_count, chador_invoice) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (payload["sub"], payload["name"], date, payload["shift_type"], machine, counter, design_code, set_number, parts, photo_path, "pending", bonus_val, is_chador_val, chador_count, chador_invoice))
            await db.commit()
        return {"status": "ok"}
    except aiosqlite.IntegrityError: raise HTTPException(400, "شما قبلاً برای این تاریخ، شیفت و چرخ کارکرد ثبت کرده‌اید.")
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(500, str(e))

@app.get("/api/admin/users")
async def get_users(payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT id, name, phone, role, shift_type FROM users") as cursor:
            return {"users": [{"id": r[0], "name": r[1], "phone": r[2], "role": r[3], "shift_type": r[4]} for r in await cursor.fetchall()]}

@app.post("/api/admin/create_user")
async def create_user(data: CreateUserData, payload: dict = Depends(verify_admin)):
    clean_phone = normalize_phone(data.phone_number)
    pwd_hash = hash_password(data.password)
    final_shift = data.shift_type if data.role == "operator" else None
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        try:
            await db.execute("INSERT INTO users (phone, password_hash, name, role, shift_type) VALUES (?, ?, ?, ?, ?)", (clean_phone, pwd_hash, data.name, data.role, final_shift))
            await db.commit()
            return {"status": "ok"}
        except: raise HTTPException(400, "شماره تکراری است")

@app.put("/api/admin/users/{user_id}")
async def update_user(user_id: int, data: UpdateUserData, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        if data.password: await db.execute("UPDATE users SET name=?, shift_type=?, password_hash=? WHERE id=?", (data.name, data.shift_type, hash_password(data.password), user_id))
        else: await db.execute("UPDATE users SET name=?, shift_type=? WHERE id=?", (data.name, data.shift_type, user_id))
        await db.commit()
        return {"status": "ok"}

@app.get("/api/get_weeks")
async def get_weeks(year: int, month: int, payload: dict = Depends(verify_token)):
    month_names = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور', 'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند']
    m_name = month_names[month-1]
    try: first_day = jdatetime.date(year, month, 1)
    except: return {"weeks": []}
    wd = first_day.weekday()
    start_of_week1 = first_day + jdatetime.timedelta(days=1) if wd == 6 else first_day - jdatetime.timedelta(days=wd)
    days_in_month = 31 if month <= 6 else (30 if month <= 11 else (29 if not jdatetime.date(year, 12, 1).isleap() else 30))
    last_day = jdatetime.date(year, month, days_in_month)
    weeks = []; curr = start_of_week1; week_num = 1
    while curr <= last_day:
        end_of_week = curr + jdatetime.timedelta(days=6)
        overlap_days = (min(end_of_week, last_day) - max(curr, first_day)).days + 1
        if overlap_days > 0:
            if not (overlap_days == 1 and curr < first_day):
                label = f"هفته {['اول','دوم','سوم','چهارم','پنجم','ششم'][week_num-1]}"
                if curr.month != month: label += f" ({overlap_days} روز اول {m_name})"
                elif end_of_week.month != month: label += f" ({overlap_days} روز آخر {m_name})"
                weeks.append({"value": f"{week_num}", "label": label})
                week_num += 1
        curr = end_of_week + jdatetime.timedelta(days=1)
    return {"weeks": weeks}

@app.get("/api/admin/salary_settings")
async def get_salary_settings(year: int, month: int, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT price, chador_price, show_salary, show_opposite_salary FROM monthly_prices WHERE year=? AND month=?", (year, month)) as cursor:
            row = await cursor.fetchone()
            if row: return {"price": row[0], "chador_price": row[1], "show_salary": bool(row[2]), "show_opposite_salary": bool(row[3])}
    return {"price": 0, "chador_price": 0, "show_salary": False, "show_opposite_salary": False}

@app.post("/api/admin/salary_settings")
async def save_salary_settings(year: int, month: int, price: float, chador_price: float, show_salary: bool, show_opposite_salary: bool, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        await db.execute("INSERT OR REPLACE INTO monthly_prices (year, month, price, chador_price, show_salary, show_opposite_salary) VALUES (?, ?, ?, ?, ?, ?)", (year, month, price, chador_price, 1 if show_salary else 0, 1 if show_opposite_salary else 0))
        await db.commit()
    return {"status": "ok"}

@app.get("/api/admin/monthly_financials")
async def get_monthly_financials(year: int, month: int, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT name, shift_type FROM users WHERE role='operator'") as cursor:
            users = await cursor.fetchall()
            
        async with db.execute("SELECT DISTINCT operator_name, shift_type, machine FROM shifts WHERE status='approved' AND date LIKE ?", (f"{year}-{month:02d}-%",)) as cursor:
            worked_machines = await cursor.fetchall()
            
        machines_by_op = {}
        for wm in worked_machines:
            op_name, shift, mach = wm
            key = f"{shift} {op_name}"
            if key not in machines_by_op: machines_by_op[key] = set()
            machines_by_op[key].add(mach)
            
        result = []
        for u in users:
            op_name, op_shift = u
            key = f"{op_shift} {op_name}"
            
            async with db.execute("SELECT reward, reward_title, penalty, penalty_title FROM monthly_shift_financials WHERE year=? AND month=? AND operator_name=? AND shift_type=?", (year, month, op_name, op_shift)) as cursor:
                fin = await cursor.fetchone()
                r = fin[0] if fin else 0
                r_t = fin[1] if fin else ""
                p = fin[2] if fin else 0
                p_t = fin[3] if fin else ""
                
            machines_data = []
            for mach in machines_by_op.get(key, []):
                async with db.execute("SELECT reward, reward_title, penalty, penalty_title FROM monthly_machine_financials WHERE year=? AND month=? AND operator_name=? AND shift_type=? AND machine=?", (year, month, op_name, op_shift, mach)) as cursor:
                    m_fin = await cursor.fetchone()
                    machines_data.append({
                        "machine": mach,
                        "reward": m_fin[0] if m_fin else 0,
                        "reward_title": m_fin[1] if m_fin else "",
                        "penalty": m_fin[2] if m_fin else 0,
                        "penalty_title": m_fin[3] if m_fin else ""
                    })
                    
            result.append({
                "op_name": op_name, "shift_type": op_shift, 
                "reward": r, "reward_title": r_t, "penalty": p, "penalty_title": p_t,
                "machines": machines_data
            })
        return {"status": "ok", "details": result}

@app.post("/api/admin/monthly_financials")
async def save_monthly_financials(data: dict, year: int, month: int, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        await db.execute("DELETE FROM monthly_shift_financials WHERE year=? AND month=?", (year, month))
        await db.execute("DELETE FROM monthly_machine_financials WHERE year=? AND month=?", (year, month))
        
        for shift_item in data.get("shift_financials", []):
            await db.execute("INSERT INTO monthly_shift_financials (year, month, operator_name, shift_type, reward, reward_title, penalty, penalty_title) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (year, month, shift_item["op_name"], shift_item["shift_type"], shift_item.get("reward", 0), shift_item.get("reward_title", ""), shift_item.get("penalty", 0), shift_item.get("penalty_title", "")))
                
        for mach_item in data.get("machine_financials", []):
            await db.execute("INSERT INTO monthly_machine_financials (year, month, operator_name, shift_type, machine, reward, reward_title, penalty, penalty_title) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (year, month, mach_item["op_name"], mach_item["shift_type"], mach_item["machine"], mach_item.get("reward", 0), mach_item.get("reward_title", ""), mach_item.get("penalty", 0), mach_item.get("penalty_title", "")))
                
        await db.commit()
    return {"status": "ok"}

@app.get("/api/reports/weekly")
async def get_weekly_report(year: int, month: int, week_num: int, payload: dict = Depends(verify_token)):
    month_names = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور', 'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند']
    week_names = ['اول', 'دوم', 'سوم', 'چهارم', 'پنجم', 'ششم']
    try: first_day = jdatetime.date(year, month, 1)
    except: return {"status": "empty"}
    wd = first_day.weekday()
    start_of_week1 = first_day + jdatetime.timedelta(days=1) if wd == 6 else first_day - jdatetime.timedelta(days=wd)
    days_in_month = 31 if month <= 6 else (30 if month <= 11 else (29 if not jdatetime.date(year, 12, 1).isleap() else 30))
    last_day = jdatetime.date(year, month, days_in_month)
    target_start = None; target_end = None; curr = start_of_week1; w_num = 1
    while curr <= last_day:
        end_of_week = curr + jdatetime.timedelta(days=6)
        overlap_days = (min(end_of_week, last_day) - max(curr, first_day)).days + 1
        if overlap_days > 0:
            if not (overlap_days == 1 and curr < first_day):
                if w_num == week_num: target_start = curr; target_end = end_of_week; break
                w_num += 1
        curr = end_of_week + jdatetime.timedelta(days=1)
    if not target_start: return {"status": "empty"}
    start_str = target_start.strftime("%Y-%m-%d"); end_str = target_end.strftime("%Y-%m-%d")
    week_title = f"هفته {week_names[week_num-1]} {month_names[month-1]}"
    
    is_admin = payload.get("role") == "admin"
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT price, chador_price, show_salary, show_opposite_salary FROM monthly_prices WHERE year=? AND month=?", (year, month)) as c:
            price_row = await c.fetchone()
        price = price_row[0] if price_row else 0
        chador_price = price_row[1] if price_row else 0
        show_salary = True if is_admin else (bool(price_row[2]) if price_row else False)
        show_opposite_salary = True if is_admin else (bool(price_row[3]) if price_row else False)

        query = "SELECT operator_name, shift_type, machine, counter, design_code, set_number, date, jig_bonus, id, parts, is_chador, chador_count FROM shifts WHERE status='approved'"
        async with db.execute(query) as cursor: all_rows = await cursor.fetchall()
        
    rows = [r for r in all_rows if start_str <= clean_date(r[6]) <= end_str]
    if not rows: return {"status": "empty", "week_title": week_title, "start_date": start_str, "end_date": end_str}
    
    total_all = 0; total_salary = 0; machine_totals = {}; machine_days = {}; operator_shifts = {}; sets_by_machine = {}; chador_counts = {}
    op_totals = {}; op_days = {}; op_machine_totals = {}; op_machine_days = {}; op_salaries = {}; op_machine_salaries = {}
    user_shift = payload.get("shift_type")

    for r in rows:
        op_name, shift, machine, counter, design, set_num, date, bonus, sid, parts, is_chador, chador_count = r
        work_val, _ = await calculate_work_logic(machine, date, shift, counter, set_num, parts, bonus, is_chador)
        if work_val is None: work_val = 0
        
        is_opposite = (shift != user_shift)
        can_see_salary = True
        if not is_admin:
            if not show_salary: can_see_salary = False
            if is_opposite and not show_opposite_salary: can_see_salary = False
            
        if can_see_salary:
            base_sal = int(round(work_val * price)) if not is_chador else int(round(chador_count * chador_price))
            sal_val = base_sal
        else:
            sal_val = 0
            
        total_all += work_val
        total_salary += sal_val
        
        if machine not in machine_totals: machine_totals[machine] = 0
        machine_totals[machine] += work_val
        if work_val > 0:
            if machine not in machine_days: machine_days[machine] = set()
            machine_days[machine].add(clean_date(date))
            
        op_key = f"{shift} {op_name}"
        if op_key not in operator_shifts: operator_shifts[op_key] = {}
        if machine not in operator_shifts[op_key]: operator_shifts[op_key][machine] = 0
        operator_shifts[op_key][machine] += work_val
        
        op_totals[op_key] = op_totals.get(op_key, 0) + work_val
        op_salaries[op_key] = op_salaries.get(op_key, 0) + sal_val
        
        if work_val > 0:
            if op_key not in op_days: op_days[op_key] = set()
            op_days[op_key].add(clean_date(date))
            
        if op_key not in op_machine_totals: op_machine_totals[op_key] = {}
        if machine not in op_machine_totals[op_key]: op_machine_totals[op_key][machine] = 0
        op_machine_totals[op_key][machine] += work_val
        
        if op_key not in op_machine_salaries: op_machine_salaries[op_key] = {}
        if machine not in op_machine_salaries[op_key]: op_machine_salaries[op_key][machine] = 0
        op_machine_salaries[op_key][machine] += sal_val
        
        if work_val > 0:
            if op_key not in op_machine_days: op_machine_days[op_key] = {}
            if machine not in op_machine_days[op_key]: op_machine_days[op_key][machine] = set()
            op_machine_days[op_key][machine].add(clean_date(date))
            
        if is_chador:
            if op_key not in chador_counts: chador_counts[op_key] = 0
            chador_counts[op_key] += chador_count
        if machine not in sets_by_machine: sets_by_machine[machine] = {"list": [], "bonus_count": 0}
        if design and set_num: sets_by_machine[machine]["list"].append((str(design), str(set_num)))
        b_val = 0
        if bonus is not None:
            try: b_val = int(bonus)
            except: b_val = 0
        if b_val > 0: sets_by_machine[machine]["bonus_count"] += 1

    sets_summary = []
    for machine, data in sets_by_machine.items():
        if not data["list"]: continue
        unique_sets = list(dict.fromkeys(data["list"]))
        rank = await get_set_monthly_rank(machine, end_str)
        sets_summary.append({"machine": machine, "count": data["bonus_count"], "sets_list": unique_sets, "rank": rank})
        
    final_machine_totals = {}
    for mach, work in machine_totals.items():
        d_cnt = len(machine_days.get(mach, []))
        if d_cnt == 0: d_cnt = 1
        final_machine_totals[mach] = {"total": work, "avg": work // d_cnt}
        
    operator_averages = {}
    for op_key, total in op_totals.items():
        d_cnt = len(op_days.get(op_key, []))
        if d_cnt == 0: d_cnt = 1
        operator_averages[op_key] = total // d_cnt
        
    op_machine_averages = {}
    for op_key, machines in op_machine_totals.items():
        op_machine_averages[op_key] = {}
        for mach, total in machines.items():
            d_cnt = len(op_machine_days.get(op_key, {}).get(mach, []))
            if d_cnt == 0: d_cnt = 1
            op_machine_averages[op_key][mach] = total // d_cnt
        
    return {"status": "ok", "week_title": week_title, "start_date": start_str, "end_date": end_str, "total_all": total_all, "total_salary": total_salary, "show_salary": show_salary, "show_opposite_salary": show_opposite_salary, "machine_totals": final_machine_totals, "operator_shifts": operator_shifts, "sets_summary": sets_summary, "chador_counts": chador_counts, "operator_averages": operator_averages, "op_machine_averages": op_machine_averages, "op_salaries": op_salaries, "op_machine_salaries": op_machine_salaries}

@app.get("/api/reports/monthly")
async def get_monthly_report(year: int, month: int, payload: dict = Depends(verify_token)):
    month_names = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور', 'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند']
    m_name = month_names[month-1]
    try: first_day = jdatetime.date(year, month, 1)
    except: return {"status": "empty"}
    days_in_month = 31 if month <= 6 else (30 if month <= 11 else (29 if not jdatetime.date(year, 12, 1).isleap() else 30))
    start_str = first_day.strftime("%Y-%m-%d"); end_str = jdatetime.date(year, month, days_in_month).strftime("%Y-%m-%d")
    
    is_admin = payload.get("role") == "admin"
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute("SELECT price, chador_price, show_salary, show_opposite_salary FROM monthly_prices WHERE year=? AND month=?", (year, month)) as c:
            price_row = await c.fetchone()
        price = price_row[0] if price_row else 0
        chador_price = price_row[1] if price_row else 0
        show_salary = True if is_admin else (bool(price_row[2]) if price_row else False)
        show_opposite_salary = True if is_admin else (bool(price_row[3]) if price_row else False)

        query = "SELECT operator_name, shift_type, machine, counter, design_code, set_number, date, jig_bonus, id, parts, is_chador, chador_count FROM shifts WHERE status='approved'"
        async with db.execute(query) as cursor: all_rows = await cursor.fetchall()
        
        async with db.execute("SELECT operator_name, shift_type, reward, reward_title, penalty, penalty_title FROM monthly_shift_financials WHERE year=? AND month=?", (year, month)) as c:
            shift_fin_rows = await c.fetchall()
            
        async with db.execute("SELECT operator_name, shift_type, machine, reward, reward_title, penalty, penalty_title FROM monthly_machine_financials WHERE year=? AND month=?", (year, month)) as c:
            machine_fin_rows = await c.fetchall()
            
    rows = [r for r in all_rows if start_str <= clean_date(r[6]) <= end_str]
    if not rows: return {"status": "empty", "month_title": m_name}
    
    total_all = 0; total_salary = 0; machine_totals = {}; machine_days = {}; operator_shifts = {}; sets_by_machine = {}; chador_counts = {}
    op_totals = {}; op_days = {}; op_machine_totals = {}; op_machine_days = {}; op_salaries = {}; op_machine_salaries = {}
    user_shift = payload.get("shift_type")

    shift_financials = {}
    for fr in shift_fin_rows:
        shift_financials[f"{fr[1]} {fr[0]}"] = {"reward": fr[2] or 0, "reward_title": fr[3], "penalty": fr[4] or 0, "penalty_title": fr[5]}
        
    machine_financials = {}
    for mf in machine_fin_rows:
        key = f"{mf[1]} {mf[0]}"
        if key not in machine_financials: machine_financials[key] = {}
        machine_financials[key][mf[2]] = {"reward": mf[3] or 0, "reward_title": mf[4], "penalty": mf[5] or 0, "penalty_title": mf[6]}

    for r in rows:
        op_name, shift, machine, counter, design, set_num, date, bonus, sid, parts, is_chador, chador_count = r
        chador_count = chador_count or 0
        is_chador = is_chador or 0
        work_val, _ = await calculate_work_logic(machine, date, shift, counter, set_num, parts, bonus, is_chador)
        if work_val is None: work_val = 0
        
        is_opposite = (shift != user_shift)
        can_see_salary = True
        if not is_admin:
            if not show_salary: can_see_salary = False
            if is_opposite and not show_opposite_salary: can_see_salary = False
            
        if can_see_salary:
            base_sal = int(round(work_val * (price or 0))) if not is_chador else int(round(chador_count * (chador_price or 0)))
            sal_val = base_sal
        else:
            sal_val = 0
            
        total_all += work_val
        total_salary += sal_val
        
        if machine not in machine_totals: machine_totals[machine] = 0
        machine_totals[machine] += work_val
        if work_val > 0:
            if machine not in machine_days: machine_days[machine] = set()
            machine_days[machine].add(clean_date(date))
            
        op_key = f"{shift} {op_name}"
        if op_key not in operator_shifts: operator_shifts[op_key] = {}
        if machine not in operator_shifts[op_key]: operator_shifts[op_key][machine] = 0
        operator_shifts[op_key][machine] += work_val
        
        op_totals[op_key] = op_totals.get(op_key, 0) + work_val
        op_salaries[op_key] = op_salaries.get(op_key, 0) + sal_val
        
        if work_val > 0:
            if op_key not in op_days: op_days[op_key] = set()
            op_days[op_key].add(clean_date(date))
            
        if op_key not in op_machine_totals: op_machine_totals[op_key] = {}
        if machine not in op_machine_totals[op_key]: op_machine_totals[op_key][machine] = 0
        op_machine_totals[op_key][machine] += work_val
        
        if op_key not in op_machine_salaries: op_machine_salaries[op_key] = {}
        if machine not in op_machine_salaries[op_key]: op_machine_salaries[op_key][machine] = 0
        op_machine_salaries[op_key][machine] += sal_val
        
        if work_val > 0:
            if op_key not in op_machine_days: op_machine_days[op_key] = {}
            if machine not in op_machine_days[op_key]: op_machine_days[op_key][machine] = set()
            op_machine_days[op_key][machine].add(clean_date(date))
            
        if is_chador:
            if op_key not in chador_counts: chador_counts[op_key] = 0
            chador_counts[op_key] += chador_count
        if machine not in sets_by_machine: sets_by_machine[machine] = {"list": [], "bonus_count": 0}
        if design and set_num: sets_by_machine[machine]["list"].append((str(design), str(set_num)))
        b_val = 0
        if bonus is not None:
            try: b_val = int(bonus)
            except: b_val = 0
        if b_val > 0: sets_by_machine[machine]["bonus_count"] += 1

    for op_key in op_machine_salaries:
        op_shift = op_key.split(' ', 1)[0]
        is_opposite = (op_shift != user_shift)
        can_see_this_op = True
        if not is_admin:
            if not show_salary: can_see_this_op = False
            if is_opposite and not show_opposite_salary: can_see_this_op = False
        
        if not can_see_this_op: continue
        
        for mach in op_machine_salaries[op_key]:
            if op_key in machine_financials and mach in machine_financials[op_key]:
                m_fin = machine_financials[op_key][mach]
                op_machine_salaries[op_key][mach] += m_fin["reward"]
                op_machine_salaries[op_key][mach] -= m_fin["penalty"]
                op_salaries[op_key] += m_fin["reward"]
                op_salaries[op_key] -= m_fin["penalty"]

    for op_key in op_salaries.keys():
        op_shift = op_key.split(' ', 1)[0]
        is_opposite = (op_shift != user_shift)
        can_see_this_op = True
        if not is_admin:
            if not show_salary: can_see_this_op = False
            if is_opposite and not show_opposite_salary: can_see_this_op = False
            
        if can_see_this_op and op_key in shift_financials:
            s_fin = shift_financials[op_key]
            op_salaries[op_key] += s_fin["reward"]
            op_salaries[op_key] -= s_fin["penalty"]

    total_salary = sum(op_salaries.values())

    sets_summary = []
    for machine, data in sets_by_machine.items():
        if not data["list"]: continue
        unique_sets = list(dict.fromkeys(data["list"]))
        rank = await get_set_monthly_rank(machine, end_str)
        sets_summary.append({"machine": machine, "count": data["bonus_count"], "sets_list": unique_sets, "rank": rank})
        
    final_machine_totals = {}
    for mach, work in machine_totals.items():
        d_cnt = len(machine_days.get(mach, []))
        if d_cnt == 0: d_cnt = 1
        final_machine_totals[mach] = {"total": work, "avg": work // d_cnt}
        
    operator_averages = {}
    for op_key, total in op_totals.items():
        d_cnt = len(op_days.get(op_key, []))
        if d_cnt == 0: d_cnt = 1
        operator_averages[op_key] = total // d_cnt
        
    op_machine_averages = {}
    for op_key, machines in op_machine_totals.items():
        op_machine_averages[op_key] = {}
        for mach, total in machines.items():
            d_cnt = len(op_machine_days.get(op_key, {}).get(mach, []))
            if d_cnt == 0: d_cnt = 1
            op_machine_averages[op_key][mach] = total // d_cnt
        
    return {"status": "ok", "month_title": m_name, "start_date": start_str, "end_date": end_str, "total_all": total_all, "total_salary": total_salary, "show_salary": show_salary, "show_opposite_salary": show_opposite_salary, "machine_totals": final_machine_totals, "operator_shifts": operator_shifts, "sets_summary": sets_summary, "chador_counts": chador_counts, "operator_averages": operator_averages, "op_machine_averages": op_machine_averages, "op_salaries": op_salaries, "op_machine_salaries": op_machine_salaries, "shift_financials": shift_financials, "machine_financials": machine_financials}

@app.get("/api/admin/search_shifts")
async def search_shifts(
    design_code: Optional[str] = None,
    set_number: Optional[int] = None,
    parts: Optional[str] = None,
    machine: Optional[str] = None,
    shift: Optional[str] = None,
    sort_order: str = "desc",
    payload: dict = Depends(verify_admin)
):
    query = "SELECT * FROM shifts WHERE 1=1"
    params = []
    if design_code:
        query += " AND design_code LIKE ?"
        params.append(f"%{design_code}%")
    if set_number is not None and set_number > 0:
        query += " AND set_number = ?"
        params.append(set_number)
    if parts:
        query += " AND parts LIKE ?"
        params.append(f"%{parts}%")
    if machine and machine != "all":
        query += " AND machine = ?"
        params.append(machine)
    if shift and shift != "all":
        query += " AND shift_type = ?"
        params.append(shift)

    order = "DESC" if sort_order == "desc" else "ASC"
    query += f" ORDER BY date {order}, id {order}"

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()

    shifts = []
    for r in rows:
        work_val = None
        if not r[12]:
            w, _ = await calculate_work_logic(r[5], r[3], r[4], r[6], r[8], r[9],
                                              r[15] if len(r) > 15 else 0,
                                              r[16] if len(r) > 16 else 0)
            work_val = w
        shifts.append({
            "id": r[0], "op_name": r[2], "date": r[3], "day_name": get_day_name(r[3]),
            "shift": r[4], "machine": r[5], "counter": r[6], "design": r[7],
            "set_num": r[8], "parts": r[9], "photo": r[10], "status": r[11],
            "zero_reason": r[12], "zero_note": r[14] if len(r) > 14 else "",
            "is_chador": r[16] if len(r) > 16 else 0,
            "chador_count": r[17] if len(r) > 17 else 0,
            "chador_invoice": r[18] if len(r) > 18 else "",
            "work": work_val
        })
    return {"status": "ok", "shifts": shifts, "total": len(shifts)}


@app.get("/", response_class=HTMLResponse)
async def read_root():
    response = FileResponse("static/index.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response
