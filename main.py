import os
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import db, create_document, get_documents

app = FastAPI(title="School Monitoring Admin API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# Auth helpers
# -----------------------------

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def get_admin_by_email(email: str) -> Optional[dict]:
    if db is None:
        return None
    return db["admin"].find_one({"email": email})


def ensure_default_admin():
    if db is None:
        return
    existing = db["admin"].find_one({"email": "admin@school.local"})
    if not existing:
        db["admin"].insert_one({
            "email": "admin@school.local",
            "name": "Administrator",
            "password_hash": hash_password("admin123"),
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        })


@app.on_event("startup")
async def startup_event():
    ensure_default_admin()
    # Ensure indexes for quick lookups
    if db is not None:
        db["token"].create_index("token", unique=True)
        db["student"].create_index([("first_name", 1), ("last_name", 1)])
        db["teacher"].create_index([("first_name", 1), ("last_name", 1)])
        db["camera"].create_index("classroom_id")
        db["behaviorevent"].create_index([("student_id", 1), ("teacher_id", 1), ("classroom_id", 1)])


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    name: str
    email: str


def get_current_admin(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split(" ", 1)[1]
    doc = db["token"].find_one({"token": token}) if db is not None else None
    if not doc:
        raise HTTPException(status_code=401, detail="Invalid token")
    admin = db["admin"].find_one({"_id": doc["admin_id"]})
    if not admin:
        raise HTTPException(status_code=401, detail="Invalid admin")
    return admin


@app.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    admin = get_admin_by_email(payload.email)
    if not admin or admin.get("password_hash") != hash_password(payload.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = uuid.uuid4().hex
    db["token"].insert_one({
        "token": token,
        "admin_id": admin["_id"],
        "created_at": datetime.now(timezone.utc),
    })
    return LoginResponse(token=token, name=admin.get("name", "Admin"), email=admin.get("email", ""))


@app.post("/auth/logout")
def logout(admin: dict = Depends(get_current_admin), authorization: Optional[str] = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        if db is not None:
            db["token"].delete_one({"token": token})
    return {"success": True}


@app.get("/me")
def me(admin: dict = Depends(get_current_admin)):
    return {"email": admin.get("email"), "name": admin.get("name")}


# -----------------------------
# Dashboard & Notifications
# -----------------------------

@app.get("/stats/dashboard")
def dashboard_stats(_: dict = Depends(get_current_admin)):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    counts = {
        "classrooms": db["classroom"].count_documents({}),
        "students": db["student"].count_documents({}),
        "teachers": db["teacher"].count_documents({}),
        "cameras": db["camera"].count_documents({"is_active": True}),
        "events_today": db["behaviorevent"].count_documents({}),
    }
    # Simple engagement summary
    pipeline = [
        {"$match": {"score": {"$ne": None}}},
        {"$group": {"_id": "$event_type", "avgScore": {"$avg": "$score"}}},
    ]
    summary = list(db["behaviorevent"].aggregate(pipeline)) if db is not None else []
    return {"counts": counts, "engagementSummary": summary}


@app.get("/notifications")
def list_notifications(_: dict = Depends(get_current_admin)):
    items = get_documents("notification", {}, limit=50)
    for it in items:
        it["_id"] = str(it["_id"])  # serialize
    return items


# -----------------------------
# Cameras & Classrooms
# -----------------------------

class CameraIn(BaseModel):
    classroom_id: str
    name: str
    stream_url: str
    is_active: bool = True


@app.get("/cameras")
def get_cameras(_: dict = Depends(get_current_admin)):
    cams = get_documents("camera")
    for c in cams:
        c["_id"] = str(c["_id"]) 
    return cams


@app.post("/cameras")
def add_camera(payload: CameraIn, _: dict = Depends(get_current_admin)):
    _id = create_document("camera", payload.model_dump())
    return {"_id": _id}


@app.delete("/cameras/{camera_id}")
def delete_camera(camera_id: str, _: dict = Depends(get_current_admin)):
    # safer delete using ObjectId
    from bson import ObjectId
    try:
        oid = ObjectId(camera_id)
        res = db["camera"].delete_one({"_id": oid})
        return {"deleted": res.deleted_count}
    except Exception:
        return {"deleted": 0}


class ClassroomIn(BaseModel):
    name: str
    grade: Optional[str] = None
    timetable: Optional[Dict[str, List[str]]] = None


@app.get("/classrooms")
def get_classrooms(_: dict = Depends(get_current_admin)):
    items = get_documents("classroom")
    for i in items:
        i["_id"] = str(i["_id"]) 
    return items


@app.post("/classrooms")
def add_classroom(payload: ClassroomIn, _: dict = Depends(get_current_admin)):
    _id = create_document("classroom", payload.model_dump())
    return {"_id": _id}


@app.patch("/classrooms/{classroom_id}/timetable")
def update_timetable(classroom_id: str, timetable: Dict[str, List[str]], _: dict = Depends(get_current_admin)):
    from bson import ObjectId
    try:
        oid = ObjectId(classroom_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid classroom id")
    db["classroom"].update_one({"_id": oid}, {"$set": {"timetable": timetable, "updated_at": datetime.now(timezone.utc)}})
    return {"success": True}


# -----------------------------
# Students & Teachers & Reports
# -----------------------------

class PersonSearch(BaseModel):
    name: Optional[str] = None
    classroom_id: Optional[str] = None


@app.get("/students")
def search_students(name: Optional[str] = None, classroom_id: Optional[str] = None, _: dict = Depends(get_current_admin)):
    query: Dict[str, Any] = {}
    if name:
        parts = name.strip().split()
        if len(parts) == 1:
            query["$or"] = [{"first_name": {"$regex": parts[0], "$options": "i"}}, {"last_name": {"$regex": parts[0], "$options": "i"}}]
        else:
            query["first_name"] = {"$regex": parts[0], "$options": "i"}
            query["last_name"] = {"$regex": parts[-1], "$options": "i"}
    if classroom_id:
        query["classroom_id"] = classroom_id
    items = list(db["student"].find(query).limit(50)) if db is not None else []
    for s in items:
        s["_id"] = str(s["_id"]) 
    return items


@app.get("/teachers")
def list_teachers(name: Optional[str] = None, _: dict = Depends(get_current_admin)):
    query: Dict[str, Any] = {}
    if name:
        parts = name.strip().split()
        if len(parts) == 1:
            query["$or"] = [{"first_name": {"$regex": parts[0], "$options": "i"}}, {"last_name": {"$regex": parts[0], "$options": "i"}}]
        else:
            query["first_name"] = {"$regex": parts[0], "$options": "i"}
            query["last_name"] = {"$regex": parts[-1], "$options": "i"}
    items = list(db["teacher"].find(query).limit(50)) if db is not None else []
    for t in items:
        t["_id"] = str(t["_id"]) 
    return items


@app.get("/students/{student_id}/report")
def student_report(student_id: str, _: dict = Depends(get_current_admin)):
    from bson import ObjectId
    try:
        oid = ObjectId(student_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid student id")
    student = db["student"].find_one({"_id": oid})
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    events = list(db["behaviorevent"].find({"student_id": student_id}))
    # aggregate simple metrics
    total = len(events)
    avg_score = None
    if total:
        scores = [e.get("score") for e in events if e.get("score") is not None]
        avg_score = sum(scores) / len(scores) if scores else None
    type_breakdown: Dict[str, int] = {}
    for e in events:
        et = e.get("event_type", "unknown")
        type_breakdown[et] = type_breakdown.get(et, 0) + 1
    student["_id"] = str(student["_id"]) 
    return {"student": student, "totalEvents": total, "averageScore": avg_score, "breakdown": type_breakdown, "events": events}


@app.get("/teachers/{teacher_id}/performance")
def teacher_performance(teacher_id: str, _: dict = Depends(get_current_admin)):
    from bson import ObjectId
    try:
        oid = ObjectId(teacher_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid teacher id")
    teacher = db["teacher"].find_one({"_id": oid})
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
    events = list(db["behaviorevent"].find({"teacher_id": teacher_id}))
    total = len(events)
    avg_score = None
    if total:
        scores = [e.get("score") for e in events if e.get("score") is not None]
        avg_score = sum(scores) / len(scores) if scores else None
    type_breakdown: Dict[str, int] = {}
    for e in events:
        et = e.get("event_type", "unknown")
        type_breakdown[et] = type_breakdown.get(et, 0) + 1
    teacher["_id"] = str(teacher["_id"]) 
    return {"teacher": teacher, "totalEvents": total, "averageScore": avg_score, "breakdown": type_breakdown, "events": events}


# -----------------------------
# Demo seeding endpoint (optional)
# -----------------------------

@app.post("/seed")
def seed(_: dict = Depends(get_current_admin)):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    # Create classrooms
    cls_ids = []
    for name in ["10A", "10B", "11A"]:
        res = db["classroom"].insert_one({
            "name": name,
            "grade": name[:2],
            "timetable": {"Mon": ["Math", "English", "Physics"], "Tue": ["Chem", "History", "Sports"]},
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        })
        cls_ids.append(str(res.inserted_id))
    # Students
    for i in range(1, 16):
        db["student"].insert_one({
            "first_name": f"Student{i}",
            "last_name": "Demo",
            "classroom_id": cls_ids[i % len(cls_ids)],
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        })
    # Teachers
    for sub in ["Math", "English", "Physics", "Chem", "History", "Sports"]:
        db["teacher"].insert_one({
            "first_name": sub,
            "last_name": "Teacher",
            "subject": sub,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        })
    # Cameras
    placeholders = [
        "https://images.unsplash.com/photo-1557324232-b8917d3c3dcb?q=80&w=800&auto=format&fit=crop",
        "https://images.unsplash.com/photo-1523246191808-8b153aa73d33?q=80&w=800&auto=format&fit=crop",
        "https://images.unsplash.com/photo-1541339907198-e08756dedf3f?q=80&w=800&auto=format&fit=crop",
    ]
    for idx, cls in enumerate(cls_ids):
        db["camera"].insert_one({
            "classroom_id": cls,
            "name": f"Class {idx+1} Cam",
            "stream_url": placeholders[idx % len(placeholders)],
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        })
    # Notifications
    for n in [
        {"title": "High Engagement", "message": "Class 10A shows 85% engagement", "level": "info"},
        {"title": "Distraction Spike", "message": "Class 11A showed increased chatter", "level": "warning"},
    ]:
        db["notification"].insert_one({**n, "created_at": datetime.now(timezone.utc)})
    return {"success": True}


@app.get("/")
def root():
    return {"status": "ok", "app": "School Monitoring Admin API"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = os.getenv("DATABASE_NAME") or "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️ Connected but Error: {str(e)[:50]}"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"
    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
