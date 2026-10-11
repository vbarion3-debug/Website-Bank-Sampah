from pydantic import BaseModel, Field, EmailStr
from datetime import datetime
from typing import Optional, List
from enum import Enum


class RoleEnum(str, Enum):
    admin = "admin"
    super_admin = "super_admin"


# ========== REQUEST SCHEMAS ==========

class AdminRegister(BaseModel):
    nama_admin: str = Field(..., min_length=3, max_length=100)
    username: str = Field(..., min_length=5, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)
    no_hp: str = Field(..., pattern=r"^(\+62|0)[0-9]{9,12}$")
    role: Optional[RoleEnum] = RoleEnum.admin


class AdminLogin(BaseModel):
    """Step 1: Login admin - username + password via JSON body"""
    username: str
    password: str


class AdminVerifyOTP(BaseModel):
    """Step 2: Verify OTP yang dikirim ke email admin"""
    username: str
    kode_otp: str = Field(..., min_length=6, max_length=6)


class SuperAdminLoginRequest(BaseModel):
    """Step 1: Super admin login dengan email + password"""
    email: EmailStr
    password: str


class SuperAdminVerifyOTP(BaseModel):
    """Step 2: Verify OTP yang dikirim ke email super admin"""
    email: EmailStr
    kode_otp: str = Field(..., min_length=6, max_length=6)


class AdminUpdate(BaseModel):
    """
    Super admin mengedit akun admin (partial update - cuma field yang dikirim yang diubah).
    Field yang dikirim null diabaikan. Username & role sengaja TIDAK bisa diubah.
    """
    nama_admin: Optional[str] = Field(None, min_length=3, max_length=100)
    email: Optional[EmailStr] = None
    no_hp: Optional[str] = Field(None, pattern=r"^(\+62|0)[0-9]{9,12}$")
    password: Optional[str] = Field(None, min_length=8, description="Reset password admin (opsional)")


class AdminStatusUpdate(BaseModel):
    is_active: bool


# ========== RESPONSE SCHEMAS ==========

class AdminResponse(BaseModel):
    id_admin: int
    nama_admin: str
    username: str
    email: Optional[str] = None
    no_hp: Optional[str] = None
    role: RoleEnum
    is_active: bool = True
    tanggal_dibuat: datetime

    class Config:
        from_attributes = True


class AdminListResponse(BaseModel):
    total: int
    data: List[AdminResponse]
