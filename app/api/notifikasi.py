from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.models import Notifikasi, PesanChat
from app.schemas.notifikasi import (
    AksiMassalResponse, NotifikasiListResponse, NotifikasiRingkasanResponse, NotifikasiResponse,
)
from app.utils.security import get_current_nasabah

router = APIRouter(prefix="/notifikasi", tags=["Notifikasi Nasabah"])

N = Notifikasi


# ========== RINGKASAN (ringan, untuk polling badge) ==========
# Route statis (/ringkasan, /baca-semua) HARUS di atas route dengan {id_notifikasi}.
@router.get("/ringkasan", response_model=NotifikasiRingkasanResponse)
def ringkasan_notifikasi(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    nik = get_current_nasabah(authorization)
    belum_dibaca = db.query(func.count(N.id_notifikasi)).filter(
        N.nik == nik, N.is_read.is_(False)
    ).scalar()
    id_terbaru = db.query(func.max(N.id_notifikasi)).filter(N.nik == nik).scalar()
    chat_belum_dibaca = db.query(func.count(PesanChat.id)).filter(
        PesanChat.nik_nasabah == nik,
        PesanChat.sender_type == "admin",
        PesanChat.is_read.is_(False),
    ).scalar()
    return NotifikasiRingkasanResponse(
        belum_dibaca=belum_dibaca,
        chat_belum_dibaca=chat_belum_dibaca,
        id_terbaru=id_terbaru,
    )


# ========== LIST ==========
@router.get("/", response_model=NotifikasiListResponse)
def list_notifikasi(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
    hanya_belum_dibaca: bool = Query(False),
    setelah_id: Optional[int] = Query(None, ge=0, description="Hanya yang id-nya lebih baru (untuk toast)"),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    nik = get_current_nasabah(authorization)
    filters = [N.nik == nik]
    if hanya_belum_dibaca:
        filters.append(N.is_read.is_(False))
    if setelah_id is not None:
        filters.append(N.id_notifikasi > setelah_id)

    total = db.query(func.count(N.id_notifikasi)).filter(*filters).scalar()
    belum_dibaca = db.query(func.count(N.id_notifikasi)).filter(
        N.nik == nik, N.is_read.is_(False)
    ).scalar()
    rows = (
        db.query(N)
        .filter(*filters)
        .order_by(N.tanggal.desc(), N.id_notifikasi.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return NotifikasiListResponse(total=total, belum_dibaca=belum_dibaca, data=rows)


# ========== TANDAI SEMUA DIBACA ==========
@router.post("/baca-semua", response_model=AksiMassalResponse)
def baca_semua(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    nik = get_current_nasabah(authorization)
    jumlah = db.query(N).filter(N.nik == nik, N.is_read.is_(False)).update(
        {"is_read": True}, synchronize_session=False
    )
    db.commit()
    return AksiMassalResponse(diperbarui=jumlah)


# ========== TANDAI 1 DIBACA ==========
@router.post("/{id_notifikasi}/baca", response_model=NotifikasiResponse)
def baca_satu(
    id_notifikasi: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    nik = get_current_nasabah(authorization)
    n = db.query(N).filter(N.id_notifikasi == id_notifikasi, N.nik == nik).first()
    if not n:
        raise HTTPException(status_code=404, detail="Notifikasi tidak ditemukan")
    if not n.is_read:
        n.is_read = True
        db.commit()
        db.refresh(n)
    return n


# ========== HAPUS 1 ==========
@router.delete("/{id_notifikasi}")
def hapus_notifikasi(
    id_notifikasi: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    nik = get_current_nasabah(authorization)
    n = db.query(N).filter(N.id_notifikasi == id_notifikasi, N.nik == nik).first()
    if not n:
        raise HTTPException(status_code=404, detail="Notifikasi tidak ditemukan")
    db.delete(n)
    db.commit()
    return {"message": "Notifikasi dihapus"}