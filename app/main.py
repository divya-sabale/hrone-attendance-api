"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
import json
from bson import json_util
from contextlib import asynccontextmanager
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"),tz_aware=True)
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.employees.create_index("emp_code", unique=True)
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
    db.attendance_logs.create_index([("date", -1), ("emp_code", 1)])
    db.employees.create_index([("department", 1), ("joined_on", 1)])
    db.attendance_logs.create_index([("date", 1), ("emp_code", 1)])
    db.employees.create_index("joined_on")
    yield


app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
    lifespan=lifespan,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(punch_in: datetime, shift_start: str, date_str: Optional[str] = None) -> int:
    punch_in = punch_in.replace(microsecond=0)
    if punch_in.tzinfo is None:
        punch_in = punch_in.replace(tzinfo=timezone.utc)
    punch_in_ist = punch_in.astimezone(IST)
    if date_str is None:
        date_str = punch_in_ist.date().isoformat()
    h, m = map(int, shift_start.split(":"))
    start = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
    seconds = int((punch_in_ist - start).total_seconds())
    if seconds > 600:
        return seconds // 60
    return 0

def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = int((punch_out - punch_in).total_seconds())
    hours = Decimal(seconds) / Decimal(3600)
    return float(hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def compute_overtime(punch_out: datetime, shift_end: str, date_str: str, shift_start: Optional[str] = None) -> int:
    punch_out = punch_out.replace(microsecond=0)
    if punch_out.tzinfo is None:
        punch_out = punch_out.replace(tzinfo=timezone.utc)
    h, m = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
    if shift_start is not None:
        sh, sm = map(int, shift_start.split(":"))
        if (h, m) <= (sh, sm):
            end += timedelta(days=1)
    minutes = int((punch_out - end).total_seconds()) // 60
    return minutes if minutes >= 30 else 0

def ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc)


def dt_to_ms(dt: Optional[datetime]) -> Optional[int]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp()) * 1000


def attendance_date(punch_in: datetime, shift_start: str, shift_end: str) -> str:
    ist = punch_in.astimezone(IST)
    sh, sm = map(int, shift_start.split(":"))
    eh, em = map(int, shift_end.split(":"))
    d = ist.date()
    if (eh, em) <= (sh, sm) and (ist.hour, ist.minute) < (eh, em):
        d = d - timedelta(days=1)
    return d.isoformat()

