import os

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.core.database import get_db
from app.models.models import Nasabah, PenarikanSaldo, StatusPenarikan
from app.schemas.nasabah import NasabahRegister, RegistrationSuccess, NasabahResponse, NasabahProfileUpdate
from app.utils.security import get_current_nasabah, delete_session, extract_session_id
from app.utils.notifikasi import notif_rekening_diubah
# from app.utils.otp_service import OTPService

router = APIRouter(prefix="/nasabah", tags=["Nasabah"])

# ========== REGISTRASI NASABAH ==========

@router.post("/register", response_model=RegistrationSuccess)
def register_nasabah(data: NasabahRegister, db: Session = Depends(get_db)):
    """
    Endpoint untuk registrasi nasabah baru.
    Nasabah hanya perlu input data diri, TANPA password.
    Login nanti menggunakan OTP via HP.
    """
    
    # ===== VALIDASI NIK =====
    existing_nik = db.query(Nasabah).filter(Nasabah.nik == data.nik).first()
    if existing_nik:
        raise HTTPException(
            status_code=400, 
            detail="NIK sudah terdaftar. Silakan gunakan NIK lain atau login."
        )
    
    # ===== VALIDASI NO HP =====
    existing_phone = db.query(Nasabah).filter(Nasabah.no_hp == data.no_hp).first()
    if existing_phone:
        raise HTTPException(
            status_code=400, 
            detail="Nomor HP sudah terdaftar. Silakan gunakan nomor HP lain atau login."
        )
    
    # ===== BUAT NASABAH BARU =====
    new_nasabah = Nasabah(
        nik=data.nik,
        nama_nasabah=data.nama_nasabah,
        no_hp=data.no_hp,
        no_rekening=data.no_rekening,
        nama_bank=data.nama_bank,
        nama_pemilik_rekening=data.nama_pemilik_rekening,
        alamat=data.alamat,
        is_active=True
    )
    
    db.add(new_nasabah)
    db.commit()
    db.refresh(new_nasabah)
    
    return RegistrationSuccess(
        message="Registrasi berhasil!",
        nik=new_nasabah.nik,
        nama_nasabah=new_nasabah.nama_nasabah,
        no_hp=new_nasabah.no_hp
    )


# ========== GET PROFILE (Protected) ==========

@router.get("/profile", response_model=NasabahResponse)
def get_profile(
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """
    Get profile nasabah yang sudah login.
    Butuh Authorization header dengan JWT token.
    
    Header format: Authorization: Bearer <token>
    """
    nik = get_current_nasabah(authorization)
    
    nasabah = db.query(Nasabah).filter(Nasabah.nik == nik).first()
    if not nasabah:
        raise HTTPException(status_code=404, detail="Nasabah tidak ditemukan")
    
    return nasabah

# Matikan dengan NASABAH_BOLEH_UBAH_REKENING=false di .env kalau mau rekening hanya bisa diubah admin
NASABAH_BOLEH_UBAH_REKENING = os.getenv("NASABAH_BOLEH_UBAH_REKENING", "true").lower() == "true"
FIELD_REKENING = ("no_rekening", "nama_bank", "nama_pemilik_rekening")


# ========== EDIT PROFILE (Protected) ==========
@router.patch("/profile", response_model=NasabahResponse)
def update_profile(
    data: NasabahProfileUpdate,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    nik = get_current_nasabah(authorization)
    # Kunci baris: konsisten dengan alur penarikan yang juga mengunci baris nasabah
    nasabah = db.query(Nasabah).filter(Nasabah.nik == nik).with_for_update().first()
    if not nasabah:
        raise HTTPException(status_code=404, detail="Nasabah tidak ditemukan")
    if not nasabah.is_active:
        raise HTTPException(status_code=403, detail="Akun Anda telah dinonaktifkan. Hubungi admin.")

    perubahan = data.model_dump(exclude_unset=True)
    if perubahan.get("alamat") == "":
        perubahan["alamat"] = None
    for kolom, nilai in perubahan.items():
        if nilai is None and kolom != "alamat":
            raise HTTPException(status_code=400, detail=f"{kolom} tidak boleh dikosongkan")

    # Buang field yang nilainya sama dengan yang sudah tersimpan
    perubahan = {k: v for k, v in perubahan.items() if getattr(nasabah, k) != v}
    if not perubahan:
        return nasabah

    if "no_hp" in perubahan:
        dipakai = db.query(Nasabah).filter(
            Nasabah.no_hp == perubahan["no_hp"], Nasabah.nik != nik
        ).first()
        if dipakai:
            raise HTTPException(status_code=400, detail="Nomor HP sudah dipakai nasabah lain")

    rekening_berubah = any(k in perubahan for k in FIELD_REKENING)
    if rekening_berubah:
        if not NASABAH_BOLEH_UBAH_REKENING:
            raise HTTPException(status_code=403, detail="Perubahan rekening hanya bisa dilakukan lewat admin")
        masih_menunggu = db.query(PenarikanSaldo).filter(
            PenarikanSaldo.nik == nik,
            PenarikanSaldo.status == StatusPenarikan.menunggu,
        ).first()
        if masih_menunggu:
            raise HTTPException(
                status_code=409,
                detail="Rekening tidak bisa diubah selama ada penarikan yang menunggu diproses",
            )

    for kolom, nilai in perubahan.items():
        setattr(nasabah, kolom, nilai)
    if rekening_berubah:
        notif_rekening_diubah(db, nasabah)   # peringatan keamanan untuk nasabah itu sendiri
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Data bentrok dengan nasabah lain (mis. nomor HP)")
    db.refresh(nasabah)
    return nasabah