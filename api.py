import aiosqlite
import jdatetime
import os
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

def hash_password(password: str): return hashlib.sha256(password.encode()).hexdigest()

def get_day_name(date_str: str) -> str:
    try:
        ds = clean_date(date_str)
        y, m, d = map(int, ds.split('-'))
        days = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]
        return days[jdatetime.date(y, m, d).weekday()]
    except: return ""

def clean_date(d: str) -> str:
    if not d: return ""
    d = str(d).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    d = re.sub(r'[/\\.]', '-', d.strip())
    parts = d.split('-')
    if len(parts) == 3:
        try: return f"{int(parts[0])}-{int(parts[1]):02d}-{int(parts[2]):02d}"
        except: pass
    return d

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        await db.execute("""CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT UNIQUE, password_hash TEXT, name TEXT, role TEXT DEFAULT 'operator', shift_type TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS shifts (id INTEGER PRIMARY KEY, operator_id INTEGER, operator_name TEXT, date TEXT, shift_type TEXT, machine TEXT, counter INTEGER, design_code TEXT, set_number INTEGER, parts TEXT, photo_path TEXT, status TEXT, zero_reason TEXT, admin_note TEXT, zero_note TEXT, jig_bonus INTEGER DEFAULT 0, is_chador INTEGER DEFAULT 0, chador_count INTEGER DEFAULT 0, chador_invoice TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS machines (name TEXT PRIMARY KEY, reset_period INTEGER, start_counter INTEGER DEFAULT 0)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS monthly_prices (year INTEGER, month INTEGER, price REAL, chador_price REAL DEFAULT 0, show_salary INTEGER, show_opposite_salary INTEGER DEFAULT 0, min_daily INTEGER DEFAULT 300000, min_weekly INTEGER DEFAULT 1800000, min_monthly INTEGER DEFAULT 7800000, PRIMARY KEY (year, month))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS monthly_shift_financials (year INTEGER, month INTEGER, operator_name TEXT, shift_type TEXT, reward INTEGER DEFAULT 0, reward_title TEXT, penalty INTEGER DEFAULT 0, penalty_title TEXT, PRIMARY KEY (year, month, operator_name, shift_type))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS monthly_machine_financials (year INTEGER, month INTEGER, operator_name TEXT, shift_type TEXT, machine TEXT, reward INTEGER DEFAULT 0, reward_title TEXT, penalty INTEGER DEFAULT 0, penalty_title TEXT, PRIMARY KEY (year, month, operator_name, shift_type, machine))""")
        
        for col in ["zero_note TEXT", "jig_bonus INTEGER DEFAULT 0", "is_chador INTEGER DEFAULT 0", "chador_count INTEGER DEFAULT 0", "chador_invoice TEXT"]:
            try: await db.execute(f"ALTER TABLE shifts ADD COLUMN {col};")
            except: pass
        for col in ["chador_price REAL DEFAULT 0", "show_opposite_salary INTEGER DEFAULT 0", "min_daily INTEGER DEFAULT 300000", "min_weekly INTEGER DEFAULT 1800000", "min_monthly INTEGER DEFAULT 7800000"]:
            try: await db.execute(f"ALTER TABLE monthly_prices ADD COLUMN {col};")
            except: pass
            
        for m in ['10 کله', '12 کله', 'چادری']:
            await db.execute("INSERT OR IGNORE INTO machines (name, reset_period, start_counter) VALUES (?, 5000000, 0)", (m,))
        await db.execute("INSERT OR IGNORE INTO users (phone, password_hash, name, role, shift_type) VALUES (?, ?, ?, ?, ?)", ("09120143502", hash_password("2864"), "مدیر کل", "admin", None))
        await db.commit()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db(); yield

app = FastAPI(title="Embroidery Workshop API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/app_photos", StaticFiles(directory=PHOTO_DIR), name="app_photos")

class LoginData(BaseModel): phone_number: str; password: str
class CreateUserData(BaseModel): phone_number: str; name: str; role: Literal["admin", "operator"]; password: str = Field(..., min_length=4); shift_type: Optional[Literal["صبح", "شب"]] = None
class UpdateUserData(BaseModel): name: str; shift_type: Optional[Literal["صبح", "شب"]] = None; password: Optional[str] = None
class MachineData(BaseModel): name: str; reset_period: int = 5000000; start_counter: int = 0
class UpdateMachineData(BaseModel): reset_period: int; start_counter: int

def normalize_phone(phone: str) -> str:
    phone = phone.replace(" ", "").replace("-", "")
    if phone.startswith("+98"): return "0" + phone[3:]
    if phone.startswith("98"): return "0" + phone[2:]
    return phone

def verify_token(authorization: str = Header(...)):
    try: return jwt.decode(authorization.split(" ")[1], SECRET_KEY, algorithms=[ALGORITHM])
    except: raise HTTPException(status_code=401, detail="نشست شما منقضی شده است.")

def verify_admin(payload: dict = Depends(verify_token)):
    if payload.get("role") != "admin": raise HTTPException(status_code=403, detail="دسترسی مدیریتی ندارید.")
    return payload

async def get_machine_info(machine_name):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT reset_period, start_counter FROM machines WHERE name=?", (machine_name,)) as cursor:
            return await cursor.fetchone() or (5000000, 0)

async def get_immediate_previous_shift(machine, date_str, shift_type):
    target_date = clean_date(date_str)
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM shifts WHERE machine=? AND status='approved' ORDER BY id DESC", (machine,)) as cursor:
            rows = await cursor.fetchall()
    if shift_type == 'شب':
        for r in rows:
            if clean_date(r[3]) == target_date and r[4] == 'صبح': return r
    else:
        try:
            y, m, d = map(int, target_date.split('-'))
            prev_date_str = (jdatetime.date(y, m, d) - jdatetime.timedelta(days=1)).strftime("%Y-%m-%d")
        except: return None
        for r in rows:
            if clean_date(r[3]) == prev_date_str and r[4] == 'شب': return r
    return None

async def get_last_valid_shift_before(machine, date_str, shift_type):
    target_date = clean_date(date_str)
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM shifts WHERE machine=? AND status='approved' AND zero_reason IS NULL", (machine,)) as cursor:
            rows = await cursor.fetchall()
    valid_rows = [(clean_date(r[3]), r[4], r) for r in rows]
    valid_rows.sort(key=lambda x: (x[0], '2' if x[1]=='شب' else '1'), reverse=True)
    for r_date, r_shift, r in valid_rows:
        if r_date < target_date: return r
        elif r_date == target_date and shift_type == 'شب' and r_shift == 'صبح': return r
    return None

def calculate_work(prev_counter, curr_counter, reset_period, is_first_shift, start_counter):
    if prev_counter is None:
        if is_first_shift: prev_counter = start_counter
        else: return None 
    if curr_counter >= prev_counter: work = curr_counter - prev_counter
    else: work = (reset_period - prev_counter) + curr_counter
    if not is_first_shift and work > 600000: return None
    return work

async def calculate_work_logic(machine, date_str, shift_type, counter, set_number, parts_str, stored_bonus=0, is_chador=0):
    if stored_bonus is None: stored_bonus = 0
    if is_chador: return 0, False
    target_date = clean_date(date_str)
    reset, start = await get_machine_info(machine)
    immediate_prev = await get_immediate_previous_shift(machine, target_date, shift_type)
    prev_counter = None; is_first = False
    if immediate_prev:
        if immediate_prev[12]: 
            last_valid = await get_last_valid_shift_before(machine, target_date, shift_type)
            if last_valid: prev_counter = last_valid[6]
            else: is_first = True 
        else: prev_counter = immediate_prev[6]
    else:
        last_valid = await get_last_valid_shift_before(machine, target_date, shift_type)
        if last_valid: prev_counter = last_valid[6]
        else: is_first = True 
    work = calculate_work(prev_counter, counter, reset, is_first, start)
    if work is not None: work += stored_bonus
    return work, is_first

async def get_set_monthly_rank(machine, date_str, design_code=None, set_number=None):
    last_date = clean_date(date_str)
    if not last_date: return 1
    try:
        y, m, d = map(int, last_date.split('-')); first_day_str = f"{y}-{m:02d}-01"
    except: return 1
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT date, jig_bonus FROM shifts WHERE machine=? AND status='approved'", (machine,)) as cursor: rows = await cursor.fetchall()
    count = 0
    for r in rows:
        c_date = clean_date(r[0])
        if c_date and first_day_str <= c_date <= last_date:
            bonus_val = int(r[1]) if r[1] else 0
            if bonus_val > 0: count += 1
    return count if count > 0 else 1

async def check_missing_shifts(operator_id: int, new_date_str: str, machine: str, new_shift: str):
    new_date_clean = clean_date(new_date_str)
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT date FROM shifts WHERE operator_id=? AND machine=? AND shift_type=? AND status IN ('approved', 'pending', 'rejected')", (operator_id, machine, new_shift)) as cursor:
            rows = await cursor.fetchall()
    recorded_dates = {clean_date(r[0]) for r in rows if r[0]}
    if not recorded_dates: return
    min_date_str = min(recorded_dates)
    try:
        y, m, d = map(int, min_date_str.split('-')); curr = jdatetime.date(y, m, d)
        y2, m2, d2 = map(int, new_date_clean.split('-')); target_date = jdatetime.date(y2, m2, d2)
    except: return
    while curr < target_date:
        curr_str = curr.strftime("%Y-%m-%d")
        if curr_str not in recorded_dates and curr.weekday() != 6: 
            raise HTTPException(400, f"شما شیفت {new_shift} تاریخ {curr_str} را ثبت نکرده‌اید.")
        curr += jdatetime.timedelta(days=1)

@app.get("/api/today")
async def get_today(): return {"date": jdatetime.date.today().strftime("%Y-%m-%d")}

@app.post("/api/login")
async def login(data: LoginData):
    clean_phone = normalize_phone(data.phone_number)
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, name, role, shift_type FROM users WHERE phone=? AND password_hash=?", (clean_phone, hash_password(data.password))) as cursor:
            row = await cursor.fetchone()
            if not row: raise HTTPException(status_code=401, detail="شماره یا رمز اشتباه است")
            user_id, name, role, shift_type = row
            payload = {"sub": str(user_id), "name": name, "role": role, "exp": datetime.now(timezone.utc) + timedelta(days=3650)}
            if role == 'operator': payload["shift_type"] = shift_type
            return {"status": "ok", "token": jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM), "name": name, "role": role, "shift_type": shift_type}

@app.post("/api/change_password")
async def change_password(data: dict, payload: dict = Depends(verify_token)):
    old_pass, new_pass = data.get("old_password"), data.get("new_password")
    if not old_pass or not new_pass or len(new_pass) < 4: raise HTTPException(400, "رمز جدید نامعتبر است.")
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id FROM users WHERE id=? AND password_hash=?", (int(payload["sub"]), hash_password(old_pass))) as cursor:
            if not await cursor.fetchone(): raise HTTPException(400, "رمز فعلی اشتباه است.")
        await db.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new_pass), int(payload["sub"])))
        await db.commit()
    return {"status": "ok"}

@app.get("/api/machines")
async def get_machines_public(payload: dict = Depends(verify_token)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT name FROM machines") as cursor: return {"machines": [r[0] for r in await cursor.fetchall()]}

@app.get("/api/operator/check_shift")
async def check_shift(date: str, machine: str, shift_type: Optional[str] = None, target_operator_id: Optional[int] = None, payload: dict = Depends(verify_token)):
    date = clean_date(date)
    op_id = target_operator_id if target_operator_id else int(payload["sub"])
    effective_shift = shift_type if shift_type else payload.get("shift_type")
    if not effective_shift: raise HTTPException(400, "نوع شیفت نامشخص است.")
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, operator_id, operator_name, status, date, shift_type FROM shifts WHERE machine=? AND shift_type=? AND status IN ('approved', 'pending', 'rejected')", (machine, effective_shift)) as cursor:
            rows = await cursor.fetchall()
    for row in rows:
        if clean_date(row[4]) == date:
            return {"status": "exists", "shift_id": row[0], "owner_id": row[1], "owner_name": row[2], "is_mine": (str(row[1]) == str(op_id)), "shift_status": row[3]}
    return {"status": "new"}

@app.get("/api/operator/shift/{shift_id}")
async def get_shift_detail(shift_id: int, payload: dict = Depends(verify_token)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)) as cursor:
            row = await cursor.fetchone()
            if row: return {"status": "ok", "data": {"id": row[0], "machine": row[5], "counter": row[6], "design": row[7], "set_number": row[8], "parts": row[9], "photo_path": row[10], "date": row[3], "zero_reason": row[12], "zero_note": row[14] if len(row)>14 else "", "request_bonus": row[15] if len(row)>15 else 0, "is_chador": row[16] if len(row)>16 else 0, "chador_count": row[17] if len(row)>17 else 0, "chador_invoice": row[18] if len(row)>18 else ""}}
            raise HTTPException(404, "یافت نشد")

@app.get("/api/operator/last_shifts")
async def get_last_shifts(machine: Optional[str] = None, target_operator_id: Optional[int] = None, target_shift_type: Optional[str] = None, payload: dict = Depends(verify_token)):
    op_id = target_operator_id if target_operator_id else int(payload["sub"])
    shift = target_shift_type if target_shift_type else payload.get("shift_type")
    async with aiosqlite.connect(DB_NAME) as db:
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
        y, m, d = map(int, date.split('-')); curr_date = jdatetime.date(y, m, d)
        if 'شب' in shift_type: expected_date = date; expected_shift = 'صبح'
        else: expected_date = (curr_date - jdatetime.timedelta(days=1)).strftime("%Y-%m-%d"); expected_shift = 'شب'
    except: return {"exists": False}
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT date, shift_type, machine, counter, design_code, set_number, parts, status, zero_reason, operator_name, jig_bonus FROM shifts WHERE machine LIKE ? AND shift_type LIKE ?", (f"%{machine.strip()}%", f"%{expected_shift.strip()}%")) as cursor:
            rows = await cursor.fetchall()
    for r in rows:
        if clean_date(r[0]) == expected_date:
            if r[7] == 'rejected': return {"exists": False, "rejected": True, "expected_date": expected_date, "expected_shift": expected_shift}
            return {"exists": True, "data": {"date": r[0], "day_name": get_day_name(r[0]), "shift_type": r[1], "machine": r[2], "counter": r[3], "design": r[4], "set_number": r[5], "parts": r[6], "status": r[7], "zero_reason": r[8], "operator_name": r[9], "jig_bonus": r[10] if len(r)>10 and r[10] else 0}}
    return {"exists": False, "expected_date": expected_date, "expected_shift": expected_shift}

@app.get("/api/operator/suggest_date")
async def suggest_date(machine: Optional[str] = None, target_operator_id: Optional[str] = None, target_shift_type: Optional[str] = None, payload: dict = Depends(verify_token)):
    op_id = int(target_operator_id) if target_operator_id else int(payload["sub"])
    shift = target_shift_type if target_shift_type else payload.get("shift_type")
    async with aiosqlite.connect(DB_NAME) as db:
        query = "SELECT date FROM shifts WHERE operator_id=? AND shift_type=? "
        params = [op_id, shift]
        if machine: query += "AND machine=? "; params.append(machine)
        async with db.execute(query, params) as cursor: rows = await cursor.fetchall()
    valid_dates = []
    for r in rows:
        if not r[0]: continue
        try:
            y, m, d = map(int, clean_date(r[0]).split('-')); valid_dates.append(jdatetime.date(y, m, d))
        except: pass
    if not valid_dates: return {"date": jdatetime.date.today().strftime("%Y-%m-%d")}
    valid_dates.sort()
    today = jdatetime.date.today()
    check_date = valid_dates[0]
    while check_date <= today:
        if check_date not in valid_dates and check_date.weekday() != 6: return {"date": check_date.strftime("%Y-%m-%d")}
        check_date += jdatetime.timedelta(days=1)
    next_date = max(valid_dates) + jdatetime.timedelta(days=1)
    while next_date.weekday() == 6: next_date += jdatetime.timedelta(days=1)
    return {"date": next_date.strftime("%Y-%m-%d")}

@app.post("/api/operator/preview_work")
async def preview_work(machine: str = Form(...), date: str = Form(...), counter: int = Form(...), set_number: int = Form(0), parts: str = Form(""), is_chador: str = Form("false"), request_bonus: str = Form("false"), target_operator_id: Optional[int] = None, target_shift_type: Optional[str] = None, payload: dict = Depends(verify_token)):
    try:
        date = clean_date(date)
        op_id = target_operator_id if target_operator_id else int(payload["sub"])
        shift = target_shift_type if target_shift_type else payload.get("shift_type")
        await check_missing_shifts(op_id, date, machine, shift)
        is_chador_val = 1 if is_chador == "true" else 0
        immediate_prev = await get_immediate_previous_shift(machine, date, shift)
        pending_warning = False; missing_prev_error = False
        if not immediate_prev:
            if shift == 'شب': q, p = "SELECT id FROM shifts WHERE machine=? AND date=? AND shift_type='صبح'", (machine, date)
            else:
                try:
                    y, m, d = map(int, date.split('-')); prev_d = (jdatetime.date(y, m, d) - jdatetime.timedelta(days=1)).strftime("%Y-%m-%d")
                    q, p = "SELECT id FROM shifts WHERE machine=? AND date=? AND shift_type='شب'", (machine, prev_d)
                except: q, p = "", ()
            has_prev_record = False
            if q:
                async with aiosqlite.connect(DB_NAME) as db:
                    async with db.execute(q, p) as cursor:
                        if await cursor.fetchone(): has_prev_record = True
            if not has_prev_record: missing_prev_error = True
        work, is_first = await calculate_work_logic(machine, date, shift, counter, set_number, parts, is_chador=is_chador_val)
        if missing_prev_error: work = None
        if work is not None and work < 0: raise HTTPException(400, "کارکرد منفی است!")
        if work is not None and work > 600000: raise HTTPException(400, "کارکرد غیرعادی است.")
        return {"work": work, "pending_warning": pending_warning, "missing_prev_error": missing_prev_error}
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(500, str(e))

@app.post("/api/submit_shift")
async def submit_shift(machine: str = Form(...), date: str = Form(...), design_code: str = Form(""), counter: int = Form(...), set_number: int = Form(0), parts: str = Form(""), photo: UploadFile = File(...), request_bonus: str = Form("false"), is_chador: str = Form("false"), chador_count: int = Form(0), chador_invoice: Optional[str] = Form(None), payload: dict = Depends(verify_token)):
    if payload.get("role") != "operator": raise HTTPException(403, "دسترسی غیرمجاز")
    try:
        date = clean_date(date)
        await check_missing_shifts(int(payload["sub"]), date, machine, payload["shift_type"])
        
        if not photo:
            raise HTTPException(400, "عکس دستگاه الزامی است.")
            
        contents = await photo.read()
        if len(contents) > 5 * 1024 * 1024: 
            raise HTTPException(400, "حجم عکس بیشتر از ۵ مگابایت است. لطفا عکس کم‌حجم‌تری انتخاب کنید.")
            
        safe_name = machine.replace(" ", "-")
        photo_path = f"{PHOTO_DIR}/{date}_{payload['shift_type']}_{payload['sub']}_{safe_name}.jpg"
        with open(photo_path, "wb") as buffer: buffer.write(contents)
        
        is_chador_val = 1 if is_chador == "true" else 0
        work, _ = await calculate_work_logic(machine, date, payload["shift_type"], counter, set_number, parts, is_chador=is_chador_val)
        if work is not None and work < 0: raise HTTPException(400, "کارکرد منفی است!")
        if work is not None and work > 600000: raise HTTPException(400, "کارکرد غیرعادی است.")
        
        bonus_val = 60000 if request_bonus == "true" else 0
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT id, date FROM shifts WHERE machine=? AND shift_type=? AND status IN ('approved', 'pending', 'rejected')", (machine, payload["shift_type"])) as cursor:
                for r in await cursor.fetchall():
                    if clean_date(r[1]) == date: raise HTTPException(400, "قبلا ثبت شده است.")
            await db.execute("INSERT INTO shifts (operator_id, operator_name, date, shift_type, machine, counter, design_code, set_number, parts, photo_path, status, jig_bonus, is_chador, chador_count, chador_invoice) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (payload["sub"], payload["name"], date, payload["shift_type"], machine, counter, design_code, set_number, parts, photo_path, "pending", bonus_val, is_chador_val, chador_count, chador_invoice))
            await db.commit()
        return {"status": "ok"}
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(500, f"خطای سرور: {str(e)}")

@app.post("/api/operator/submit_zero")
async def submit_zero(data: dict, payload: dict = Depends(verify_token)):
    try:
        date = clean_date(data.get("date")); machine = data.get("machine"); shift_type = data.get("shift_type", payload.get("shift_type"))
        await check_missing_shifts(int(payload["sub"]), date, machine, shift_type)
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT id, date FROM shifts WHERE machine=? AND shift_type=? AND status IN ('approved', 'pending', 'rejected')", (machine, shift_type)) as cursor:
                for r in await cursor.fetchall():
                    if clean_date(r[1]) == date: raise HTTPException(400, "قبلا ثبت شده است.")
            await db.execute("INSERT INTO shifts (operator_id, operator_name, date, shift_type, machine, counter, design_code, set_number, parts, photo_path, status, zero_reason, zero_note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (payload["sub"], payload["name"], date, shift_type, machine, 0, "000", 0, "", "", "pending", data.get("zero_reason"), data.get("zero_note", "")))
            await db.commit()
        return {"status": "ok"}
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(500, str(e))

@app.get("/api/admin/all_shifts")
async def get_all_shifts(start_date: str, end_date: str, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM shifts WHERE date BETWEEN ? AND ? ORDER BY date DESC, CASE shift_type WHEN 'صبح' THEN 1 ELSE 2 END ASC", (clean_date(start_date), clean_date(end_date))) as cursor:
            rows = await cursor.fetchall()
    shifts = []
    for r in rows:
        work_val = None if r[12] else (await calculate_work_logic(r[5], r[3], r[4], r[6], r[8], r[9], r[15] if len(r)>15 else 0, r[16] if len(r)>16 else 0))[0]
        shifts.append({"id": r[0], "op_id": r[1], "op_name": r[2], "date": r[3], "day_name": get_day_name(r[3]), "shift": r[4], "machine": r[5], "counter": r[6], "design": r[7], "set_num": r[8], "parts": r[9], "photo": r[10], "status": r[11], "zero_reason": r[12], "zero_note": r[14] if len(r)>14 else "", "is_chador": r[16] if len(r)>16 else 0, "chador_count": r[17] if len(r)>17 else 0, "chador_invoice": r[18] if len(r)>18 else "", "work": work_val})
    return {"status": "ok", "shifts": shifts}

@app.get("/api/admin/pending_shifts")
async def get_pending_shifts(payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM shifts WHERE status='pending' ORDER BY date DESC, CASE shift_type WHEN 'صبح' THEN 1 ELSE 2 END ASC") as cursor:
            rows = await cursor.fetchall()
    result = []
    for r in rows:
        work_val = None if r[12] else (await calculate_work_logic(r[5], r[3], r[4], r[6], r[8], r[9], r[15] if len(r)>15 else 0, r[16] if len(r)>16 else 0))[0]
        prev_info = None
        immediate_prev = await get_immediate_previous_shift(r[5], r[3], r[4])
        if immediate_prev:
            prev_work = None if immediate_prev[12] else (await calculate_work_logic(immediate_prev[5], immediate_prev[3], immediate_prev[4], immediate_prev[6], immediate_prev[8], immediate_prev[9], immediate_prev[15] if len(immediate_prev)>15 else 0, immediate_prev[16] if len(immediate_prev)>16 else 0))[0]
            prev_info = {"date": immediate_prev[3], "shift_type": immediate_prev[4], "counter": immediate_prev[6], "design": immediate_prev[7], "set_num": immediate_prev[8], "parts": immediate_prev[9], "status": immediate_prev[11], "work": prev_work, "zero_reason": immediate_prev[12]}
        rank = await get_set_monthly_rank(r[5], r[3])
        result.append({"id": r[0], "op_name": r[2], "date": r[3], "day_name": get_day_name(r[3]), "shift": r[4], "machine": r[5], "counter": r[6], "design": r[7], "set_num": r[8], "parts": r[9], "photo": r[10], "status": r[11], "zero_reason": r[12], "zero_note": r[14] if len(r)>14 else "", "work": work_val, "potential_bonus": r[15] if len(r)>15 else 0, "is_chador": r[16] if len(r)>16 else 0, "chador_count": r[17] if len(r)>17 else 0, "monthly_set_rank": rank, "prev_info": prev_info})
    return {"status": "ok", "shifts": result}

@app.post("/api/admin/approve_shift/{shift_id}")
async def approve_shift(shift_id: int, apply_bonus: bool = False, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE shifts SET status='approved', jig_bonus=? WHERE id=?", (60000 if apply_bonus else 0, shift_id))
        await db.commit(); return {"status": "ok"}

@app.post("/api/admin/reject_shift/{shift_id}")
async def reject_shift(shift_id: int, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE shifts SET status='rejected' WHERE id=?", (shift_id,)); await db.commit(); return {"status": "ok"}

@app.delete("/api/admin/delete_shift/{shift_id}")
async def delete_shift(shift_id: int, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM shifts WHERE id=?", (shift_id,)); await db.commit(); return {"status": "ok"}

@app.post("/api/admin/manual_shift")
async def manual_shift(operator_id: int = Form(...), date: str = Form(...), shift_type: str = Form(...), machine: str = Form(...), counter: int = Form(...), design_code: str = Form(""), set_number: int = Form(0), parts: str = Form(""), is_chador: str = Form("false"), chador_count: int = Form(0), chador_invoice: Optional[str] = Form(None), zero_reason: Optional[str] = Form(None), zero_note: Optional[str] = Form(None), request_bonus: str = Form("false"), photo: Optional[UploadFile] = File(None), payload: dict = Depends(verify_admin)):
    try:
        date = clean_date(date)
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT name FROM users WHERE id=?", (operator_id,)) as cursor:
                row = await cursor.fetchone(); op_name = row[0] if row else "نامشخص"
            final_photo = ""
            if photo:
                safe_name = machine.replace(" ", "-"); photo_path = f"{PHOTO_DIR}/{date}_{shift_type}_{operator_id}_{safe_name}.jpg"
                contents = await photo.read()
                if len(contents) > 5 * 1024 * 1024: raise HTTPException(400, "حجم عکس زیاد است")
                with open(photo_path, "wb") as buffer: buffer.write(contents)
                final_photo = photo_path
            await db.execute("INSERT INTO shifts (operator_id, operator_name, date, shift_type, machine, counter, design_code, set_number, parts, photo_path, status, admin_note, zero_reason, zero_note, jig_bonus, is_chador, chador_count, chador_invoice) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (operator_id, op_name, date, shift_type, machine, counter, design_code, set_number, parts, final_photo, "approved", "ثبت دستی", zero_reason, zero_note, 60000 if request_bonus == "true" else 0, 1 if is_chador == "true" else 0, chador_count, chador_invoice))
            await db.commit(); return {"status": "ok"}
    except Exception as e: raise HTTPException(500, str(e))

@app.put("/api/admin/edit_shift/{shift_id}")
async def admin_edit_shift(shift_id: int, operator_id: int = Form(...), date: str = Form(...), shift_type: str = Form(...), machine: str = Form(...), counter: int = Form(...), design_code: str = Form(""), set_number: int = Form(0), parts: str = Form(""), is_chador: str = Form("false"), chador_count: int = Form(0), chador_invoice: Optional[str] = Form(None), zero_reason: Optional[str] = Form(None), zero_note: Optional[str] = Form(None), request_bonus: str = Form("false"), photo: Optional[UploadFile] = File(None), payload: dict = Depends(verify_admin)):
    try:
        date = clean_date(date)
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT name FROM users WHERE id=?", (operator_id,)) as cursor:
                row = await cursor.fetchone(); op_name = row[0] if row else "نامشخص"
            async with db.execute("SELECT photo_path FROM shifts WHERE id=?", (shift_id,)) as c:
                old_row = await c.fetchone(); final_photo = old_row[0] if old_row and old_row[0] else ""
            if photo:
                safe_name = machine.replace(" ", "-"); photo_path = f"{PHOTO_DIR}/{date}_{shift_type}_{operator_id}_{safe_name}.jpg"
                contents = await photo.read()
                if len(contents) > 5 * 1024 * 1024: raise HTTPException(400, "حجم عکس زیاد است")
                with open(photo_path, "wb") as buffer: buffer.write(contents)
                final_photo = photo_path
            await db.execute("UPDATE shifts SET operator_id=?, operator_name=?, machine=?, date=?, shift_type=?, counter=?, design_code=?, set_number=?, parts=?, photo_path=?, is_chador=?, chador_count=?, chador_invoice=?, zero_reason=?, zero_note=?, jig_bonus=?, status='approved' WHERE id=?", (operator_id, op_name, machine, date, shift_type, counter, design_code, set_number, parts, final_photo, 1 if is_chador == "true" else 0, chador_count, chador_invoice, zero_reason, zero_note, 60000 if request_bonus == "true" else 0, shift_id))
            await db.commit(); return {"status": "ok"}
    except Exception as e: raise HTTPException(500, str(e))

@app.get("/api/admin/machines")
async def get_machines_admin(payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT name, reset_period, start_counter FROM machines") as cursor:
            return {"machines": [{"name": r[0], "reset_period": r[1], "start_counter": r[2]} for r in await cursor.fetchall()]}

@app.post("/api/admin/machines")
async def add_machine(data: MachineData, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            await db.execute("INSERT INTO machines (name, reset_period, start_counter) VALUES (?, ?, ?)", (data.name, data.reset_period, data.start_counter))
            await db.commit(); return {"status": "ok"}
        except: raise HTTPException(400, "چرخ تکراری است")

@app.put("/api/admin/machines/{machine_name}")
async def update_machine(machine_name: str, data: UpdateMachineData, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE machines SET reset_period=?, start_counter=? WHERE name=?", (data.reset_period, data.start_counter, machine_name)); await db.commit(); return {"status": "ok"}

@app.get("/api/admin/duration_analysis")
async def get_duration_analysis(year: int, month: int, payload: dict = Depends(verify_admin)):
    start_str = f"{year}-{month:02d}-01"; days_in_month = 31 if month <= 6 else (30 if month <= 11 else (29 if not jdatetime.date(year, 12, 1).isleap() else 30)); end_str = jdatetime.date(year, month, days_in_month).strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT machine, design_code, set_number, COUNT(id) FROM shifts WHERE status='approved' AND zero_reason IS NULL AND date >= ? AND date <= ? GROUP BY machine, design_code, set_number", (start_str, end_str)) as cursor:
            rows = await cursor.fetchall()
    stats = {}
    for r in rows:
        mach, design, set_num, shifts = r; days_taken = shifts / 2.0
        if mach not in stats: stats[mach] = {}
        if design not in stats[mach]: stats[mach][design] = {'shifts': 0, 'days': 0, 'sets': 0}
        stats[mach][design]['shifts'] += shifts; stats[mach][design]['days'] += days_taken; stats[mach][design]['sets'] += 1
    result = {}
    for mach, designs in stats.items():
        result[mach] = {}
        for design, d in designs.items():
            result[mach][design] = {"avg_shifts": round(d['shifts'] / d['sets'], 1), "avg_days": round(d['days'] / d['sets'], 1), "total_sets": d['sets'], "total_shifts": d['shifts']}
    return {"status": "ok", "data": result}

@app.get("/api/admin/search_shifts")
async def search_shifts(design_code: Optional[str] = None, set_number: Optional[int] = None, parts: Optional[str] = None, machine: Optional[str] = None, shift: Optional[str] = None, start_date: Optional[str] = None, end_date: Optional[str] = None, sort_order: str = "desc", payload: dict = Depends(verify_admin)):
    query = "SELECT * FROM shifts WHERE 1=1"; params = []
    if design_code: query += " AND design_code LIKE ?"; params.append(f"%{design_code}%")
    if set_number is not None and set_number > 0: query += " AND set_number = ?"; params.append(set_number)
    if parts: query += " AND parts LIKE ?"; params.append(f"%{parts}%")
    if machine and machine != "all": query += " AND machine = ?"; params.append(machine)
    if shift and shift != "all": query += " AND shift_type = ?"; params.append(shift)
    if start_date: query += " AND date >= ?"; params.append(clean_date(start_date))
    if end_date: query += " AND date <= ?"; params.append(clean_date(end_date))
    query += f" ORDER BY date {('DESC' if sort_order == 'desc' else 'ASC')}, CASE shift_type WHEN 'صبح' THEN 1 ELSE 2 END ASC"
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(query, params) as cursor: rows = await cursor.fetchall()
    shifts = []
    for r in rows:
        work_val = None if r[12] else (await calculate_work_logic(r[5], r[3], r[4], r[6], r[8], r[9], r[15] if len(r)>15 else 0, r[16] if len(r)>16 else 0))[0]
        shifts.append({"id": r[0], "op_name": r[2], "date": r[3], "day_name": get_day_name(r[3]), "shift": r[4], "machine": r[5], "counter": r[6], "design": r[7], "set_num": r[8], "parts": r[9], "photo": r[10], "status": r[11], "zero_reason": r[12], "zero_note": r[14] if len(r)>14 else "", "is_chador": r[16] if len(r)>16 else 0, "chador_count": r[17] if len(r)>17 else 0, "chador_invoice": r[18] if len(r)>18 else "", "work": work_val})
    return {"status": "ok", "shifts": shifts, "total": len(shifts)}

@app.get("/api/admin/users")
async def get_users(payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, name, phone, role, shift_type FROM users") as cursor:
            return {"users": [{"id": r[0], "name": r[1], "phone": r[2], "role": r[3], "shift_type": r[4]} for r in await cursor.fetchall()]}

@app.post("/api/admin/create_user")
async def create_user(data: CreateUserData, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            await db.execute("INSERT INTO users (phone, password_hash, name, role, shift_type) VALUES (?, ?, ?, ?, ?)", (normalize_phone(data.phone_number), hash_password(data.password), data.name, data.role, data.shift_type if data.role == "operator" else None))
            await db.commit(); return {"status": "ok"}
        except: raise HTTPException(400, "شماره تکراری است")

@app.put("/api/admin/users/{user_id}")
async def update_user(user_id: int, data: UpdateUserData, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        if data.password: await db.execute("UPDATE users SET name=?, shift_type=?, password_hash=? WHERE id=?", (data.name, data.shift_type, hash_password(data.password), user_id))
        else: await db.execute("UPDATE users SET name=?, shift_type=? WHERE id=?", (data.name, data.shift_type, user_id))
        await db.commit(); return {"status": "ok"}

@app.get("/api/get_weeks")
async def get_weeks(year: int, month: int, payload: dict = Depends(verify_token)):
    month_names = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور', 'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند']; m_name = month_names[month-1]
    try: first_day = jdatetime.date(year, month, 1)
    except: return {"weeks": []}
    wd = first_day.weekday(); start_of_week1 = first_day + jdatetime.timedelta(days=1) if wd == 6 else first_day - jdatetime.timedelta(days=wd)
    days_in_month = 31 if month <= 6 else (30 if month <= 11 else (29 if not jdatetime.date(year, 12, 1).isleap() else 30)); last_day = jdatetime.date(year, month, days_in_month)
    weeks = []; curr = start_of_week1; week_num = 1
    while curr <= last_day:
        end_of_week = curr + jdatetime.timedelta(days=6); overlap_days = (min(end_of_week, last_day) - max(curr, first_day)).days + 1
        if overlap_days > 0:
            if not (overlap_days == 1 and curr < first_day):
                label = f"هفته {['اول','دوم','سوم','چهارم','پنجم','ششم'][week_num-1]}"
                if curr.month != month: label += f" ({overlap_days} روز اول {m_name})"
                elif end_of_week.month != month: label += f" ({overlap_days} روز آخر {m_name})"
                weeks.append({"value": f"{week_num}", "label": label}); week_num += 1
        curr = end_of_week + jdatetime.timedelta(days=1)
    return {"weeks": weeks}

@app.get("/api/admin/salary_settings")
async def get_salary_settings(year: int, month: int, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT price, chador_price, show_salary, show_opposite_salary, min_daily, min_weekly, min_monthly FROM monthly_prices WHERE year=? AND month=?", (year, month)) as cursor:
            row = await cursor.fetchone()
            if row: return {"price": row[0], "chador_price": row[1], "show_salary": bool(row[2]), "show_opposite_salary": bool(row[3]), "min_daily": row[4], "min_weekly": row[5], "min_monthly": row[6]}
    return {"price": 0, "chador_price": 0, "show_salary": False, "show_opposite_salary": False, "min_daily": 300000, "min_weekly": 1800000, "min_monthly": 7800000}

@app.post("/api/admin/salary_settings")
async def save_salary_settings(year: int, month: int, price: float, chador_price: float, show_salary: bool, show_opposite_salary: bool, min_daily: int = 300000, min_weekly: int = 1800000, min_monthly: int = 7800000, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO monthly_prices (year, month, price, chador_price, show_salary, show_opposite_salary, min_daily, min_weekly, min_monthly) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (year, month, price, chador_price, 1 if show_salary else 0, 1 if show_opposite_salary else 0, min_daily, min_weekly, min_monthly))
        await db.commit(); return {"status": "ok"}

@app.get("/api/admin/monthly_financials")
async def get_monthly_financials(year: int, month: int, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT name, shift_type FROM users WHERE role='operator'") as cursor: users = await cursor.fetchall()
        async with db.execute("SELECT DISTINCT operator_name, shift_type, machine FROM shifts WHERE status='approved' AND date LIKE ?", (f"{year}-{month:02d}-%",)) as cursor: worked_machines = await cursor.fetchall()
        machines_by_op = {}
        for wm in worked_machines:
            machines_by_op.setdefault(f"{wm[1]} {wm[0]}", set()).add(wm[2])
        result = []
        for u in users:
            op_name, op_shift = u; key = f"{op_shift} {op_name}"
            async with db.execute("SELECT reward, reward_title, penalty, penalty_title FROM monthly_shift_financials WHERE year=? AND month=? AND operator_name=? AND shift_type=?", (year, month, op_name, op_shift)) as cursor:
                fin = await cursor.fetchone(); r = fin[0] if fin else 0; r_t = fin[1] if fin else ""; p = fin[2] if fin else 0; p_t = fin[3] if fin else ""
            machines_data = []
            for mach in machines_by_op.get(key, []):
                async with db.execute("SELECT reward, reward_title, penalty, penalty_title FROM monthly_machine_financials WHERE year=? AND month=? AND operator_name=? AND shift_type=? AND machine=?", (year, month, op_name, op_shift, mach)) as cursor:
                    m_fin = await cursor.fetchone(); machines_data.append({"machine": mach, "reward": m_fin[0] if m_fin else 0, "reward_title": m_fin[1] if m_fin else "", "penalty": m_fin[2] if m_fin else 0, "penalty_title": m_fin[3] if m_fin else ""})
            result.append({"op_name": op_name, "shift_type": op_shift, "reward": r, "reward_title": r_t, "penalty": p, "penalty_title": p_t, "machines": machines_data})
        return {"status": "ok", "details": result}

@app.post("/api/admin/monthly_financials")
async def save_monthly_financials(data: dict, year: int, month: int, payload: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM monthly_shift_financials WHERE year=? AND month=?", (year, month))
        await db.execute("DELETE FROM monthly_machine_financials WHERE year=? AND month=?", (year, month))
        for shift_item in data.get("shift_financials", []):
            await db.execute("INSERT INTO monthly_shift_financials (year, month, operator_name, shift_type, reward, reward_title, penalty, penalty_title) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (year, month, shift_item["op_name"], shift_item["shift_type"], shift_item.get("reward", 0), shift_item.get("reward_title", ""), shift_item.get("penalty", 0), shift_item.get("penalty_title", "")))
        for mach_item in data.get("machine_financials", []):
            await db.execute("INSERT INTO monthly_machine_financials (year, month, operator_name, shift_type, machine, reward, reward_title, penalty, penalty_title) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (year, month, mach_item["op_name"], mach_item["shift_type"], mach_item["machine"], mach_item.get("reward", 0), mach_item.get("reward_title", ""), mach_item.get("penalty", 0), mach_item.get("penalty_title", "")))
        await db.commit(); return {"status": "ok"}

@app.get("/api/reports/weekly")
async def get_weekly_report(year: int, month: int, week_num: int, payload: dict = Depends(verify_token)):
    month_names = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور', 'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند']
    week_names = ['اول', 'دوم', 'سوم', 'چهارم', 'پنجم', 'ششم']
    try: first_day = jdatetime.date(year, month, 1)
    except: return {"status": "empty"}
    wd = first_day.weekday(); start_of_week1 = first_day + jdatetime.timedelta(days=1) if wd == 6 else first_day - jdatetime.timedelta(days=wd)
    days_in_month = 31 if month <= 6 else (30 if month <= 11 else (29 if not jdatetime.date(year, 12, 1).isleap() else 30)); last_day = jdatetime.date(year, month, days_in_month)
    target_start = None; target_end = None; curr = start_of_week1; w_num = 1
    while curr <= last_day:
        end_of_week = curr + jdatetime.timedelta(days=6); overlap_days = (min(end_of_week, last_day) - max(curr, first_day)).days + 1
        if overlap_days > 0:
            if not (overlap_days == 1 and curr < first_day):
                if w_num == week_num: target_start = curr; target_end = end_of_week; break
                w_num += 1
        curr = end_of_week + jdatetime.timedelta(days=1)
    if not target_start: return {"status": "empty"}
    start_str = target_start.strftime("%Y-%m-%d"); end_str = target_end.strftime("%Y-%m-%d"); week_title = f"هفته {week_names[week_num-1]} {month_names[month-1]}"
    
    is_admin = payload.get("role") == "admin"
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT price, chador_price, show_salary, show_opposite_salary, min_daily, min_weekly FROM monthly_prices WHERE year=? AND month=?", (year, month)) as c:
            price_row = await c.fetchone()
        price = price_row[0] if price_row else 0; chador_price = price_row[1] if price_row else 0
        show_salary = True if is_admin else (bool(price_row[2]) if price_row else False); show_opposite_salary = True if is_admin else (bool(price_row[3]) if price_row else False)
        min_daily = price_row[4] if price_row else 300000; min_weekly = price_row[5] if price_row else 1800000
        async with db.execute("SELECT operator_name, shift_type, machine, counter, design_code, set_number, date, jig_bonus, id, parts, is_chador, chador_count, zero_reason FROM shifts WHERE status='approved'") as cursor: all_rows = await cursor.fetchall()
    rows = [r for r in all_rows if start_str <= clean_date(r[6]) <= end_str]
    if not rows: return {"status": "empty", "week_title": week_title, "start_date": start_str, "end_date": end_str, "min_daily": min_daily, "min_weekly": min_weekly}
    
    total_all = 0; total_salary = 0; machine_totals = {}; machine_days = {}; operator_shifts = {}; sets_by_machine = {}
    op_totals = {}; op_days = {}; op_machine_totals = {}; op_machine_days = {}; op_salaries = {}; op_machine_salaries = {}
    chador_counts = {}; chador_salaries = {}; clothes_salaries = {}; user_shift = payload.get("shift_type")

    for r in rows:
        op_name, shift, machine, counter, design, set_num, date, bonus, sid, parts, is_chador, chador_count, zero_reason = r
        machine = machine or ""; chador_count = chador_count or 0; is_chador = is_chador or 0
        work_val = 0 if zero_reason else (await calculate_work_logic(machine, date, shift, counter, set_num, parts, bonus, is_chador))[0] or 0
        is_opposite = (shift != user_shift); can_see_salary = True
        if not is_admin:
            if not show_salary: can_see_salary = False
            if is_opposite and not show_opposite_salary: can_see_salary = False
        op_key = f"{shift} {op_name}"; sal_val = 0
        if can_see_salary:
            if is_chador:
                sal_val = int(round(chador_count * (chador_price or 0))); chador_salaries[op_key] = chador_salaries.get(op_key, 0) + sal_val; chador_counts[op_key] = chador_counts.get(op_key, 0) + chador_count
            else:
                sal_val = int(round(work_val * (price or 0))); clothes_salaries[op_key] = clothes_salaries.get(op_key, 0) + sal_val
        total_all += work_val; total_salary += sal_val
        machine_totals[machine] = machine_totals.get(machine, 0) + work_val
        if work_val > 0:
            if machine not in machine_days: machine_days[machine] = set()
            machine_days[machine].add(clean_date(date))
        if op_key not in operator_shifts: operator_shifts[op_key] = {}
        operator_shifts[op_key][machine] = operator_shifts[op_key].get(machine, 0) + work_val
        op_totals[op_key] = op_totals.get(op_key, 0) + work_val; op_salaries[op_key] = op_salaries.get(op_key, 0) + sal_val
        if work_val > 0:
            if op_key not in op_days: op_days[op_key] = set()
            op_days[op_key].add(clean_date(date))
        if op_key not in op_machine_totals: op_machine_totals[op_key] = {}
        op_machine_totals[op_key][machine] = op_machine_totals[op_key].get(machine, 0) + work_val
        if op_key not in op_machine_salaries: op_machine_salaries[op_key] = {}
        op_machine_salaries[op_key][machine] = op_machine_salaries[op_key].get(machine, 0) + sal_val
        if work_val > 0:
            if op_key not in op_machine_days: op_machine_days[op_key] = {}
            if machine not in op_machine_days[op_key]: op_machine_days[op_key][machine] = set()
            op_machine_days[op_key][machine].add(clean_date(date))
        if machine not in sets_by_machine: sets_by_machine[machine] = {"list": [], "bonus_count": 0}
        if design and set_num: sets_by_machine[machine]["list"].append((str(design), str(set_num)))
        if bonus and int(bonus) > 0: sets_by_machine[machine]["bonus_count"] += 1

    sets_summary = []
    for machine, data in sets_by_machine.items():
        if not data["list"]: continue
        unique_sets = list(dict.fromkeys(data["list"]))
        rank = await get_set_monthly_rank(machine, end_str)
        sets_summary.append({"machine": machine, "count": data["bonus_count"], "sets_list": unique_sets, "rank": rank})
        
    today = jdatetime.date.today(); days_passed = 0
    if target_start <= today:
        end_check = min(today, target_end); check_date = target_start
        while check_date <= end_check:
            if check_date.weekday() != 6: days_passed += 1
            check_date += jdatetime.timedelta(days=1)
    if days_passed == 0: days_passed = 1 

    final_machine_totals = {m: {"total": w, "avg": w // days_passed} for m, w in machine_totals.items()}
    operator_averages = {k: v // days_passed for k, v in op_totals.items()}
    op_machine_averages = {k: {m: v // days_passed for m, v in mchs.items()} for k, mchs in op_machine_totals.items()}
        
    return {"status": "ok", "week_title": week_title, "start_date": start_str, "end_date": end_str, "total_all": total_all, "total_salary": total_salary, "show_salary": show_salary, "show_opposite_salary": show_opposite_salary, "machine_totals": final_machine_totals, "operator_shifts": operator_shifts, "sets_summary": sets_summary, "operator_averages": operator_averages, "op_machine_averages": op_machine_averages, "op_salaries": op_salaries, "op_machine_salaries": op_machine_salaries, "min_daily": min_daily, "min_weekly": min_weekly, "chador_counts": chador_counts, "chador_salaries": chador_salaries, "clothes_salaries": clothes_salaries, "price": price, "chador_price": chador_price}

@app.get("/api/reports/monthly")
async def get_monthly_report(year: int, month: int, payload: dict = Depends(verify_token)):
    month_names = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور', 'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند']; m_name = month_names[month-1]
    try: first_day = jdatetime.date(year, month, 1)
    except: return {"status": "empty"}
    days_in_month = 31 if month <= 6 else (30 if month <= 11 else (29 if not jdatetime.date(year, 12, 1).isleap() else 30))
    start_str = first_day.strftime("%Y-%m-%d"); end_str = jdatetime.date(year, month, days_in_month).strftime("%Y-%m-%d")
    is_admin = payload.get("role") == "admin"
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT price, chador_price, show_salary, show_opposite_salary, min_daily, min_monthly FROM monthly_prices WHERE year=? AND month=?", (year, month)) as c:
            price_row = await c.fetchone()
        price = price_row[0] if price_row else 0; chador_price = price_row[1] if price_row else 0
        show_salary = True if is_admin else (bool(price_row[2]) if price_row else False); show_opposite_salary = True if is_admin else (bool(price_row[3]) if price_row else False)
        min_daily = price_row[4] if price_row else 300000; min_monthly = price_row[5] if price_row else 7800000
        async with db.execute("SELECT operator_name, shift_type, machine, counter, design_code, set_number, date, jig_bonus, id, parts, is_chador, chador_count, zero_reason FROM shifts WHERE status='approved'") as cursor: all_rows = await cursor.fetchall()
        async with db.execute("SELECT operator_name, shift_type, reward, reward_title, penalty, penalty_title FROM monthly_shift_financials WHERE year=? AND month=?", (year, month)) as c: shift_fin_rows = await c.fetchall()
        async with db.execute("SELECT operator_name, shift_type, machine, reward, reward_title, penalty, penalty_title FROM monthly_machine_financials WHERE year=? AND month=?", (year, month)) as c: machine_fin_rows = await c.fetchall()
            
    rows = [r for r in all_rows if start_str <= clean_date(r[6]) <= end_str]
    if not rows: return {"status": "empty", "month_title": m_name, "min_daily": min_daily, "min_monthly": min_monthly}
    
    total_all = 0; total_salary = 0; machine_totals = {}; machine_days = {}; operator_shifts = {}; sets_by_machine = {}
    op_totals = {}; op_days = {}; op_machine_totals = {}; op_machine_days = {}; op_salaries = {}; op_machine_salaries = {}
    chador_counts = {}; chador_salaries = {}; clothes_salaries = {}; user_shift = payload.get("shift_type")
    shift_financials = {f"{fr[1]} {fr[0]}": {"reward": fr[2] or 0, "reward_title": fr[3], "penalty": fr[4] or 0, "penalty_title": fr[5]} for fr in shift_fin_rows}
    machine_financials = {}
    for mf in machine_fin_rows:
        machine_financials.setdefault(f"{mf[1]} {mf[0]}", {})[mf[2]] = {"reward": mf[3] or 0, "reward_title": mf[4], "penalty": mf[5] or 0, "penalty_title": mf[6]}

    for r in rows:
        op_name, shift, machine, counter, design, set_num, date, bonus, sid, parts, is_chador, chador_count, zero_reason = r
        machine = machine or ""; chador_count = chador_count or 0; is_chador = is_chador or 0
        work_val = 0 if zero_reason else (await calculate_work_logic(machine, date, shift, counter, set_num, parts, bonus, is_chador))[0] or 0
        is_opposite = (shift != user_shift); can_see_salary = True
        if not is_admin:
            if not show_salary: can_see_salary = False
            if is_opposite and not show_opposite_salary: can_see_salary = False
        op_key = f"{shift} {op_name}"; sal_val = 0
        if can_see_salary:
            if is_chador:
                sal_val = int(round(chador_count * (chador_price or 0))); chador_salaries[op_key] = chador_salaries.get(op_key, 0) + sal_val; chador_counts[op_key] = chador_counts.get(op_key, 0) + chador_count
            else:
                sal_val = int(round(work_val * (price or 0))); clothes_salaries[op_key] = clothes_salaries.get(op_key, 0) + sal_val
        total_all += work_val; total_salary += sal_val
        machine_totals[machine] = machine_totals.get(machine, 0) + work_val
        if work_val > 0:
            if machine not in machine_days: machine_days[machine] = set()
            machine_days[machine].add(clean_date(date))
        if op_key not in operator_shifts: operator_shifts[op_key] = {}
        operator_shifts[op_key][machine] = operator_shifts[op_key].get(machine, 0) + work_val
        op_totals[op_key] = op_totals.get(op_key, 0) + work_val; op_salaries[op_key] = op_salaries.get(op_key, 0) + sal_val
        if work_val > 0:
            if op_key not in op_days: op_days[op_key] = set()
            op_days[op_key].add(clean_date(date))
        if op_key not in op_machine_totals: op_machine_totals[op_key] = {}
        op_machine_totals[op_key][machine] = op_machine_totals[op_key].get(machine, 0) + work_val
        if op_key not in op_machine_salaries: op_machine_salaries[op_key] = {}
        op_machine_salaries[op_key][machine] = op_machine_salaries[op_key].get(machine, 0) + sal_val
        if work_val > 0:
            if op_key not in op_machine_days: op_machine_days[op_key] = {}
            if machine not in op_machine_days[op_key]: op_machine_days[op_key][machine] = set()
            op_machine_days[op_key][machine].add(clean_date(date))
        if machine not in sets_by_machine: sets_by_machine[machine] = {"list": [], "bonus_count": 0}
        if design and set_num: sets_by_machine[machine]["list"].append((str(design), str(set_num)))
        if bonus and int(bonus) > 0: sets_by_machine[machine]["bonus_count"] += 1

    for op_key in op_machine_salaries:
        op_shift = op_key.split(' ', 1)[0]; is_opposite = (op_shift != user_shift); can_see_this_op = True
        if not is_admin:
            if not show_salary: can_see_this_op = False
            if is_opposite and not show_opposite_salary: can_see_this_op = False
        if not can_see_this_op: continue
        for mach in op_machine_salaries[op_key]:
            if op_key in machine_financials and mach in machine_financials[op_key]:
                m_fin = machine_financials[op_key][mach]
                op_machine_salaries[op_key][mach] += m_fin["reward"] - m_fin["penalty"]
                op_salaries[op_key] += m_fin["reward"] - m_fin["penalty"]

    for op_key in op_salaries.keys():
        op_shift = op_key.split(' ', 1)[0]; is_opposite = (op_shift != user_shift); can_see_this_op = True
        if not is_admin:
            if not show_salary: can_see_this_op = False
            if is_opposite and not show_opposite_salary: can_see_this_op = False
        if can_see_this_op and op_key in shift_financials:
            s_fin = shift_financials[op_key]; op_salaries[op_key] += s_fin["reward"] - s_fin["penalty"]

    total_salary = sum(op_salaries.values())
    sets_summary = []
    for machine, data in sets_by_machine.items():
        if not data["list"]: continue
        unique_sets = list(dict.fromkeys(data["list"])); rank = await get_set_monthly_rank(machine, end_str)
        sets_summary.append({"machine": machine, "count": data["bonus_count"], "sets_list": unique_sets, "rank": rank})
        
    today = jdatetime.date.today(); days_passed = 0
    if first_day <= today:
        end_check = min(today, jdatetime.date(year, month, days_in_month)); check_date = first_day
        while check_date <= end_check:
            if check_date.weekday() != 6: days_passed += 1
            check_date += jdatetime.timedelta(days=1)
    if days_passed == 0: days_passed = 1 

    final_machine_totals = {m: {"total": w, "avg": w // days_passed} for m, w in machine_totals.items()}
    operator_averages = {k: v // days_passed for k, v in op_totals.items()}
    op_machine_averages = {k: {m: v // days_passed for m, v in mchs.items()} for k, mchs in op_machine_totals.items()}
        
    return {"status": "ok", "month_title": m_name, "start_date": start_str, "end_date": end_str, "total_all": total_all, "total_salary": total_salary, "show_salary": show_salary, "show_opposite_salary": show_opposite_salary, "machine_totals": final_machine_totals, "operator_shifts": operator_shifts, "sets_summary": sets_summary, "operator_averages": operator_averages, "op_machine_averages": op_machine_averages, "op_salaries": op_salaries, "op_machine_salaries": op_machine_salaries, "shift_financials": shift_financials, "machine_financials": machine_financials, "min_daily": min_daily, "min_monthly": min_monthly, "chador_counts": chador_counts, "chador_salaries": chador_salaries, "clothes_salaries": clothes_salaries, "price": price, "chador_price": chador_price}

@app.get("/", response_class=HTMLResponse)
async def read_root():
    return FileResponse("static/index.html")