def history_to_api(history):
    out = []
    for h in history or []:
        changes = {}
        for k, v in h.get("changes", {}).items():
            if k in ("punch_in", "punch_out"):
                changes[k] = {"from": dt_to_ms(v.get("from")), "to": dt_to_ms(v.get("to"))}
            else:
                changes[k] = v
        out.append({
            "at": dt_to_ms(h.get("at")),
            "by": h.get("by"),
            "reason": h.get("reason"),
            "changes": changes,
        })
    return out

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def check_joined_on(cls, v):
        datetime.strptime(v, "%Y-%m-%d")
        return v

    @model_validator(mode="after")
    def check_shift(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self

class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = Field(default=None, ge=100000000000, le=4102444800000)
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = Field(default=None, ge=100000000000, le=4102444800000)

class RegularizeIn(BaseModel):
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None
    punch_in: Optional[StrictInt] = Field(default=None, ge=100000000000, le=4102444800000)
    punch_out: Optional[StrictInt] = Field(default=None, ge=100000000000, le=4102444800000)
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        client.admin.command("ping")
    except Exception:
        raise HTTPException(503, "database unavailable")
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    doc.pop("_id", None)
    doc["created_at"] = dt_to_ms(doc["created_at"])
    return doc


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")
    if body.punched_at is not None:
        ts = ms_to_dt(body.punched_at)
    else:
        ts = datetime.now(timezone.utc).replace(microsecond=0)
    rec = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": ts}},
        sort=[("punch_in", -1)],
    )
    if rec is None:
        raise HTTPException(404, "no punch-in found")
    if rec.get("punch_out") is not None:
        raise HTTPException(409, "already punched out")
    pin = rec["punch_in"]
    if pin.tzinfo is None:
        pin = pin.replace(tzinfo=timezone.utc)
    if ts <= pin or (ts - pin) > timedelta(hours=24):
        raise HTTPException(422, "punch-out must be after punch-in and within 24 hours")
    hours = compute_work_hours(pin, ts)
    overtime = compute_overtime(ts, emp["shift_end"], rec["date"], emp["shift_start"])
    half = hours < 4.5
    res = db.attendance_logs.update_one(
        {"_id": rec["_id"], "punch_out": None},
        {"$set": {
            "punch_out": ts,
            "work_hours": hours,
            "overtime_minutes": overtime,
            "half_day": half,
        }},
    )
    if res.modified_count == 0:
        raise HTTPException(409, "already punched out")
    return {
        "emp_code": rec["emp_code"],
        "date": rec["date"],
        "status": rec["status"],
        "punch_in": dt_to_ms(pin),
        "punch_out": dt_to_ms(ts),
        "work_hours": hours,
        "late_minutes": rec.get("late_minutes", 0),
        "overtime_minutes": overtime,
        "half_day": half,
        "history": rec.get("history", []),
    }


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
   
):
    q = {}
    if department:
        q["department"] = department
    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0}).sort("emp_code", 1).skip(skip).limit(page_size))
    for e in items:
        e["created_at"] = dt_to_ms(e.get("created_at"))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")
    if body.punched_at is not None:
        ts = ms_to_dt(body.punched_at)
    else:
        ts = datetime.now(timezone.utc).replace(microsecond=0)
    d = attendance_date(ts, emp["shift_start"], emp["shift_end"])
    late = compute_late_minutes(ts, emp["shift_start"], d)
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
    return {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": dt_to_ms(ts),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }


@app.patch("/attendance/{emp_code}/{date}")
def regularize(emp_code: str, date: str, body: RegularizeIn):
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(422, "date must be YYYY-MM-DD")
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")
    rec = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if rec is None:
        raise HTTPException(404, "attendance record not found")

    def aware(x):
        if x is not None and x.tzinfo is None:
            return x.replace(tzinfo=timezone.utc)
        return x

    old = {
        "status": rec["status"],
        "punch_in": aware(rec.get("punch_in")),
        "punch_out": aware(rec.get("punch_out")),
        "work_hours": rec.get("work_hours"),
        "late_minutes": rec.get("late_minutes", 0),
        "overtime_minutes": rec.get("overtime_minutes", 0),
        "half_day": rec.get("half_day", False),
    }
    old_history = rec.get("history", [])

    new_status = body.status or old["status"]
    if new_status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "ABSENT or LEAVE cannot have punch times")
        new = {
            "status": new_status,
            "punch_in": None,
            "punch_out": None,
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
        }
    else:
        pin = ms_to_dt(body.punch_in) if body.punch_in is not None else old["punch_in"]
        pout = ms_to_dt(body.punch_out) if body.punch_out is not None else old["punch_out"]
        if pin is None:
            raise HTTPException(422, "a presence status needs a punch_in")
        if body.punch_in is not None:
            if attendance_date(pin, emp["shift_start"], emp["shift_end"]) != date:
                raise HTTPException(422, "punch_in must stay on the record's attendance date")
        if pout is not None and (pout <= pin or (pout - pin) > timedelta(hours=24)):
            raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")
        late = compute_late_minutes(pin, emp["shift_start"], date)
        if pout is not None:
            hours = compute_work_hours(pin, pout)
            overtime = compute_overtime(pout, emp["shift_end"], date, emp["shift_start"])
            half = hours < 4.5
        else:
            hours, overtime, half = None, 0, False
        new = {
            "status": new_status,
            "punch_in": pin,
            "punch_out": pout,
            "work_hours": hours,
            "late_minutes": late,
            "overtime_minutes": overtime,
            "half_day": half,
        }

    changes = {k: {"from": old[k], "to": new[k]} for k in new if old[k] != new[k]}
    if not changes:
        raise HTTPException(422, "request changes nothing")

    entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }
    res = db.attendance_logs.update_one(
        {
            "_id": rec["_id"],
            "status": rec["status"],
            "punch_in": rec.get("punch_in"),
            "punch_out": rec.get("punch_out"),
            "$expr": {"$eq": [{"$size": {"$ifNull": ["$history", []]}}, len(old_history)]},
        },
        {"$set": new, "$push": {"history": entry}},
    )
    if res.modified_count == 0:
        raise HTTPException(409, "record was changed by someone else, try again")
    return {
        "emp_code": emp_code,
        "date": date,
        "status": new["status"],
        "punch_in": dt_to_ms(new["punch_in"]),
        "punch_out": dt_to_ms(new["punch_out"]),
        "work_hours": new["work_hours"],
        "late_minutes": new["late_minutes"],
        "overtime_minutes": new["overtime_minutes"],
        "half_day": new["half_day"],
        "history": history_to_api(old_history + [entry]),
    }

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must not be after date_to")
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    total = db.attendance_logs.count_documents(q)
    skip = (page - 1) * page_size
    page_docs = list(
        db.attendance_logs.find(q, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip(skip)
        .limit(page_size)
    )
    for d in page_docs:
        d["punch_in"] = dt_to_ms(d.get("punch_in"))
        d["punch_out"] = dt_to_ms(d.get("punch_out"))
        d.setdefault("half_day", False)
        d.setdefault("history", [])
    return {"items": page_docs, "total": total, "page": page, "page_size": page_size} 

def r2(x) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def working_days_between(start: datetime, end: datetime) -> int:
    n = 0
    d = start
    while d <= end:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n   

PRESENT_STATUSES = ["PRESENT", "WFH", "ON_DUTY"]


def employee_monthly_pipeline(emp_code: str, date_from: str, date_to: str) -> list:
    return [
        {"$match": {"emp_code": emp_code, "date": {"$gte": date_from, "$lte": date_to}}},
        {"$addFields": {
            "dow": {"$dayOfWeek": {"$dateFromString": {"dateString": "$date", "format": "%Y-%m-%d"}}}
        }},
        {"$group": {
            "_id": None,
            "present_days": {"$sum": {"$cond": [
                {"$and": [
                    {"$in": ["$status", PRESENT_STATUSES]},
                    {"$in": ["$dow", [2, 3, 4, 5, 6]]},
                ]},
                {"$cond": [{"$eq": [{"$ifNull": ["$half_day", False]}, True]}, 0.5, 1]},
                0,
            ]}},
            "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
        }},
    ]


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")
    y, mth = int(month[:4]), int(month[5:7])
    first = datetime(y, mth, 1)
    last = datetime(y + (mth == 12), mth % 12 + 1, 1) - timedelta(days=1)
    joined = datetime.strptime(emp["joined_on"], "%Y-%m-%d")
    start = max(first, joined)
    working_days = working_days_between(start, last) if start <= last else 0
    pipeline = employee_monthly_pipeline(
        emp_code, first.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d")
    )
    rows = list(db.attendance_logs.aggregate(pipeline))
    s = rows[0] if rows else {}
    present = float(s.get("present_days", 0))
    pct = r2(present / working_days * 100) if working_days > 0 else None
    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present,
        "leave_days": int(s.get("leave_days", 0)),
        "late_count": int(s.get("late_count", 0)),
        "total_late_minutes": int(s.get("total_late_minutes", 0)),
        "total_overtime_minutes": int(s.get("total_overtime_minutes", 0)),
        "attendance_pct": pct,
    }

