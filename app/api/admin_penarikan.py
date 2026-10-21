from datetime import datetime
from app.core.timezone import now_wib
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.api.penarikan import kirim_file_bukti
from app.core.database import get_db
from app.models.models import Admin, Nasabah, PenarikanSaldo, StatusPenarikan
from app.schemas.penarikan import (
    PenarikanAdminListResponse, PenarikanAdminResponse,
    PenarikanSaldoResponse, PenarikanTolak, StatusPenarikanEnum,
)
from app.utils.bukti_transfer import hapus_bukti_transfer, simpan_bukti_transfer
from app.utils.security import get_current_admin
from app.utils.notifikasi import notif_penarikan_selesai, notif_penarikan_ditolak

router = APIRouter(prefix="/admin/penarikan", tags=["Admin - Penarikan Saldo"])


# ========== HELPER ==========

def _get_admin_object(authorization: str, db: Session) -> Admin:
    session = get_current_admin(authorization)
    admin = db.query(Admin).filter(Admin.username == session["sub"]).first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin tidak ditemukan")
    return admin


def _to_admin_response(p: PenarikanSaldo) -> PenarikanAdminResponse:
    dasar = PenarikanSaldoResponse.model_validate(p).model_dump()
    return PenarikanAdminResponse(
        **dasar,
        nama_nasabah=p.nasabah.nama_nasabah if p.nasabah else None,
        no_hp_nasabah=p.nasabah.no_hp if p.nasabah else None,
        diproses_oleh=p.admin.username if p.admin else None,
    )


def _ambil_untuk_diproses(db: Session, id_penarikan: int) -> PenarikanSaldo:
    """
    Ambil pengajuan dengan row lock + pastikan masih 'menunggu'.
    Lock ini yang mencegah dua admin (atau admin vs pembatalan nasabah) memproses pengajuan yang sama.
    """
    p = db.query(PenarikanSaldo).filter(
        PenarikanSaldo.id_penarikan == id_penarikan
    ).with_for_update().first()
    if not p:
        raise HTTPException(status_code=404, detail="Pengajuan penarikan tidak ditemukan")
    if p.status != StatusPenarikan.menunggu:
        raise HTTPException(
            status_code=400,
            detail=f"Pengajuan ini sudah berstatus '{p.status.value}', tidak bisa diproses lagi",
        )
    return p


# ========== ADMIN: LIST PENGAJUAN ==========
@router.get("/", response_model=PenarikanAdminListResponse)
def list_penarikan(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
    status: Optional[StatusPenarikanEnum] = Query(None, description="Filter status, mis. 'menunggu' untuk antrean"),
    nik: Optional[str] = Query(None, description="Filter berdasarkan NIK nasabah"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    get_current_admin(authorization)

    filters = []
    if status:
        filters.append(PenarikanSaldo.status == StatusPenarikan(status.value))
    if nik:
        filters.append(PenarikanSaldo.nik == nik)

    total = db.query(func.count(PenarikanSaldo.id_penarikan)).filter(*filters).scalar()

    # Antrean 'menunggu' paling lama di atas (FIFO), selain itu terbaru di atas
    urutan = (
        PenarikanSaldo.tanggal_pengajuan.asc()
        if status == StatusPenarikanEnum.menunggu
        else PenarikanSaldo.tanggal_pengajuan.desc()
    )
    rows = (
        db.query(PenarikanSaldo)
        .options(joinedload(PenarikanSaldo.nasabah), joinedload(PenarikanSaldo.admin))
        .filter(*filters)
        .order_by(urutan)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return PenarikanAdminListResponse(total=total, data=[_to_admin_response(p) for p in rows])


# ========== ADMIN: DETAIL 1 PENGAJUAN ==========
@router.get("/{id_penarikan}", response_model=PenarikanAdminResponse)
def detail_penarikan_admin(
    id_penarikan: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    get_current_admin(authorization)
    p = db.query(PenarikanSaldo).filter(PenarikanSaldo.id_penarikan == id_penarikan).first()
    if not p:
        raise HTTPException(status_code=404, detail="Pengajuan penarikan tidak ditemukan")
    return _to_admin_response(p)


# ========== ADMIN: LIHAT BUKTI TRANSFER ==========
@router.get("/{id_penarikan}/bukti")
def lihat_bukti_admin(
    id_penarikan: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    get_current_admin(authorization)
    p = db.query(PenarikanSaldo).filter(PenarikanSaldo.id_penarikan == id_penarikan).first()
    if not p:
        raise HTTPException(status_code=404, detail="Pengajuan penarikan tidak ditemukan")
    return kirim_file_bukti(p)


# ========== ADMIN: KONFIRMASI SELESAI (WAJIB UPLOAD BUKTI TRANSFER) ==========
@router.post("/{id_penarikan}/selesai", response_model=PenarikanAdminResponse)
def selesaikan_penarikan(
    id_penarikan: int,
    bukti_transfer: UploadFile = File(..., description="Screenshot bukti transfer (JPG/PNG/WEBP, maks 5 MB)"),
    catatan: Optional[str] = Form(None, max_length=500),
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    """
    Admin sudah mentransfer manual ke rekening nasabah, lalu upload screenshot buktinya di sini.
    Request ini multipart/form-data (bukan JSON). Tanpa file bukti, request ditolak (422).
    Saldo TIDAK diubah lagi karena sudah dipotong saat nasabah mengajukan.
    """
    admin = _get_admin_object(authorization, db)
    penarikan = _ambil_untuk_diproses(db, id_penarikan)

    nama_file = simpan_bukti_transfer(bukti_transfer)  # validasi isi file + simpan di folder privat
    try:
        penarikan.status = StatusPenarikan.selesai
        penarikan.bukti_transfer = nama_file
        penarikan.keterangan = catatan
        penarikan.id_admin = admin.id_admin
        penarikan.tanggal_diproses = now_wib()
        notif_penarikan_selesai(db, penarikan)
        db.commit()
    except Exception:
        db.rollback()
        hapus_bukti_transfer(nama_file)  # jangan tinggalkan file yatim kalau commit gagal
        raise

    db.refresh(penarikan)
    return _to_admin_response(penarikan)


# ========== ADMIN: TOLAK PENGAJUAN (SALDO DIKEMBALIKAN) ==========
@router.post("/{id_penarikan}/tolak", response_model=PenarikanAdminResponse)
def tolak_penarikan(
    id_penarikan: int,
    data: PenarikanTolak,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    admin = _get_admin_object(authorization, db)
    penarikan = _ambil_untuk_diproses(db, id_penarikan)

    nasabah = db.query(Nasabah).filter(Nasabah.nik == penarikan.nik).with_for_update().first()
    nasabah.saldo = nasabah.saldo + penarikan.jumlah  # kembalikan saldo yang ditahan

    penarikan.status = StatusPenarikan.ditolak
    penarikan.keterangan = data.alasan
    penarikan.id_admin = admin.id_admin
    penarikan.tanggal_diproses = now_wib()
    notif_penarikan_ditolak(db, penarikan)
    db.commit()
    db.refresh(penarikan)
    return _to_admin_response(penarikan)
