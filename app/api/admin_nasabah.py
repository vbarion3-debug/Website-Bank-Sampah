from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import Optional
from decimal import Decimal

from app.core.database import get_db
from app.models.models import Nasabah, RiwayatSaldoAdjustment, Admin
from app.schemas.admin_nasabah import (
    NasabahAdminResponse, NasabahListResponse,
    NasabahAdminUpdate, NasabahStatusUpdate, SaldoAdjustment,
    RiwayatSaldoAdjustmentResponse
)
from app.utils.security import get_current_admin
from app.utils.notifikasi import notif_saldo_manual

router = APIRouter(prefix="/admin/nasabah", tags=["Admin - Kelola Nasabah"])


# ========== LIST + SEARCH SEMUA NASABAH ==========
@router.get("/", response_model=NasabahListResponse)
def list_nasabah(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, description="Cari berdasarkan NIK, nama, atau no HP"),
    is_active: Optional[bool] = Query(None, description="Filter status aktif/nonaktif"),
):
    """
    Admin lihat semua nasabah, bisa search dan filter.
    Search mencocokkan NIK, nama_nasabah, atau no_hp (partial match, case-insensitive untuk nama).
    """
    get_current_admin(authorization)

    query = db.query(Nasabah)

    if search:
        like_pattern = f"%{search}%"
        query = query.filter(
            or_(
                Nasabah.nik.ilike(like_pattern),
                Nasabah.nama_nasabah.ilike(like_pattern),
                Nasabah.no_hp.ilike(like_pattern),
            )
        )

    if is_active is not None:
        query = query.filter(Nasabah.is_active == is_active)

    hasil = query.order_by(Nasabah.tanggal_daftar.desc()).all()

    return NasabahListResponse(total=len(hasil), data=hasil)


# ========== DETAIL 1 NASABAH ==========
@router.get("/{nik}", response_model=NasabahAdminResponse)
def get_nasabah_detail(
    nik: str,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    get_current_admin(authorization)

    nasabah = db.query(Nasabah).filter(Nasabah.nik == nik).first()
    if not nasabah:
        raise HTTPException(status_code=404, detail="Nasabah tidak ditemukan")
    return nasabah


# ========== EDIT DATA PRIBADI & REKENING ==========
@router.put("/{nik}", response_model=NasabahAdminResponse)
def update_nasabah(
    nik: str,
    data: NasabahAdminUpdate,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    get_current_admin(authorization)

    nasabah = db.query(Nasabah).filter(Nasabah.nik == nik).first()
    if not nasabah:
        raise HTTPException(status_code=404, detail="Nasabah tidak ditemukan")

    # Kalau no_hp diubah, pastikan gak bentrok dengan nasabah lain
    update_data = data.model_dump(exclude_unset=True)
    if "no_hp" in update_data and update_data["no_hp"] != nasabah.no_hp:
        bentrok = db.query(Nasabah).filter(
            Nasabah.no_hp == update_data["no_hp"],
            Nasabah.nik != nik
        ).first()
        if bentrok:
            raise HTTPException(status_code=400, detail="Nomor HP sudah dipakai nasabah lain")

    for field, value in update_data.items():
        setattr(nasabah, field, value)

    db.commit()
    db.refresh(nasabah)
    return nasabah


# ========== TOGGLE AKTIF / NONAKTIF ==========
@router.patch("/{nik}/status", response_model=NasabahAdminResponse)
def update_status_nasabah(
    nik: str,
    data: NasabahStatusUpdate,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    get_current_admin(authorization)

    nasabah = db.query(Nasabah).filter(Nasabah.nik == nik).first()
    if not nasabah:
        raise HTTPException(status_code=404, detail="Nasabah tidak ditemukan")

    nasabah.is_active = data.is_active
    db.commit()
    db.refresh(nasabah)

    status_text = "diaktifkan" if data.is_active else "dinonaktifkan"
    return nasabah


# ========== KOREKSI SALDO MANUAL (dengan audit log) ==========
@router.patch("/{nik}/saldo", response_model=NasabahAdminResponse)
def adjust_saldo_nasabah(
    nik: str,
    data: SaldoAdjustment,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """
    Koreksi saldo manual oleh admin (bukan dari transaksi normal).
    Setiap koreksi otomatis tercatat di riwayat_saldo_adjustment untuk audit trail.
    """
    admin_session = get_current_admin(authorization)

    nasabah = db.query(Nasabah).filter(Nasabah.nik == nik).first()
    if not nasabah:
        raise HTTPException(status_code=404, detail="Nasabah tidak ditemukan")

    # Ambil objek Admin lengkap dari session (untuk id_admin di log)
    admin = db.query(Admin).filter(Admin.username == admin_session["sub"]).first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin tidak ditemukan")

    saldo_sebelum = nasabah.saldo
    saldo_sesudah = saldo_sebelum + data.jumlah

    if saldo_sesudah < 0:
        raise HTTPException(status_code=400, detail="Saldo tidak boleh menjadi negatif")

    # Catat log audit SEBELUM update saldo
    log_adjustment = RiwayatSaldoAdjustment(
        nik=nik,
        id_admin=admin.id_admin,
        jumlah=data.jumlah,
        saldo_sebelum=saldo_sebelum,
        saldo_sesudah=saldo_sesudah,
        keterangan=data.keterangan
    )
    db.add(log_adjustment)

    nasabah.saldo = saldo_sesudah
    notif_saldo_manual(db, nik, data.jumlah, saldo_sesudah, data.keterangan)
    db.commit()
    db.refresh(nasabah)
    return nasabah


# ========== LIHAT RIWAYAT KOREKSI SALDO 1 NASABAH ==========
@router.get("/{nik}/saldo/riwayat", response_model=list[RiwayatSaldoAdjustmentResponse])
def get_riwayat_saldo(
    nik: str,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    get_current_admin(authorization)

    nasabah = db.query(Nasabah).filter(Nasabah.nik == nik).first()
    if not nasabah:
        raise HTTPException(status_code=404, detail="Nasabah tidak ditemukan")

    return db.query(RiwayatSaldoAdjustment)\
        .filter(RiwayatSaldoAdjustment.nik == nik)\
        .order_by(RiwayatSaldoAdjustment.tanggal.desc())\
        .all()