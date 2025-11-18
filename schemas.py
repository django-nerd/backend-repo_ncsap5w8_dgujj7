"""
Database Schemas

Define your MongoDB collection schemas here using Pydantic models.
These schemas are used for data validation in your application.

Each Pydantic model represents a collection in your database.
Model name is converted to lowercase for the collection name:
- User -> "user" collection
- Product -> "product" collection
- BlogPost -> "blogs" collection
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict

# Core domain schemas for the School Monitoring App

class Admin(BaseModel):
    email: str = Field(..., description="Admin email (login)")
    name: str = Field(..., description="Full name")
    password_hash: str = Field(..., description="Hashed password")
    is_active: bool = Field(True)

class Camera(BaseModel):
    classroom_id: str = Field(..., description="ID of classroom this camera belongs to")
    name: str = Field(..., description="Camera label, e.g., Front Cam")
    stream_url: str = Field(..., description="RTSP/HTTP stream or placeholder image URL")
    is_active: bool = Field(True)

class Classroom(BaseModel):
    name: str = Field(..., description="Classroom name, e.g., 10A")
    grade: Optional[str] = Field(None, description="Grade level")
    timetable: Optional[Dict[str, List[str]]] = Field(
        default_factory=dict,
        description="Day -> list of periods/courses"
    )

class Student(BaseModel):
    first_name: str
    last_name: str
    classroom_id: str
    roll_number: Optional[str] = None

class Teacher(BaseModel):
    first_name: str
    last_name: str
    subject: Optional[str] = None

class BehaviorEvent(BaseModel):
    student_id: Optional[str] = None
    teacher_id: Optional[str] = None
    classroom_id: Optional[str] = None
    event_type: str = Field(..., description="e.g., engagement, distraction, participation, tardiness")
    score: Optional[float] = Field(None, ge=0, le=1, description="Normalized score 0-1")
    notes: Optional[str] = None

class Notification(BaseModel):
    title: str
    message: str
    level: str = Field("info", description="info|warning|critical")

# Example schemas kept for reference (not used by the app directly)
class User(BaseModel):
    name: str = Field(..., description="Full name")
    email: str = Field(..., description="Email address")
    address: str = Field(..., description="Address")
    age: Optional[int] = Field(None, ge=0, le=120, description="Age in years")
    is_active: bool = Field(True, description="Whether user is active")

class Product(BaseModel):
    title: str = Field(..., description="Product title")
    description: Optional[str] = Field(None, description="Product description")
    price: float = Field(..., ge=0, description="Price in dollars")
    category: str = Field(..., description="Product category")
    in_stock: bool = Field(True, description="Whether product is in stock")
