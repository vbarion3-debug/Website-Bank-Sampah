import csv
import io
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from fastapi.responses import Response
from sqlalchemy import func, literal_column
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.database import get_db
from app.core.timezone import now_wib
from app.models.models import (
    TransaksiJualMitra, DetailTransaksiJualMitra, DetailTransaksi,
    MitraPengepul, HargaMitraPengepul, JenisSampah, Admin
)
from app.schemas.transaksi_jual_mitra import (
    TransaksiJualMitraCreate, TransaksiJualMitraResponse, DetailJualMitraResponse,
    RingkasanLaporanJual, LaporanPerJenis, LaporanPerMitra, LaporanPerBulan,
    LaporanJualMitraResponse, StokJenisResponse,
)
from app.utils.security import get_current_admin

router = APIRouter(prefix="/transaksi-jual-mitra", tags=["Transaksi Jual ke Mitra"])

T = TransaksiJualMitra
D = DetailTransaksiJualMitra


# ========== HELPER ==========

def _query_dasar(db: Session):
    """Query transaksi + relasi yang selalu dibutuhkan response (anti N+1)."""
    return db.query(T).options(
        joinedload(T.mitra),
        joinedload(T.admin),
        selectinload(T.details).joinedload(D.jenis_sampah),
    )


def _to_response(t: TransaksiJualMitra) -> TransaksiJualMitraResponse:
    return TransaksiJualMitraResponse(
        id_transaksi_jual=t.id_transaksi_jual,
        id_mitra=t.id_mitra,
        nama_mitra=t.mitra.nama_mitra if t.mitra else None,
        id_admin=t.id_admin,
        dicatat_oleh=t.admin.username if t.admin else None,
        tanggal_transaksi=t.tanggal_transaksi,
        total_berat=t.total_berat,
        total_nilai=t.total_nilai,
        keterangan=t.keterangan,
        details=[
            DetailJualMitraResponse(
                id_detail_jual=d.id_detail_jual,
                id_jenis=d.id_jenis,
                nama_jenis=d.jenis_sampah.nama_jenis if d.jenis_sampah else None,
                berat=d.berat,
                harga_per_kg=d.harga_per_kg,
                subtotal=d.subtotal,
            )
            for d in sorted(t.details, key=lambda x: x.id_detail_jual)
        ],
    )


def _filter_laporan(
    tanggal_mulai: Optional[date],
    tanggal_akhir: Optional[date],
    id_mitra: Optional[int],
) -> list:
    """
    Kolom tanggal_transaksi bertipe DateTime, jadi 'tanggal_akhir' dibuat inklusif:
    transaksi sampai jam 23:59 di tanggal itu tetap ikut (bukan terpotong di 00:00).
    """
    if tanggal_mulai and tanggal_akhir and tanggal_mulai > tanggal_akhir:
        raise HTTPException(status_code=400, detail="Tanggal mulai tidak boleh setelah tanggal akhir")
    filters = []
    if tanggal_mulai:
        filters.append(T.tanggal_transaksi >= datetime.combine(tanggal_mulai, time.min))
    if tanggal_akhir:
        filters.append(T.tanggal_transaksi < datetime.combine(tanggal_akhir + timedelta(days=1), time.min))
    if id_mitra:
        filters.append(T.id_mitra == id_mitra)
    return filters


def _aman_csv(nilai) -> str:
    """Cegah CSV injection: sel teks yang diawali = + - @ dibaca Excel sebagai rumus."""
    s = "" if nilai is None else str(nilai)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


# ========== ADMIN: BUAT TRANSAKSI JUAL KE MITRA ==========

