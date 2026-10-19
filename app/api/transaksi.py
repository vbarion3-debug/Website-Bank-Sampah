import base64
import os
import uuid
from datetime import date, datetime
from app.core.timezone import now_wib

import json
from typing import List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Header, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session
from decimal import Decimal

from app.core.database import get_db
from app.models.models import (Transaksi, DetailTransaksi,
                               JenisSampah, Nasabah, DataIoT)
from app.schemas.transaksi import (TransaksiCreate, TransaksiResponse, DetailTransaksiResponse,
                                   TransaksiIoTCreate, TransaksiHistoryResponse,
                                   DetailTransaksiCreate, DetailTransaksiUpdate)
from app.utils.security import get_current_admin, get_current_nasabah, verify_iot_api_key

router = APIRouter(prefix="/transaksi", tags=["Transaksi"])

# ========== KONSTAN & HELPER ==========

CONFIDENCE_THRESHOLD = Decimal("80")

UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "static", "uploads", "sampah"
)
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _save_base64_image(base64_str: Optional[str]) -> Optional[str]:
    """Decode base64 foto dari IoT dan simpan ke /static/uploads/sampah/. Return URL relatif, atau None kalau kosong/gagal."""
    if not base64_str:
        return None
    try:
        if "," in base64_str and base64_str.strip().startswith("data:"):
            base64_str = base64_str.split(",", 1)[1]
        image_bytes = base64.b64decode(base64_str)
        filename = f"{uuid.uuid4().hex}.jpg"
        filepath = os.path.join(UPLOAD_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(image_bytes)
        return f"/static/uploads/sampah/{filename}"
    except Exception:
        return None


def _serialize_detail(detail: DetailTransaksi, db: Session) -> DetailTransaksiResponse:
    """Gabungkan 1 item DetailTransaksi dengan data IoT terkait (kalau ada) + status flag."""
    data_iot = (
        db.query(DataIoT)
        .filter(DataIoT.id_detail == detail.id_detail)
        .first()
    )
    confidence = data_iot.confidence if data_iot else None
    is_flagged = confidence is not None and confidence < CONFIDENCE_THRESHOLD

    return DetailTransaksiResponse(
        id_detail=detail.id_detail,
        id_jenis=detail.id_jenis,
        nama_jenis=detail.jenis_sampah.nama_jenis if detail.jenis_sampah else None,
        berat=detail.berat,
        harga_per_kg=detail.harga_per_kg,
        subtotal=detail.subtotal,
        jenis_terdeteksi=data_iot.jenis_terdeteksi if data_iot else None,
        confidence=confidence,
        foto_url=(data_iot.foto_url if data_iot and data_iot.foto_url else detail.foto_url),
        is_flagged=is_flagged,
        ditambahkan_manual=detail.ditambahkan_manual,
        dikoreksi_oleh=detail.dikoreksi_oleh,
        dikoreksi_at=detail.dikoreksi_at,
    )


def _recompute_totals(transaksi: Transaksi, db: Session):
    """Hitung ulang total_berat & total_nilai transaksi dari semua detail-nya saat ini."""
    details = db.query(DetailTransaksi).filter(DetailTransaksi.id_transaksi == transaksi.id_transaksi).all()
    transaksi.total_berat = sum((d.berat for d in details), Decimal(0))
    transaksi.total_nilai = sum((d.subtotal for d in details), Decimal(0))


def _to_history_response(transaksi: Transaksi, db: Session) -> TransaksiHistoryResponse:
    nasabah = db.query(Nasabah).filter(Nasabah.nik == transaksi.nik).first()
    details = [_serialize_detail(d, db) for d in transaksi.details]
    return TransaksiHistoryResponse(
        id_transaksi=transaksi.id_transaksi,
        nik=transaksi.nik,
        nama_nasabah=nasabah.nama_nasabah if nasabah else None,
        tanggal_transaksi=transaksi.tanggal_transaksi,
        total_berat=transaksi.total_berat,
        total_nilai=transaksi.total_nilai,
        keterangan=transaksi.keterangan,
        ada_flag=any(d.is_flagged for d in details),
        details=details,
        dibuat_manual=transaksi.dibuat_manual,
        dibuat_oleh=transaksi.dibuat_oleh,
    )


# ========== ADMIN: BUAT TRANSAKSI SETORAN SAMPAH (MANUAL) ==========
@router.post("/create", response_model=TransaksiResponse)
def create_transaksi(
    data: TransaksiCreate,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """
    Admin mencatat transaksi setoran sampah nasabah.
    Harga per kg diambil dari tabel jenis_sampah (bukan dari input admin),
    supaya harga tidak bisa dimanipulasi.
    Saldo nasabah otomatis bertambah sesuai total_nilai.
    """
    get_current_admin(authorization)

    nasabah = db.query(Nasabah).filter(Nasabah.nik == data.nik).first()
    if not nasabah:
        raise HTTPException(status_code=404, detail="Nasabah dengan NIK tersebut tidak ditemukan")
    if not nasabah.is_active:
        raise HTTPException(status_code=403, detail="Akun nasabah ini sedang dinonaktifkan")

    total_berat = Decimal(0)
    total_nilai = Decimal(0)
    detail_list = []

    for item in data.detail:
        jenis = db.query(JenisSampah).filter(JenisSampah.id_jenis == item.id_jenis).first()
        if not jenis:
            raise HTTPException(status_code=404, detail=f"Jenis sampah id {item.id_jenis} tidak ditemukan")

        harga_per_kg = jenis.harga_per_kg
        subtotal = item.berat * harga_per_kg
        total_berat += item.berat
        total_nilai += subtotal

        detail_list.append({
            "id_jenis": item.id_jenis,
            "berat": item.berat,
            "harga_per_kg": harga_per_kg,
            "subtotal": subtotal
        })

    transaksi = Transaksi(
        nik=data.nik,
        total_berat=total_berat,
        total_nilai=total_nilai,
        keterangan=data.keterangan
    )
    db.add(transaksi)
    db.flush()

    detail_responses = []
    for detail in detail_list:
        detail_transaksi = DetailTransaksi(
            id_transaksi=transaksi.id_transaksi,
            id_jenis=detail["id_jenis"],
            berat=detail["berat"],
            harga_per_kg=detail["harga_per_kg"],
            subtotal=detail["subtotal"]
        )
        db.add(detail_transaksi)
        db.flush()
        detail_responses.append(_serialize_detail(detail_transaksi, db))

    nasabah.saldo = nasabah.saldo + total_nilai
    db.commit()
    db.refresh(transaksi)
    db.refresh(nasabah)

    return TransaksiResponse(
        id_transaksi=transaksi.id_transaksi,
        nik=transaksi.nik,
        tanggal_transaksi=transaksi.tanggal_transaksi,
        total_berat=transaksi.total_berat,
        total_nilai=transaksi.total_nilai,
        keterangan=transaksi.keterangan,
        saldo_nasabah_sekarang=nasabah.saldo,
        details=detail_responses
    )


MAX_FOTO_BYTES = 5 * 1024 * 1024  # 5 MB per foto
MAX_ITEM_MANUAL = 20


def _deteksi_ekstensi_foto(head: bytes) -> Optional[str]:
    """Tentukan tipe dari ISI file (magic bytes), bukan dari nama/content-type kiriman client."""
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


def _simpan_foto_sampah(upload: UploadFile) -> str:
    """Validasi lalu simpan foto. Return URL relatif (/static/uploads/sampah/xxx.jpg)."""
    data = upload.file.read(MAX_FOTO_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Ada file foto yang kosong")
    if len(data) > MAX_FOTO_BYTES:
        raise HTTPException(status_code=413, detail="Ukuran tiap foto maksimal 5 MB")
    ekstensi = _deteksi_ekstensi_foto(data[:12])
    if ekstensi is None:
        raise HTTPException(status_code=415, detail="Format foto harus JPG, PNG, atau WEBP")
    nama_file = f"{uuid.uuid4().hex}{ekstensi}"
    with open(os.path.join(UPLOAD_DIR, nama_file), "wb") as f:
        f.write(data)
    return f"/static/uploads/sampah/{nama_file}"


def _hapus_foto_sampah(url: Optional[str]) -> None:
    """Bersih-bersih kalau commit DB gagal setelah foto terlanjur tersimpan."""
    if not url:
        return
    try:
        os.remove(os.path.join(UPLOAD_DIR, os.path.basename(url)))
    except OSError:
        pass


def _parse_items_manual(items_json: str) -> list:
    try:
        raw = json.loads(items_json)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format 'items' bukan JSON yang valid")
    if not isinstance(raw, list) or not raw:
        raise HTTPException(status_code=400, detail="Minimal harus ada 1 item sampah")
    if len(raw) > MAX_ITEM_MANUAL:
        raise HTTPException(status_code=400, detail=f"Maksimal {MAX_ITEM_MANUAL} item per transaksi")
    try:
        return [DetailTransaksiCreate(**r) for r in raw]
    except (ValidationError, TypeError):
        raise HTTPException(
            status_code=422,
            detail="Item tidak valid: tiap item butuh id_jenis dan berat (> 0)",
        )


# ========== ADMIN: BUAT TRANSAKSI MANUAL (WAJIB FOTO TIAP ITEM) ==========
@router.post("/create-manual", response_model=TransaksiResponse)
def create_transaksi_manual(
    nik: str = Form(..., min_length=16, max_length=16),
    items: str = Form(..., description='JSON: [{"id_jenis": 1, "berat": 2.5}, ...]'),
    foto: List[UploadFile] = File(..., description="Satu foto per item, urutan SAMA dengan 'items'"),
    keterangan: Optional[str] = Form(None, max_length=500),
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    """
    Admin mencatat setoran secara manual (tanpa IoT). Tiap item WAJIB punya foto.
    Transaksi ditandai dibuat_manual=True + dibuat_oleh=<username admin>.
    Request ini multipart/form-data. Harga tetap diambil dari tabel jenis_sampah.
    """
    admin_session = get_current_admin(authorization)
    daftar = _parse_items_manual(items)

    if len(foto) != len(daftar):
        raise HTTPException(
            status_code=400,
            detail=f"Jumlah foto ({len(foto)}) harus sama dengan jumlah item ({len(daftar)})",
        )

    # Kunci baris nasabah supaya dua setoran barengan tidak saling menimpa saldo
    nasabah = db.query(Nasabah).filter(Nasabah.nik == nik).with_for_update().first()
    if not nasabah:
        raise HTTPException(status_code=404, detail="Nasabah dengan NIK tersebut tidak ditemukan")
    if not nasabah.is_active:
        raise HTTPException(status_code=403, detail="Akun nasabah ini sedang dinonaktifkan")

    # Cek semua jenis sampah SEBELUM menyimpan foto apa pun
    jenis_list = []
    for item in daftar:
        jenis = db.query(JenisSampah).filter(JenisSampah.id_jenis == item.id_jenis).first()
        if not jenis:
            raise HTTPException(status_code=404, detail=f"Jenis sampah id {item.id_jenis} tidak ditemukan")
        jenis_list.append(jenis)

    foto_tersimpan: list = []
    try:
        for upload in foto:
            foto_tersimpan.append(_simpan_foto_sampah(upload))

        total_berat = Decimal(0)
        total_nilai = Decimal(0)
        transaksi = Transaksi(
            nik=nik,
            total_berat=0,
            total_nilai=0,
            keterangan=keterangan,
            dibuat_manual=True,
            dibuat_oleh=admin_session["sub"],
        )
        db.add(transaksi)
        db.flush()

        detail_responses = []
        for item, jenis, url_foto in zip(daftar, jenis_list, foto_tersimpan):
            subtotal = item.berat * jenis.harga_per_kg
            total_berat += item.berat
            total_nilai += subtotal
            detail = DetailTransaksi(
                id_transaksi=transaksi.id_transaksi,
                id_jenis=item.id_jenis,
                berat=item.berat,
                harga_per_kg=jenis.harga_per_kg,
                subtotal=subtotal,
                foto_url=url_foto,
            )
            db.add(detail)
            db.flush()
            detail_responses.append(_serialize_detail(detail, db))

        transaksi.total_berat = total_berat
        transaksi.total_nilai = total_nilai
        nasabah.saldo = nasabah.saldo + total_nilai
        db.commit()
    except Exception:
        db.rollback()
        for url_foto in foto_tersimpan:
            _hapus_foto_sampah(url_foto)  # jangan tinggalkan foto yatim
        raise

    db.refresh(transaksi)
    db.refresh(nasabah)
    return TransaksiResponse(
        id_transaksi=transaksi.id_transaksi,
        nik=transaksi.nik,
        tanggal_transaksi=transaksi.tanggal_transaksi,
        total_berat=transaksi.total_berat,
        total_nilai=transaksi.total_nilai,
        keterangan=transaksi.keterangan,
        dibuat_manual=True,
        dibuat_oleh=transaksi.dibuat_oleh,
        saldo_nasabah_sekarang=nasabah.saldo,
        details=detail_responses,
    )

# ========== ADMIN: LIHAT SEMUA RIWAYAT TRANSAKSI ==========
@router.get("/history-admin", response_model=list[TransaksiHistoryResponse])
def get_history_admin(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
    nik: Optional[str] = None,
    tanggal_mulai: Optional[date] = None,
    tanggal_akhir: Optional[date] = None,
    limit: int = 100,
):
    """Admin lihat semua riwayat transaksi (semua nasabah), lengkap dengan status flag per item."""
    get_current_admin(authorization)

    query = db.query(Transaksi)
    if nik:
        query = query.filter(Transaksi.nik == nik)
    if tanggal_mulai:
        query = query.filter(Transaksi.tanggal_transaksi >= tanggal_mulai)
    if tanggal_akhir:
        query = query.filter(Transaksi.tanggal_transaksi <= tanggal_akhir)

    transaksi_list = query.order_by(Transaksi.tanggal_transaksi.desc()).limit(limit).all()
    return [_to_history_response(t, db) for t in transaksi_list]

# ========== NASABAH: LIHAT HISTORY TRANSAKSI SENDIRI ==========
@router.get("/history", response_model=list[TransaksiHistoryResponse])
def get_history(
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Nasabah lihat riwayat transaksi sendiri (pakai token nasabah)"""
    nik = get_current_nasabah(authorization)
    transaksi_list = db.query(Transaksi)\
        .filter(Transaksi.nik == nik)\
        .order_by(Transaksi.tanggal_transaksi.desc())\
        .all()
    return [_to_history_response(t, db) for t in transaksi_list]

# ========== ADMIN: DETAIL SATU TRANSAKSI (buat modal review) ==========
@router.get("/{id_transaksi}", response_model=TransaksiHistoryResponse)
def get_transaksi_detail(
    id_transaksi: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    get_current_admin(authorization)

    transaksi = db.query(Transaksi).filter(Transaksi.id_transaksi == id_transaksi).first()
    if not transaksi:
        raise HTTPException(status_code=404, detail="Transaksi tidak ditemukan")

    return _to_history_response(transaksi, db)


# ========== IOT: BUAT TRANSAKSI OTOMATIS DARI DETEKSI ML ==========
def buat_transaksi_iot(
    db: Session,
    nik: str,
    items: list[dict],
    keterangan: Optional[str] = None,
) -> TransaksiResponse:
    """
    Logika inti pembuatan transaksi otomatis (dipakai /create-iot dan penggabung IoT+ML).
    TIDAK commit -> caller yang commit, supaya bisa atomic dengan update status scan.
 
    items: list dict dengan key:
        id_jenis, berat, jenis_terdeteksi (opsional), confidence (opsional), foto_url (opsional)
    """
    nasabah = db.query(Nasabah).filter(Nasabah.nik == nik).first()
    if not nasabah:
        raise HTTPException(status_code=404, detail="Nasabah dengan NIK tersebut tidak ditemukan")
    if not nasabah.is_active:
        raise HTTPException(status_code=403, detail="Akun nasabah ini sedang dinonaktifkan")
 
    total_berat = Decimal(0)
    total_nilai = Decimal(0)
    detail_list = []
 
    for item in items:
        jenis = db.query(JenisSampah).filter(JenisSampah.id_jenis == item["id_jenis"]).first()
        if not jenis:
            raise HTTPException(status_code=404, detail=f"Jenis sampah id {item['id_jenis']} tidak ditemukan")
 
        harga_per_kg = jenis.harga_per_kg
        subtotal = item["berat"] * harga_per_kg
        total_berat += item["berat"]
        total_nilai += subtotal
 
        detail_list.append({**item, "harga_per_kg": harga_per_kg, "subtotal": subtotal})
 
    transaksi = Transaksi(
        nik=nik,
        total_berat=total_berat,
        total_nilai=total_nilai,
        keterangan=keterangan or "Transaksi otomatis dari IoT",
    )
    db.add(transaksi)
    db.flush()
 
    detail_responses = []
    for d in detail_list:
        detail_transaksi = DetailTransaksi(
            id_transaksi=transaksi.id_transaksi,
            id_jenis=d["id_jenis"],
            berat=d["berat"],
            harga_per_kg=d["harga_per_kg"],
            subtotal=d["subtotal"],
        )
        db.add(detail_transaksi)
        db.flush()  # perlu id_detail sebelum dipakai DataIoT
 
        db.add(DataIoT(
            id_transaksi=transaksi.id_transaksi,
            id_detail=detail_transaksi.id_detail,
            jenis_terdeteksi=d.get("jenis_terdeteksi"),
            berat_sensor=d["berat"],
            confidence=d.get("confidence"),
            foto_url=d.get("foto_url"),
        ))
        db.flush()
 
        detail_responses.append(_serialize_detail(detail_transaksi, db))
 
    nasabah.saldo = nasabah.saldo + total_nilai
    db.flush()
 
    return TransaksiResponse(
        id_transaksi=transaksi.id_transaksi,
        nik=transaksi.nik,
        tanggal_transaksi=transaksi.tanggal_transaksi,
        total_berat=transaksi.total_berat,
        total_nilai=transaksi.total_nilai,
        keterangan=transaksi.keterangan,
        saldo_nasabah_sekarang=nasabah.saldo,
        details=detail_responses,
    )
 
 
@router.post("/create-iot", response_model=TransaksiResponse)
def create_transaksi_iot(
    data: TransaksiIoTCreate,
    db: Session = Depends(get_db),
    _: None = Depends(verify_iot_api_key),
):
    """Endpoint lama (1 request lengkap: berat + hasil ML). Tetap dipertahankan."""
    items = [
        {
            "id_jenis": item.id_jenis,
            "berat": item.berat,
            "jenis_terdeteksi": item.jenis_terdeteksi,
            "confidence": item.confidence,
            "foto_url": _save_base64_image(item.foto_base64),
        }
        for item in data.detail
    ]
    hasil = buat_transaksi_iot(db, data.nik, items, data.keterangan or "Transaksi otomatis dari IoT")
    db.commit()
    return hasil


# ========== ADMIN: EDIT 1 ITEM SAMPAH DALAM TRANSAKSI ==========
@router.put("/{id_transaksi}/detail/{id_detail}", response_model=DetailTransaksiResponse)
def update_detail_transaksi(
    id_transaksi: int,
    id_detail: int,
    data: DetailTransaksiUpdate,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    """Admin koreksi manual 1 item sampah (misal hasil deteksi ML salah / confidence rendah)."""
    admin_session = get_current_admin(authorization)
    admin_username = admin_session["sub"]

    transaksi = db.query(Transaksi).filter(Transaksi.id_transaksi == id_transaksi).first()
    if not transaksi:
        raise HTTPException(status_code=404, detail="Transaksi tidak ditemukan")

    detail = db.query(DetailTransaksi).filter(
        DetailTransaksi.id_detail == id_detail,
        DetailTransaksi.id_transaksi == id_transaksi,
    ).first()
    if not detail:
        raise HTTPException(status_code=404, detail="Item sampah tidak ditemukan di transaksi ini")

    nasabah = db.query(Nasabah).filter(Nasabah.nik == transaksi.nik).first()
    subtotal_lama = detail.subtotal

    if data.id_jenis is not None:
        jenis = db.query(JenisSampah).filter(JenisSampah.id_jenis == data.id_jenis).first()
        if not jenis:
            raise HTTPException(status_code=404, detail="Jenis sampah tidak ditemukan")
        detail.id_jenis = jenis.id_jenis
        detail.harga_per_kg = jenis.harga_per_kg

    if data.berat is not None:
        detail.berat = data.berat

    detail.subtotal = detail.berat * detail.harga_per_kg
    detail.dikoreksi_oleh = admin_username
    detail.dikoreksi_at = now_wib()

    # Selisih nilai disesuaikan ke saldo nasabah supaya tetap akurat
    selisih = detail.subtotal - subtotal_lama
    if nasabah:
        nasabah.saldo = nasabah.saldo + selisih

    _recompute_totals(transaksi, db)
    db.commit()
    db.refresh(detail)

    return _serialize_detail(detail, db)


# ========== ADMIN: HAPUS 1 ITEM SAMPAH DALAM TRANSAKSI ==========
@router.delete("/{id_transaksi}/detail/{id_detail}")
def delete_detail_transaksi(
    id_transaksi: int,
    id_detail: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    """
    Admin hapus 1 item sampah yang salah deteksi.
    CATATAN: penyesuaian saldo nasabah akibat penghapusan sengaja BELUM ditangani di sini
    (menyusul sesuai permintaan - akan dikerjakan di iterasi berikutnya).
    """
    get_current_admin(authorization)

    transaksi = db.query(Transaksi).filter(Transaksi.id_transaksi == id_transaksi).first()
    if not transaksi:
        raise HTTPException(status_code=404, detail="Transaksi tidak ditemukan")

    jumlah_item = db.query(DetailTransaksi).filter(DetailTransaksi.id_transaksi == id_transaksi).count()
    if jumlah_item <= 1:
        raise HTTPException(
            status_code=400,
            detail="Tidak bisa menghapus item terakhir dalam transaksi."
        )

    detail = db.query(DetailTransaksi).filter(
        DetailTransaksi.id_detail == id_detail,
        DetailTransaksi.id_transaksi == id_transaksi,
    ).first()
    if not detail:
        raise HTTPException(status_code=404, detail="Item sampah tidak ditemukan di transaksi ini")

    db.query(DataIoT).filter(DataIoT.id_detail == id_detail).delete()
    db.delete(detail)
    db.flush()

    _recompute_totals(transaksi, db)
    db.commit()

    return {"message": "Item sampah berhasil dihapus."}


# ========== ADMIN: TAMBAH ITEM SAMPAH BARU KE TRANSAKSI ==========
@router.post("/{id_transaksi}/detail", response_model=DetailTransaksiResponse)
def add_detail_transaksi(
    id_transaksi: int,
    data: DetailTransaksiCreate,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    """Admin tambah manual 1 item sampah baru ke transaksi yang sudah ada (misal ada sampah yang kelewat terdeteksi)."""
    admin_session = get_current_admin(authorization)
    admin_username = admin_session["sub"]

    transaksi = db.query(Transaksi).filter(Transaksi.id_transaksi == id_transaksi).first()
    if not transaksi:
        raise HTTPException(status_code=404, detail="Transaksi tidak ditemukan")

    jenis = db.query(JenisSampah).filter(JenisSampah.id_jenis == data.id_jenis).first()
    if not jenis:
        raise HTTPException(status_code=404, detail="Jenis sampah tidak ditemukan")

    subtotal = data.berat * jenis.harga_per_kg

    detail = DetailTransaksi(
        id_transaksi=id_transaksi,
        id_jenis=jenis.id_jenis,
        berat=data.berat,
        harga_per_kg=jenis.harga_per_kg,
        subtotal=subtotal,
        ditambahkan_manual=True,
        dikoreksi_oleh=admin_username,
        dikoreksi_at=now_wib(),
    )
    db.add(detail)

    nasabah = db.query(Nasabah).filter(Nasabah.nik == transaksi.nik).first()
    if nasabah:
        nasabah.saldo = nasabah.saldo + subtotal

    db.flush()
    _recompute_totals(transaksi, db)
    db.commit()
    db.refresh(detail)

    return _serialize_detail(detail, db)