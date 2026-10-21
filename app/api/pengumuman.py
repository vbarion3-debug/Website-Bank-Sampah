from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.models import Pengumuman, Admin, StatusPengumuman
from app.schemas.pengumuman import PengumumanCreate, PengumumanUpdate, PengumumanResponse
from app.utils.security import get_current_admin, get_current_session
from app.utils.notifikasi import notif_pengumuman_terbit, adalah_terbit

router = APIRouter(prefix="/pengumuman", tags=["Pengumuman"])


def _get_admin_object(authorization: str, db: Session) -> Admin:
    """Helper: dari session admin yang login, ambil objek Admin lengkap dari DB"""
    session = get_current_admin(authorization)
    admin = db.query(Admin).filter(Admin.username == session["sub"]).first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin tidak ditemukan")
    return admin


# ========== 1. BUAT PENGUMUMAN BARU (Admin) ==========
@router.post("/create", response_model=PengumumanResponse)
def create_pengumuman(
    data: PengumumanCreate,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    admin = _get_admin_object(authorization, db)
    new_pengumuman = Pengumuman(
        id_admin=admin.id_admin,
        judul=data.judul,
        isi=data.isi,
        gambar=data.gambar,
        status=data.status
    )
    db.add(new_pengumuman)
    db.flush()
    if adalah_terbit(new_pengumuman.status):
        notif_pengumuman_terbit(db, new_pengumuman)
    db.commit()
    db.refresh(new_pengumuman)
    return new_pengumuman


# ========== 2. NASABAH/ADMIN: LIST PENGUMUMAN YANG SUDAH TERBIT ==========
# HARUS DI ATAS "GET /{id_pengumuman}" milik admin di bawah!
@router.get("/nasabah/list", response_model=list[PengumumanResponse])
def list_pengumuman_nasabah(
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Siapa pun yang sudah login (nasabah atau admin) bisa lihat pengumuman yang sudah terbit"""
    get_current_session(authorization)

    return db.query(Pengumuman)\
        .filter(Pengumuman.status == StatusPengumuman.terbit)\
        .order_by(Pengumuman.tanggal_publikasi.desc())\
        .all()


# ========== 3. NASABAH/ADMIN: DETAIL 1 PENGUMUMAN YANG SUDAH TERBIT ==========
@router.get("/nasabah/{id_pengumuman}", response_model=PengumumanResponse)
def get_pengumuman_nasabah(
    id_pengumuman: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    get_current_session(authorization)

    pengumuman = db.query(Pengumuman).filter(
        Pengumuman.id_pengumuman == id_pengumuman,
        Pengumuman.status == StatusPengumuman.terbit
    ).first()

    if not pengumuman:
        raise HTTPException(status_code=404, detail="Pengumuman tidak ditemukan")

    return pengumuman


# ========== 4. ADMIN: LIST SEMUA PENGUMUMAN (semua status) ==========
@router.get("/", response_model=list[PengumumanResponse])
def list_pengumuman(
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    _get_admin_object(authorization, db)
    return db.query(Pengumuman).order_by(Pengumuman.tanggal_publikasi.desc()).all()


# ========== 5. ADMIN: DETAIL 1 PENGUMUMAN (semua status) ==========
@router.get("/{id_pengumuman}", response_model=PengumumanResponse)
def get_pengumuman(
    id_pengumuman: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    _get_admin_object(authorization, db)
    pengumuman = db.query(Pengumuman).filter(Pengumuman.id_pengumuman == id_pengumuman).first()
    if not pengumuman:
        raise HTTPException(status_code=404, detail="Pengumuman tidak ditemukan")
    return pengumuman


# ========== 6. ADMIN: EDIT PENGUMUMAN ==========
@router.put("/{id_pengumuman}", response_model=PengumumanResponse)
def update_pengumuman(
    id_pengumuman: int,
    data: PengumumanUpdate,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    _get_admin_object(authorization, db)
    pengumuman = db.query(Pengumuman).filter(Pengumuman.id_pengumuman == id_pengumuman).first()
    if not pengumuman:
        raise HTTPException(status_code=404, detail="Pengumuman tidak ditemukan")
    status_lama = pengumuman.status
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(pengumuman, field, value)
    db.flush()
    if adalah_terbit(pengumuman.status) and not adalah_terbit(status_lama):
        notif_pengumuman_terbit(db, pengumuman)
    db.commit()
    db.refresh(pengumuman)
    return pengumuman


# ========== 7. ADMIN: HAPUS PENGUMUMAN ==========
@router.delete("/{id_pengumuman}")
def delete_pengumuman(
    id_pengumuman: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    _get_admin_object(authorization, db)
    pengumuman = db.query(Pengumuman).filter(Pengumuman.id_pengumuman == id_pengumuman).first()
    if not pengumuman:
        raise HTTPException(status_code=404, detail="Pengumuman tidak ditemukan")
    db.delete(pengumuman)
    db.commit()
    return {"message": f"Pengumuman '{pengumuman.judul}' berhasil dihapus"}
