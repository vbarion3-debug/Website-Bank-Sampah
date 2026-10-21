from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_
from typing import Optional

from app.core.database import get_db
from app.models.models import MitraPengepul, HargaMitraPengepul, JenisSampah
from app.schemas.mitra_pengepul import (
    MitraPengepulCreate, MitraPengepulUpdate, MitraStatusUpdate,
    MitraPengepulResponse, MitraPengepulDetailResponse, MitraListResponse,
    HargaMitraCreate, HargaMitraUpdate, HargaMitraResponse
)
from app.utils.security import get_current_admin

router = APIRouter(prefix="/mitra-pengepul", tags=["Mitra Pengepul"])



# ========== TAMBAH MITRA BARU ==========
@router.post("/create", response_model=MitraPengepulResponse)
def create_mitra(
    data: MitraPengepulCreate,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    get_current_admin(authorization)

    new_mitra = MitraPengepul(**data.model_dump())
    db.add(new_mitra)
    db.commit()
    db.refresh(new_mitra)
    return new_mitra


# ========== LIST + SEARCH MITRA ==========
@router.get("/", response_model=MitraListResponse)
def list_mitra(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, description="Cari berdasarkan nama mitra atau nama penanggung jawab"),
    is_active: Optional[bool] = Query(None, description="Filter status kerjasama aktif/tidak"),
):
    get_current_admin(authorization)

    query = db.query(MitraPengepul)

    if search:
        like_pattern = f"%{search}%"
        query = query.filter(
            or_(
                MitraPengepul.nama_mitra.ilike(like_pattern),
                MitraPengepul.nama_penanggung_jawab.ilike(like_pattern),
            )
        )

    if is_active is not None:
        query = query.filter(MitraPengepul.is_active == is_active)

    hasil = query.order_by(MitraPengepul.nama_mitra.asc()).all()
    return MitraListResponse(total=len(hasil), data=hasil)