def month_bounds(month: str):
    y, mth = int(month[:4]), int(month[5:7])
    first = datetime(y, mth, 1)
    last = datetime(y + (mth == 12), mth % 12 + 1, 1) - timedelta(days=1)
    return first.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d")


def department_summary_pipeline(first: str, last: str, department: Optional[str]) -> list:
    match = {"joined_on": {"$lte": last}}
    if department:
        match["department"] = department
    is_present = {"$in": ["$$l.status", PRESENT_STATUSES]}

    def per_log(expr):
        return {"$sum": {"$map": {"input": "$logs", "as": "l", "in": expr}}}

    return [
        {"$match": match},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"ec": "$emp_code"},
            "pipeline": [
                {"$match": {
                    "$expr": {"$eq": ["$emp_code", "$$ec"]},
                    "date": {"$gte": first, "$lte": last},
                }},
                {"$addFields": {
                    "dow": {"$dayOfWeek": {"$dateFromString": {"dateString": "$date", "format": "%Y-%m-%d"}}}
                }},
            ],
            "as": "logs",
        }},
        {"$addFields": {
            "present_days": per_log({"$cond": [
                {"$and": [is_present, {"$in": ["$$l.dow", [2, 3, 4, 5, 6]]}]},
                {"$cond": [{"$eq": [{"$ifNull": ["$$l.half_day", False]}, True]}, 0.5, 1]},
                0,
            ]}),
            "wh_sum": per_log({"$cond": [
                {"$and": [is_present, {"$isNumber": "$$l.work_hours"}]}, "$$l.work_hours", 0,
            ]}),
            "wh_cnt": per_log({"$cond": [
                {"$and": [is_present, {"$isNumber": "$$l.work_hours"}]}, 1, 0,
            ]}),
            "late_count": per_log({"$cond": [{"$gt": [{"$ifNull": ["$$l.late_minutes", 0]}, 0]}, 1, 0]}),
            "late_min": per_log({"$ifNull": ["$$l.late_minutes", 0]}),
            "leave_count": per_log({"$cond": [{"$eq": ["$$l.status", "LEAVE"]}, 1, 0]}),
            "on_duty_count": per_log({"$cond": [{"$eq": ["$$l.status", "ON_DUTY"]}, 1, 0]}),
        }},
        {"$group": {
            "_id": "$department",
            "headcount": {"$sum": 1},
            "present_days": {"$sum": "$present_days"},
            "wh_sum": {"$sum": "$wh_sum"},
            "wh_cnt": {"$sum": "$wh_cnt"},
            "late_count": {"$sum": "$late_count"},
            "late_min": {"$sum": "$late_min"},
            "leave_count": {"$sum": "$leave_count"},
            "on_duty_count": {"$sum": "$on_duty_count"},
        }},
        {"$sort": {"_id": 1}},
    ]


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
):
    first, last = month_bounds(month)
    rows = list(db.employees.aggregate(department_summary_pipeline(first, last, department)))
    items = []
    for r in rows:
        avg = r2(r["wh_sum"] / r["wh_cnt"]) if r["wh_cnt"] > 0 else None
        items.append({
            "department": r["_id"],
            "headcount": r["headcount"],
            "present_days": r2(r["present_days"]),
            "avg_work_hours": avg,
            "late_count": int(r["late_count"]),
            "total_late_minutes": int(r["late_min"]),
            "leave_count": int(r["leave_count"]),
            "on_duty_count": int(r["on_duty_count"]),
        })
    return {"month": month, "items": items}



