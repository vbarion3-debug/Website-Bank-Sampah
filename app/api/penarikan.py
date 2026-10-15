import os
from datetime import datetime
from app.core.timezone import now_wib
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.models import Nasabah, PenarikanSaldo, StatusPenarikan
from app.schemas.penarikan import PenarikanSaldoCreate, PenarikanSaldoResponse
from app.utils.bukti_transfer import media_type_bukti, path_bukti_transfer
from app.utils.security import get_current_nasabah

router = APIRouter(prefix="/penarikan", tags=["Penarikan Saldo"])

# Minimal penarikan - nilai awal, sesuaikan dengan kesepakatan client
MIN_PENARIKAN = Decimal("10000")


def kirim_file_bukti(penarikan: PenarikanSaldo) -> FileResponse:
    """Dipakai bersama oleh endpoint nasabah (di sini) dan endpoint admin."""
    if not penarikan.bukti_transfer:
        raise HTTPException(status_code=404, detail="Bukti transfer belum tersedia")
    path = path_bukti_transfer(penarikan.bukti_transfer)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File bukti transfer tidak ditemukan di server")
    return FileResponse(
        path,
        media_type=media_type_bukti(penarikan.bukti_transfer),
        headers={"Cache-Control": "private, no-store"},
    )


# ========== NASABAH: AJUKAN PENARIKAN ==========
@router.post("/ajukan", response_model=PenarikanSaldoResponse)
def ajukan_penarikan(
    data: PenarikanSaldoCreate,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    """
    Nasabah mengajukan penarikan saldo.
    - Saldo langsung dipotong (ditahan) saat pengajuan, dikembalikan kalau ditolak/dibatalkan.
    - Rekening tujuan = rekening terdaftar nasabah (di-snapshot), BUKAN dari input.
    - Hanya boleh 1 pengajuan berstatus 'menunggu' dalam satu waktu.
    """
    nik = get_current_nasabah(authorization)

    # Kunci baris nasabah: dua request barengan dari nasabah yang sama jadi antri, bukan balapan
    nasabah = db.query(Nasabah).filter(Nasabah.nik == nik).with_for_update().first()
    if not nasabah:
        raise HTTPException(status_code=404, detail="Nasabah tidak ditemukan")
    if not nasabah.is_active:
        raise HTTPException(status_code=403, detail="Akun Anda telah dinonaktifkan. Hubungi admin.")

    if data.jumlah < MIN_PENARIKAN:
        minimal = f"{MIN_PENARIKAN:,.0f}".replace(",", ".")
        raise HTTPException(status_code=400, detail=f"Minimal penarikan Rp{minimal}")
    if data.jumlah > nasabah.saldo:
        raise HTTPException(status_code=400, detail="Saldo tidak cukup")

    sudah_ada = db.query(PenarikanSaldo).filter(
        PenarikanSaldo.nik == nik,
        PenarikanSaldo.status == StatusPenarikan.menunggu,
    ).first()
    if sudah_ada:
        raise HTTPException(
            status_code=409,
            detail="Masih ada pengajuan penarikan yang menunggu diproses. Tunggu selesai atau batalkan dulu.",
        )

    penarikan = PenarikanSaldo(
        nik=nik,
        jumlah=data.jumlah,
        nama_bank=nasabah.nama_bank,
        no_rekening=nasabah.no_rekening,
        nama_pemilik_rekening=nasabah.nama_pemilik_rekening,
    )
    nasabah.saldo = nasabah.saldo - data.jumlah
    db.add(penarikan)
    try:
        db.commit()
    except IntegrityError:  # jaring pengaman dari unique index "1 pengajuan menunggu per nasabah"
        db.rollback()
        raise HTTPException(status_code=409, detail="Masih ada pengajuan penarikan yang menunggu diproses.")
    db.refresh(penarikan)
    return penarikan


# ========== NASABAH: RIWAYAT PENARIKAN SENDIRI ==========
# HARUS DI ATAS "/{id_penarikan}", kalau tidak "history" dianggap id dan kena 422
@router.get("/history", response_model=list[PenarikanSaldoResponse])
def riwayat_penarikan(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    nik = get_current_nasabah(authorization)
    return (
        db.query(PenarikanSaldo)
        .filter(PenarikanSaldo.nik == nik)
        .order_by(PenarikanSaldo.tanggal_pengajuan.desc())
        .all()
    )


# ========== NASABAH: DETAIL 1 PENARIKAN ==========
@router.get("/{id_penarikan}", response_model=PenarikanSaldoResponse)
def detail_penarikan(
    id_penarikan: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    nik = get_current_nasabah(authorization)
    penarikan = db.query(PenarikanSaldo).filter(
        PenarikanSaldo.id_penarikan == id_penarikan,
        PenarikanSaldo.nik == nik,
    ).first()
    if not penarikan:
        raise HTTPException(status_code=404, detail="Pengajuan penarikan tidak ditemukan")
    return penarikan


# ========== NASABAH: LIHAT BUKTI TRANSFER ==========
@router.get("/{id_penarikan}/bukti")
def lihat_bukti_transfer(
    id_penarikan: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    """Bukti transfer hanya bisa dilihat nasabah pemilik pengajuan (dan admin lewat endpoint admin)."""
    nik = get_current_nasabah(authorization)
    penarikan = db.query(PenarikanSaldo).filter(
        PenarikanSaldo.id_penarikan == id_penarikan,
        PenarikanSaldo.nik == nik,
    ).first()
    if not penarikan:
        raise HTTPException(status_code=404, detail="Pengajuan penarikan tidak ditemukan")
    return kirim_file_bukti(penarikan)


# ========== NASABAH: BATALKAN PENGAJUAN ==========
@router.post("/{id_penarikan}/batal", response_model=PenarikanSaldoResponse)
def batalkan_penarikan(
    id_penarikan: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    """Nasabah membatalkan pengajuan selama masih 'menunggu'. Saldo dikembalikan."""
    nik = get_current_nasabah(authorization)

    # Urutan kunci: penarikan dulu, baru nasabah (sama seperti di sisi admin) supaya tidak deadlock
    penarikan = db.query(PenarikanSaldo).filter(
        PenarikanSaldo.id_penarikan == id_penarikan,
        PenarikanSaldo.nik == nik,
    ).with_for_update().first()
    if not penarikan:
        raise HTTPException(status_code=404, detail="Pengajuan penarikan tidak ditemukan")
    if penarikan.status != StatusPenarikan.menunggu:
        raise HTTPException(status_code=400, detail="Pengajuan ini sudah diproses admin, tidak bisa dibatalkan")

    nasabah = db.query(Nasabah).filter(Nasabah.nik == nik).with_for_update().first()
    nasabah.saldo = nasabah.saldo + penarikan.jumlah

    penarikan.status = StatusPenarikan.dibatalkan
    penarikan.tanggal_diproses = now_wib()
    db.commit()
    db.refresh(penarikan)
    return penarikan