@router.post("/create", response_model=TransaksiJualMitraResponse)
def create_transaksi_jual_mitra(
    data: TransaksiJualMitraCreate,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """
    Admin mencatat transaksi penjualan sampah ke mitra pengepul.
    Harga per kg diambil dari HargaMitraPengepul (harga khusus mitra ini),
    bukan dari input admin, supaya harga tidak bisa dimanipulasi.
    """
    admin_session = get_current_admin(authorization)
    admin = db.query(Admin).filter(Admin.username == admin_session["sub"]).first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin tidak ditemukan")

    mitra = db.query(MitraPengepul).filter(MitraPengepul.id_mitra == data.id_mitra).first()
    if not mitra:
        raise HTTPException(status_code=404, detail="Mitra pengepul tidak ditemukan")
    if not mitra.is_active:
        raise HTTPException(status_code=403, detail="Kerjasama dengan mitra ini sedang tidak aktif")

    total_berat = Decimal(0)
    total_nilai = Decimal(0)
    detail_list = []

    for item in data.detail:
        jenis = db.query(JenisSampah).filter(JenisSampah.id_jenis == item.id_jenis).first()
        if not jenis:
            raise HTTPException(status_code=404, detail=f"Jenis sampah id {item.id_jenis} tidak ditemukan")

        # Harga WAJIB ada dulu di HargaMitraPengepul untuk jenis sampah ini
        harga_mitra = db.query(HargaMitraPengepul).filter(
            HargaMitraPengepul.id_mitra == data.id_mitra,
            HargaMitraPengepul.id_jenis == item.id_jenis
        ).first()
        if not harga_mitra:
            raise HTTPException(
                status_code=400,
                detail=f"Mitra ini belum punya harga untuk jenis sampah '{jenis.nama_jenis}'. "
                       f"Tambahkan harga dulu lewat /mitra-pengepul/{data.id_mitra}/harga"
            )

        harga_per_kg = harga_mitra.harga_beli_per_kg
        subtotal = item.berat * harga_per_kg
        total_berat += item.berat
        total_nilai += subtotal

        detail_list.append({
            "id_jenis": item.id_jenis,
            "berat": item.berat,
            "harga_per_kg": harga_per_kg,
            "subtotal": subtotal
        })

    transaksi_jual = TransaksiJualMitra(
        id_mitra=data.id_mitra,
        id_admin=admin.id_admin,
        total_berat=total_berat,
        total_nilai=total_nilai,
        keterangan=data.keterangan
    )
    db.add(transaksi_jual)
    db.flush()

    for detail in detail_list:
        db.add(DetailTransaksiJualMitra(
            id_transaksi_jual=transaksi_jual.id_transaksi_jual,
            id_jenis=detail["id_jenis"],
            berat=detail["berat"],
            harga_per_kg=detail["harga_per_kg"],
            subtotal=detail["subtotal"]
        ))

    db.commit()
    db.refresh(transaksi_jual)
    return _to_response(transaksi_jual)


# ========== ADMIN: LIST TRANSAKSI JUAL KE MITRA ==========

@router.get("/", response_model=List[TransaksiJualMitraResponse])
def list_transaksi_jual_mitra(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Terbaru di atas. Untuk laporan lengkap + filter, pakai GET /laporan."""
    get_current_admin(authorization)
    rows = (
        _query_dasar(db)
        .order_by(T.tanggal_transaksi.desc(), T.id_transaksi_jual.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_to_response(t) for t in rows]


# ========== ADMIN: LAPORAN PEMASUKAN (BARU) ==========
# Semua route statis (/laporan, /stok, /mitra/...) HARUS di atas "GET /{id_transaksi_jual}".

@router.get("/laporan", response_model=LaporanJualMitraResponse)
def laporan_pemasukan(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
    tanggal_mulai: Optional[date] = Query(None, description="YYYY-MM-DD (inklusif)"),
    tanggal_akhir: Optional[date] = Query(None, description="YYYY-MM-DD (inklusif)"),
    id_mitra: Optional[int] = Query(None, gt=0, description="Filter 1 yayasan"),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """
    Laporan pemasukan dari penjualan sampah ke yayasan.
    Ringkasan & rincian dihitung di database (bukan di browser), jadi tetap akurat
    walau datanya banyak. Hanya 'data' yang dipaginasi; ringkasan selalu mencakup semua hasil filter.
    """
    get_current_admin(authorization)
    filters = _filter_laporan(tanggal_mulai, tanggal_akhir, id_mitra)

    jumlah, total_berat, total_nilai = db.query(
        func.count(T.id_transaksi_jual),
        func.coalesce(func.sum(T.total_berat), 0),
        func.coalesce(func.sum(T.total_nilai), 0),
    ).filter(*filters).one()

    # Pemasukan bulan berjalan (WIB). Hanya ikut filter yayasan, tidak ikut filter tanggal.
    awal_bulan = now_wib().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    filter_bulan = [T.tanggal_transaksi >= awal_bulan]
    if id_mitra:
        filter_bulan.append(T.id_mitra == id_mitra)
    pemasukan_bulan_ini = db.query(
        func.coalesce(func.sum(T.total_nilai), 0)
    ).filter(*filter_bulan).scalar()

    per_jenis = [
        LaporanPerJenis(id_jenis=r[0], nama_jenis=r[1], berat=r[2], nilai=r[3])
        for r in (
            db.query(D.id_jenis, JenisSampah.nama_jenis, func.sum(D.berat), func.sum(D.subtotal))
            .join(T, T.id_transaksi_jual == D.id_transaksi_jual)
            .join(JenisSampah, JenisSampah.id_jenis == D.id_jenis)
            .filter(*filters)
            .group_by(D.id_jenis, JenisSampah.nama_jenis)
            .order_by(func.sum(D.berat).desc())
            .all()
        )
    ]

    per_mitra = [
        LaporanPerMitra(id_mitra=r[0], nama_mitra=r[1], jumlah_transaksi=r[2], berat=r[3], nilai=r[4])
        for r in (
            db.query(
                T.id_mitra, MitraPengepul.nama_mitra,
                func.count(T.id_transaksi_jual), func.sum(T.total_berat), func.sum(T.total_nilai),
            )
            .join(MitraPengepul, MitraPengepul.id_mitra == T.id_mitra)
            .filter(*filters)
            .group_by(T.id_mitra, MitraPengepul.nama_mitra)
            .order_by(func.sum(T.total_nilai).desc())
            .all()
        )
    ]

    bulan = func.to_char(T.tanggal_transaksi, literal_column("'YYYY-MM'"))
    per_bulan = [
        LaporanPerBulan(bulan=r[0], berat=r[1], nilai=r[2])
        for r in (
            db.query(bulan, func.sum(T.total_berat), func.sum(T.total_nilai))
            .filter(*filters)
            .group_by(bulan)
            .order_by(bulan)
            .all()
        )
    ][-12:]  # maksimal 12 bulan terakhir dalam rentang filter

    rows = (
        _query_dasar(db)
        .filter(*filters)
        .order_by(T.tanggal_transaksi.desc(), T.id_transaksi_jual.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return LaporanJualMitraResponse(
        ringkasan=RingkasanLaporanJual(
            jumlah_transaksi=jumlah,
            total_berat=total_berat,
            total_nilai=total_nilai,
            pemasukan_bulan_ini=pemasukan_bulan_ini,
        ),
        per_jenis=per_jenis,
        per_mitra=per_mitra,
        per_bulan=per_bulan,
        total=jumlah,
        data=[_to_response(t) for t in rows],
    )


@router.get("/laporan/export")
def export_laporan_pemasukan(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
    tanggal_mulai: Optional[date] = Query(None),
    tanggal_akhir: Optional[date] = Query(None),
    id_mitra: Optional[int] = Query(None, gt=0),
):
    """CSV (bisa dibuka di Excel), satu baris per jenis sampah, mengikuti filter yang sama dengan /laporan."""
    get_current_admin(authorization)
    filters = _filter_laporan(tanggal_mulai, tanggal_akhir, id_mitra)
    rows = (
        _query_dasar(db)
        .filter(*filters)
        .order_by(T.tanggal_transaksi.desc(), T.id_transaksi_jual.desc())
        .all()
    )

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "Tanggal", "ID Transaksi", "Yayasan", "Jenis Sampah", "Berat (kg)",
        "Harga per kg", "Subtotal", "Dicatat oleh", "Keterangan",
    ])
    for t in rows:
        for d in sorted(t.details, key=lambda x: x.id_detail_jual):
            w.writerow([
                t.tanggal_transaksi.strftime("%Y-%m-%d %H:%M"),
                t.id_transaksi_jual,
                _aman_csv(t.mitra.nama_mitra if t.mitra else ""),
                _aman_csv(d.jenis_sampah.nama_jenis if d.jenis_sampah else ""),
                d.berat,
                d.harga_per_kg,
                d.subtotal,
                _aman_csv(t.admin.username if t.admin else ""),
                _aman_csv(t.keterangan),
            ])

    return Response(
        content="\ufeff" + buf.getvalue(),  # BOM supaya Excel membaca UTF-8
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="laporan-pemasukan-yayasan.csv"'},
    )


# ========== ADMIN: STOK SAMPAH SAAT INI (BARU, hanya informasi) ==========

@router.get("/stok", response_model=List[StokJenisResponse])
def stok_sampah(
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    """
    Stok per jenis = total berat setoran nasabah - total berat yang sudah dijual ke yayasan.
    Hanya menampilkan angka; BELUM dipakai untuk menolak penjualan (lihat catatan di create).
    Stok negatif berarti ada penjualan yang melebihi setoran tercatat (mis. stok awal belum diinput).
    """
    get_current_admin(authorization)
    masuk = {j: b for j, b in db.query(DetailTransaksi.id_jenis, func.sum(DetailTransaksi.berat))
             .group_by(DetailTransaksi.id_jenis).all()}
    keluar = {j: b for j, b in db.query(D.id_jenis, func.sum(D.berat))
              .group_by(D.id_jenis).all()}

    hasil = []
    for j in db.query(JenisSampah).order_by(JenisSampah.nama_jenis.asc()).all():
        m = masuk.get(j.id_jenis) or Decimal(0)
        k = keluar.get(j.id_jenis) or Decimal(0)
        hasil.append(StokJenisResponse(
            id_jenis=j.id_jenis, nama_jenis=j.nama_jenis, kategori=j.kategori,
            masuk=m, keluar=k, stok=m - k,
        ))
    return hasil


# ========== ADMIN: HISTORY TRANSAKSI KE 1 MITRA TERTENTU ==========

@router.get("/mitra/{id_mitra}", response_model=List[TransaksiJualMitraResponse])
def get_transaksi_by_mitra(
    id_mitra: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    get_current_admin(authorization)
    mitra = db.query(MitraPengepul).filter(MitraPengepul.id_mitra == id_mitra).first()
    if not mitra:
        raise HTTPException(status_code=404, detail="Mitra pengepul tidak ditemukan")

    rows = (
        _query_dasar(db)
        .filter(T.id_mitra == id_mitra)
        .order_by(T.tanggal_transaksi.desc(), T.id_transaksi_jual.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_to_response(t) for t in rows]


# ========== ADMIN: DETAIL 1 TRANSAKSI (BARU) ==========
# HARUS paling bawah: path dinamis ini tidak boleh menelan /laporan, /stok, dst.

@router.get("/{id_transaksi_jual}", response_model=TransaksiJualMitraResponse)
def get_transaksi_jual_detail(
    id_transaksi_jual: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    get_current_admin(authorization)
    t = _query_dasar(db).filter(T.id_transaksi_jual == id_transaksi_jual).first()
    if not t:
        raise HTTPException(status_code=404, detail="Transaksi penjualan tidak ditemukan")
    return _to_response(t)