def late_leaderboard_pipeline(first: str, last: str, limit: int, department: Optional[str]) -> list:
    pipeline = [
        {"$match": {"date": {"$gte": first, "$lte": last}}},
        {"$group": {
            "_id": "$emp_code",
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
        }},
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {"$lookup": {
            "from": "employees",
            "localField": "_id",
            "foreignField": "emp_code",
            "as": "emp",
        }},
        {"$unwind": "$emp"},
    ]
    if department:
        pipeline.append({"$match": {"emp.department": department}})
    pipeline += [
        {"$setWindowFields": {
            "sortBy": {"total_late_minutes": -1},
            "output": {"rank": {"$rank": {}}},
        }},
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {"$project": {
            "_id": 0,
            "rank": 1,
            "emp_code": "$_id",
            "name": "$emp.name",
            "department": "$emp.department",
            "total_late_minutes": 1,
            "late_count": 1,
        }},
    ]
    return pipeline


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None,
):
    first, last = month_bounds(month)
    rows = list(db.attendance_logs.aggregate(late_leaderboard_pipeline(first, last, limit, department)))
    return {"month": month, "items": rows}

def half_up_4(field: str) -> dict:
    return {"$cond": [
        {"$isNumber": field},
        {"$divide": [{"$floor": {"$add": [{"$multiply": [field, 10000]}, 0.500000001]}}, 10000]},
        None,
    ]}