# ========== DETAIL 1 MITRA (termasuk daftar harga jenis sampah) ==========
@router.get("/{id_mitra}", response_model=MitraPengepulDetailResponse)
def get_mitra_detail(
    id_mitra: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    get_current_admin(authorization)

    mitra = (
    db.query(MitraPengepul)
    .options(
        joinedload(MitraPengepul.harga_jenis_sampah)
        .joinedload(HargaMitraPengepul.jenis_sampah)
    )
    .filter(MitraPengepul.id_mitra == id_mitra)
    .first()
    )
    
    if not mitra:
        raise HTTPException(status_code=404, detail="Mitra pengepul tidak ditemukan")
    return mitra


# ========== EDIT DATA MITRA ==========
@router.put("/{id_mitra}", response_model=MitraPengepulResponse)
def update_mitra(
    id_mitra: int,
    data: MitraPengepulUpdate,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    get_current_admin(authorization)

    mitra = db.query(MitraPengepul).filter(MitraPengepul.id_mitra == id_mitra).first()
    if not mitra:
        raise HTTPException(status_code=404, detail="Mitra pengepul tidak ditemukan")

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(mitra, field, value)

    db.commit()
    db.refresh(mitra)
    return mitra


# ========== TOGGLE AKTIF/NONAKTIF KERJASAMA ==========
@router.patch("/{id_mitra}/status", response_model=MitraPengepulResponse)
def update_status_mitra(
    id_mitra: int,
    data: MitraStatusUpdate,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    get_current_admin(authorization)

    mitra = db.query(MitraPengepul).filter(MitraPengepul.id_mitra == id_mitra).first()
    if not mitra:
        raise HTTPException(status_code=404, detail="Mitra pengepul tidak ditemukan")

    mitra.is_active = data.is_active
    db.commit()
    db.refresh(mitra)
    return mitra


# ========== HAPUS MITRA ==========
@router.delete("/{id_mitra}")
def delete_mitra(
    id_mitra: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    get_current_admin(authorization)

    mitra = db.query(MitraPengepul).filter(MitraPengepul.id_mitra == id_mitra).first()
    if not mitra:
        raise HTTPException(status_code=404, detail="Mitra pengepul tidak ditemukan")

    db.delete(mitra)  # cascade akan otomatis hapus harga_jenis_sampah terkait
    db.commit()
    return {"message": f"Mitra pengepul '{mitra.nama_mitra}' berhasil dihapus"}


# ========== TAMBAH/UPDATE HARGA JENIS SAMPAH UNTUK MITRA INI ==========
@router.post("/{id_mitra}/harga", response_model=HargaMitraResponse)
def set_harga_mitra(
    id_mitra: int,
    data: HargaMitraCreate,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Tambah harga baru untuk 1 jenis sampah di mitra ini. Kalau sudah ada, gunakan endpoint PUT untuk update."""
    get_current_admin(authorization)

    mitra = db.query(MitraPengepul).filter(MitraPengepul.id_mitra == id_mitra).first()
    if not mitra:
        raise HTTPException(status_code=404, detail="Mitra pengepul tidak ditemukan")

    jenis = db.query(JenisSampah).filter(JenisSampah.id_jenis == data.id_jenis).first()
    if not jenis:
        raise HTTPException(status_code=404, detail="Jenis sampah tidak ditemukan")

    existing = db.query(HargaMitraPengepul).filter(
        HargaMitraPengepul.id_mitra == id_mitra,
        HargaMitraPengepul.id_jenis == data.id_jenis
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Harga untuk jenis sampah ini sudah ada di mitra ini. Gunakan PUT untuk update.")

    new_harga = HargaMitraPengepul(
        id_mitra=id_mitra,
        id_jenis=data.id_jenis,
        harga_beli_per_kg=data.harga_beli_per_kg
    )
    db.add(new_harga)
    db.commit()
    db.refresh(new_harga)

    return HargaMitraResponse(
        id_harga=new_harga.id_harga,
        id_jenis=new_harga.id_jenis,
        nama_jenis=jenis.nama_jenis,
        harga_beli_per_kg=new_harga.harga_beli_per_kg,
        tanggal_diperbarui=new_harga.tanggal_diperbarui
    )


# ========== EDIT HARGA JENIS SAMPAH MITRA ==========
@router.put("/{id_mitra}/harga/{id_harga}", response_model=HargaMitraResponse)
def update_harga_mitra(
    id_mitra: int,
    id_harga: int,
    data: HargaMitraUpdate,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    get_current_admin(authorization)

    harga = db.query(HargaMitraPengepul).filter(
        HargaMitraPengepul.id_harga == id_harga,
        HargaMitraPengepul.id_mitra == id_mitra
    ).first()
    if not harga:
        raise HTTPException(status_code=404, detail="Data harga tidak ditemukan untuk mitra ini")

    harga.harga_beli_per_kg = data.harga_beli_per_kg
    db.commit()
    db.refresh(harga)

    jenis = db.query(JenisSampah).filter(JenisSampah.id_jenis == harga.id_jenis).first()
    return HargaMitraResponse(
        id_harga=harga.id_harga,
        id_jenis=harga.id_jenis,
        nama_jenis=jenis.nama_jenis if jenis else None,
        harga_beli_per_kg=harga.harga_beli_per_kg,
        tanggal_diperbarui=harga.tanggal_diperbarui
    )


# ========== HAPUS HARGA JENIS SAMPAH DARI MITRA ==========
@router.delete("/{id_mitra}/harga/{id_harga}")
def delete_harga_mitra(
    id_mitra: int,
    id_harga: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    get_current_admin(authorization)

    harga = db.query(HargaMitraPengepul).filter(
        HargaMitraPengepul.id_harga == id_harga,
        HargaMitraPengepul.id_mitra == id_mitra
    ).first()
    if not harga:
        raise HTTPException(status_code=404, detail="Data harga tidak ditemukan untuk mitra ini")

    db.delete(harga)
    db.commit()
    return {"message": "Harga jenis sampah untuk mitra ini berhasil dihapus"}