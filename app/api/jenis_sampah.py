from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.models import JenisSampah, DetailTransaksi
from app.schemas.jenis_sampah import (
    JenisSampahCreate, JenisSampahUpdate, JenisSampahResponse, JenisSampahNasabahResponse,
)
from app.utils.security import get_current_admin, get_current_session

router = APIRouter(prefix="/jenis-sampah", tags=["Jenis Sampah"])

# ========== NASABAH/ADMIN: LIST HARGA SAMPAH ==========
# HARUS DI ATAS "GET /{id_jenis}" milik admin di bawah!
@router.get("/nasabah/list", response_model=list[JenisSampahNasabahResponse])
def list_jenis_sampah_nasabah(
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Siapa pun yang sudah login (nasabah atau admin) bisa lihat daftar harga sampah"""
    get_current_session(authorization)
    return db.query(JenisSampah).order_by(JenisSampah.nama_jenis.asc()).all()

# ========== TAMBAH JENIS SAMPAH BARU ==========
@router.post("/create", response_model=JenisSampahResponse)
def create_jenis_sampah(
    data: JenisSampahCreate,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    get_current_admin(authorization)  # pastikan admin yang login

    existing = db.query(JenisSampah).filter(JenisSampah.nama_jenis == data.nama_jenis).first()
    if existing:
        raise HTTPException(status_code=400, detail="Jenis sampah dengan nama ini sudah ada")

    new_jenis = JenisSampah(
        nama_jenis=data.nama_jenis,
        kategori=data.kategori,
        harga_per_kg=data.harga_per_kg
    )
    db.add(new_jenis)
    db.commit()
    db.refresh(new_jenis)
    return new_jenis


# ========== LIST SEMUA JENIS SAMPAH (untuk dropdown di frontend) ==========
@router.get("/", response_model=list[JenisSampahResponse])
def list_jenis_sampah(
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    get_current_admin(authorization)
    return db.query(JenisSampah).order_by(JenisSampah.nama_jenis.asc()).all()


# ========== DETAIL 1 JENIS SAMPAH ==========
@router.get("/{id_jenis}", response_model=JenisSampahResponse)
def get_jenis_sampah(
    id_jenis: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    get_current_admin(authorization)
    jenis = db.query(JenisSampah).filter(JenisSampah.id_jenis == id_jenis).first()
    if not jenis:
        raise HTTPException(status_code=404, detail="Jenis sampah tidak ditemukan")
    return jenis


# ========== EDIT JENIS SAMPAH (misal update harga) ==========
@router.put("/{id_jenis}", response_model=JenisSampahResponse)
def update_jenis_sampah(
    id_jenis: int,
    data: JenisSampahUpdate,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    get_current_admin(authorization)
    jenis = db.query(JenisSampah).filter(JenisSampah.id_jenis == id_jenis).first()
    if not jenis:
        raise HTTPException(status_code=404, detail="Jenis sampah tidak ditemukan")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(jenis, field, value)

    db.commit()
    db.refresh(jenis)
    return jenis


# ========== HAPUS JENIS SAMPAH ==========
@router.delete("/{id_jenis}")
def delete_jenis_sampah(
    id_jenis: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    get_current_admin(authorization)
    jenis = db.query(JenisSampah).filter(JenisSampah.id_jenis == id_jenis).first()
    if not jenis:
        raise HTTPException(status_code=404, detail="Jenis sampah tidak ditemukan")

    # Cegah hapus jenis sampah yang sudah pernah dipakai di transaksi
    # (supaya history transaksi lama nggak jadi rusak/orphan)
    dipakai = db.query(DetailTransaksi).filter(DetailTransaksi.id_jenis == id_jenis).first()
    if dipakai:
        raise HTTPException(
            status_code=400,
            detail="Jenis sampah ini sudah pernah dipakai di transaksi, tidak bisa dihapus. "
                   "Ubah harga saja jika diperlukan, atau nonaktifkan (fitur belum tersedia)."
        )

    db.delete(jenis)
    db.commit()
    return {"message": f"Jenis sampah '{jenis.nama_jenis}' berhasil dihapus"}