def department_trend_pipeline(department: str, date_from: str, date_to: str) -> list:
    start = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    n_days = (datetime.strptime(date_to, "%Y-%m-%d") - datetime.strptime(date_from, "%Y-%m-%d")).days + 1
    return [
        {"$match": {"department": department}},
        {"$group": {"_id": None, "codes": {"$push": "$emp_code"}, "joins": {"$push": "$joined_on"}}},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"codes": "$codes"},
            "pipeline": [
                {"$match": {
                    "date": {"$gte": date_from, "$lte": date_to},
                    "$expr": {"$in": ["$emp_code", "$$codes"]},
                }},
                {"$group": {
                    "_id": "$date",
                    "present": {"$sum": {"$cond": [
                        {"$in": ["$status", PRESENT_STATUSES]},
                        {"$cond": [{"$eq": [{"$ifNull": ["$half_day", False]}, True]}, 0.5, 1]},
                        0,
                    ]}},
                    "late": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
                }},
            ],
            "as": "days",
        }},
        {"$project": {"_id": 0, "joins": 1, "days": 1, "offsets": {"$range": [0, n_days]}}},
        {"$unwind": "$offsets"},
        {"$addFields": {"dt": {"$dateAdd": {"startDate": start, "unit": "day", "amount": "$offsets"}}}},
        {"$addFields": {
            "d": {"$dateToString": {"date": "$dt", "format": "%Y-%m-%d"}},
            "dow": {"$dayOfWeek": "$dt"},
        }},
        {"$addFields": {"stat": {"$first": {"$filter": {
            "input": "$days", "as": "x", "cond": {"$eq": ["$$x._id", "$d"]},
        }}}}},
        {"$project": {
            "_id": 0,
            "date": "$d",
            "is_working_day": {"$in": ["$dow", [2, 3, 4, 5, 6]]},
            "headcount": {"$size": {"$filter": {
                "input": "$joins", "as": "j", "cond": {"$lte": ["$$j", "$d"]},
            }}},
            "present_count": {"$ifNull": ["$stat.present", 0]},
            "late_count": {"$ifNull": ["$stat.late", 0]},
        }},
        {"$addFields": {"raw_rate": {"$cond": [
            {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
            {"$divide": ["$present_count", "$headcount"]},
            None,
        ]}}},
        {"$addFields": {"attendance_rate": half_up_4("$raw_rate")}},
        {"$setWindowFields": {
            "sortBy": {"date": 1},
            "output": {"moving_raw": {"$avg": "$attendance_rate", "window": {"documents": [-6, 0]}}},
        }},
        {"$addFields": {"moving_avg_7d": half_up_4("$moving_raw")}},
        {"$project": {"raw_rate": 0, "moving_raw": 0}},
        {"$sort": {"date": 1}},
    ]


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    date_from: str = Query(..., alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str = Query(..., alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    try:
        d1 = datetime.strptime(date_from, "%Y-%m-%d")
        d2 = datetime.strptime(date_to, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(422, "from and to must be real dates")
    if d2 < d1:
        raise HTTPException(422, "to must not be before from")
    if (d2 - d1).days + 1 > 92:
        raise HTTPException(422, "range must not exceed 92 days")
    if db.employees.find_one({"department": department}, {"_id": 1}) is None:
        raise HTTPException(404, "department not found")
    rows = list(db.employees.aggregate(department_trend_pipeline(department, date_from, date_to)))
    return {"department": department, "items": rows}



def run_explain(cmd: dict) -> dict:
    res = db.command({"explain": cmd, "verbosity": "executionStats"})
    return json.loads(json_util.dumps(res))


@app.get("/admin/explain/{endpoint}")
def admin_explain(
    endpoint: Literal["attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"],
    emp_code: Optional[str] = None,
    month: Optional[str] = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    date_from_q: Optional[str] = Query(None, alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to_q: Optional[str] = Query(None, alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$"),

):
    if endpoint == "attendance_list":
        q = {}
        if emp_code:
            q["emp_code"] = emp_code
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to
        if status:
            q["status"] = status
        cmd = {
            "find": "attendance_logs",
            "filter": q,
            "sort": {"date": -1, "emp_code": 1},
            "skip": (page - 1) * page_size,
            "limit": page_size,
        }
        coll = "attendance_logs"
    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(422, "emp_code and month are required")
        first, last = month_bounds(month)
        cmd = {
            "aggregate": "attendance_logs",
            "pipeline": employee_monthly_pipeline(emp_code, first, last),
            "cursor": {},
        }
        coll = "attendance_logs"
    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(422, "month is required")
        first, last = month_bounds(month)
        cmd = {
            "aggregate": "employees",
            "pipeline": department_summary_pipeline(first, last, department),
            "cursor": {},
        }
    elif endpoint == "department_trend":
        if not department or not date_from_q or not date_to_q:
            raise HTTPException(422, "department, from and to are required")
        cmd = {
            "aggregate": "employees",
            "pipeline": department_trend_pipeline(department, date_from_q, date_to_q),
            "cursor": {},
        }
        coll = "employees"
    else:
        if not month:
            raise HTTPException(422, "month is required")
        first, last = month_bounds(month)
        cmd = {
            "aggregate": "attendance_logs",
            "pipeline": late_leaderboard_pipeline(first, last, limit, department),
            "cursor": {},
        }
        coll = "attendance_logs"
    return {"endpoint": endpoint, "collection": coll, "explain": run_explain(cmd)}



# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #






