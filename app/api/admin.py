from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.models import (
    Admin, RoleAdmin, ChatAssignment,
    RiwayatSaldoAdjustment, TransaksiJualMitra, Pengumuman, PenarikanSaldo,
)
from app.schemas.admin import (
    AdminRegister, AdminResponse, AdminUpdate, AdminStatusUpdate, AdminListResponse,
)
from app.utils.security import (
    hash_password, require_super_admin, get_current_admin, delete_sessions_by_subject,
)

router = APIRouter(prefix="/admin", tags=["Admin Fitur"])

ROLE_ADMIN = ("admin", "super_admin")  # role yang dipakai di session admin


@router.get("/dashboard")
def get_admin_dashboard(authorization: str = Header(None)):
    get_current_admin(authorization)
    return {"message": "Ini halaman dashboard Admin"}


@router.get("/laporan")
def get_laporan(authorization: str = Header(None)):
    get_current_admin(authorization)
    return {"message": "Ini daftar laporan transaksi untuk Admin"}


# ========== SUPER ADMIN ONLY: BUAT AKUN ADMIN ==========
@router.post("/create-admin", response_model=AdminResponse)
def create_admin(
    data: AdminRegister,
    db: Session = Depends(get_db),
    _super_admin: dict = Depends(require_super_admin)
):
    existing = db.query(Admin).filter(
        (Admin.username == data.username) | (Admin.email == data.email)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username atau email sudah dipakai")

    new_admin = Admin(
        nama_admin=data.nama_admin,
        username=data.username,
        email=data.email,
        password=hash_password(data.password),
        no_hp=data.no_hp,
        role=RoleAdmin.admin
    )
    db.add(new_admin)
    db.commit()
    db.refresh(new_admin)
    return new_admin


# ========== SUPER ADMIN ONLY: LIST SEMUA AKUN ADMIN ==========
@router.get("/list-admin", response_model=AdminListResponse)
def list_admin(
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, description="Cari berdasarkan nama, username, atau email"),
    is_active: Optional[bool] = Query(None, description="Filter akun aktif/nonaktif"),
    _super_admin: dict = Depends(require_super_admin)
):
    query = db.query(Admin)
    if search:
        like_pattern = f"%{search}%"
        query = query.filter(
            or_(
                Admin.nama_admin.ilike(like_pattern),
                Admin.username.ilike(like_pattern),
                Admin.email.ilike(like_pattern),
            )
        )
    if is_active is not None:
        query = query.filter(Admin.is_active == is_active)
    hasil = query.order_by(Admin.tanggal_dibuat.desc()).all()
    return AdminListResponse(total=len(hasil), data=hasil)


# ========== SUPER ADMIN ONLY: EDIT AKUN ADMIN ==========
@router.patch("/edit-admin/{id_admin}", response_model=AdminResponse)
def edit_admin(
    id_admin: int,
    data: AdminUpdate,
    db: Session = Depends(get_db),
    _super_admin: dict = Depends(require_super_admin)
):
    """
    Edit nama, email, no HP, atau reset password akun admin (partial update).
    - Akun super admin tidak bisa diubah lewat endpoint ini.
    - Username tidak bisa diubah (dipakai sebagai identitas session & riwayat chat).
    - Kalau password diganti, semua session aktif admin tersebut dihapus (harus login ulang).
    """
    admin = db.query(Admin).filter(Admin.id_admin == id_admin).first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin tidak ditemukan")
    if admin.role == RoleAdmin.super_admin:
        raise HTTPException(status_code=403, detail="Akun super admin tidak bisa diubah lewat endpoint ini")

    update_data = data.model_dump(exclude_unset=True, exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="Tidak ada field yang diubah")

    if "email" in update_data and update_data["email"] != admin.email:
        bentrok = db.query(Admin).filter(
            Admin.email == update_data["email"],
            Admin.id_admin != id_admin
        ).first()
        if bentrok:
            raise HTTPException(status_code=400, detail="Email sudah dipakai admin lain")

    password_diubah = "password" in update_data
    if password_diubah:
        admin.password = hash_password(update_data.pop("password"))

    for field, value in update_data.items():
        setattr(admin, field, value)

    db.commit()
    db.refresh(admin)

    if password_diubah:
        delete_sessions_by_subject(admin.username, ROLE_ADMIN)

    return admin


# ========== SUPER ADMIN ONLY: AKTIFKAN / NONAKTIFKAN AKUN ADMIN ==========
@router.patch("/status-admin/{id_admin}", response_model=AdminResponse)
def ubah_status_admin(
    id_admin: int,
    data: AdminStatusUpdate,
    db: Session = Depends(get_db),
    _super_admin: dict = Depends(require_super_admin)
):
    """
    Nonaktifkan akun admin (tidak bisa login lagi, data & riwayatnya tetap utuh), atau aktifkan kembali.
    Saat dinonaktifkan: semua session aktifnya langsung dihapus, dan chat yang sedang
    ditanganinya dilepas supaya admin lain bisa mengambil alih.
    """
    admin = db.query(Admin).filter(Admin.id_admin == id_admin).first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin tidak ditemukan")
    if admin.role == RoleAdmin.super_admin:
        raise HTTPException(status_code=403, detail="Akun super admin tidak bisa dinonaktifkan")

    admin.is_active = data.is_active
    if not data.is_active:
        db.query(ChatAssignment).filter(ChatAssignment.admin_username == admin.username).delete()

    db.commit()
    db.refresh(admin)

    if not data.is_active:
        delete_sessions_by_subject(admin.username, ROLE_ADMIN)

    return admin


# ========== SUPER ADMIN ONLY: HAPUS AKUN ADMIN ==========
@router.delete("/delete-admin/{id_admin}")
def delete_admin(
    id_admin: int,
    db: Session = Depends(get_db),
    _super_admin: dict = Depends(require_super_admin)
):
    """
    Hapus permanen. Hanya untuk admin yang belum punya riwayat apa pun.
    Admin yang sudah pernah membuat pengumuman / koreksi saldo / transaksi jual / memproses penarikan
    tidak bisa dihapus (menjaga audit trail) - nonaktifkan saja.
    """
    admin = db.query(Admin).filter(Admin.id_admin == id_admin).first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin tidak ditemukan")
    if admin.role == RoleAdmin.super_admin:
        raise HTTPException(status_code=403, detail="Tidak bisa menghapus akun super admin lewat endpoint ini")

    punya_riwayat = any(
        db.query(model).filter(model.id_admin == id_admin).first() is not None
        for model in (RiwayatSaldoAdjustment, TransaksiJualMitra, Pengumuman, PenarikanSaldo)
    )
    if punya_riwayat:
        raise HTTPException(
            status_code=409,
            detail="Admin ini sudah punya riwayat (pengumuman/koreksi saldo/transaksi/penarikan) "
                   "sehingga tidak bisa dihapus. Nonaktifkan saja lewat PATCH /admin/status-admin/{id_admin}."
        )

    username = admin.username
    db.query(ChatAssignment).filter(ChatAssignment.admin_username == username).delete()
    db.delete(admin)
    db.commit()
    delete_sessions_by_subject(username, ROLE_ADMIN)
    return {"message": f"Admin '{username}' berhasil dihapus"}
