from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Optional
from decimal import Decimal

# ========== REQUEST ==========

class DetailJualMitraCreate(BaseModel):
    id_jenis: int = Field(..., gt=0)
    berat: Decimal = Field(..., gt=0, decimal_places=2)

class TransaksiJualMitraCreate(BaseModel):
    id_mitra: int = Field(..., gt=0)
    detail: List[DetailJualMitraCreate] = Field(..., min_length=1)
    keterangan: Optional[str] = None

# ========== RESPONSE ==========

class DetailJualMitraResponse(BaseModel):
    id_detail_jual: int
    id_jenis: int
    nama_jenis: Optional[str] = None
    berat: Decimal
    harga_per_kg: Decimal
    subtotal: Decimal

    class Config:
        from_attributes = True

class TransaksiJualMitraResponse(BaseModel):
    id_transaksi_jual: int
    id_mitra: int
    nama_mitra: Optional[str] = None
    id_admin: int
    dicatat_oleh: Optional[str] = None  # username admin yang mencatat
    tanggal_transaksi: datetime
    total_berat: Decimal
    total_nilai: Decimal
    keterangan: Optional[str]
    details: List[DetailJualMitraResponse]

    class Config:
        from_attributes = True

# ========== LAPORAN PEMASUKAN (BARU) ==========

class RingkasanLaporanJual(BaseModel):
    jumlah_transaksi: int
    total_berat: Decimal
    total_nilai: Decimal             # sesuai filter yang dipilih
    pemasukan_bulan_ini: Decimal     # bulan berjalan (WIB), tidak terpengaruh filter tanggal

class LaporanPerJenis(BaseModel):
    id_jenis: int
    nama_jenis: Optional[str] = None
    berat: Decimal
    nilai: Decimal

class LaporanPerMitra(BaseModel):
    id_mitra: int
    nama_mitra: Optional[str] = None
    jumlah_transaksi: int
    berat: Decimal
    nilai: Decimal

class LaporanPerBulan(BaseModel):
    bulan: str  # "YYYY-MM"
    berat: Decimal
    nilai: Decimal

class LaporanJualMitraResponse(BaseModel):
    ringkasan: RingkasanLaporanJual
    per_jenis: List[LaporanPerJenis]
    per_mitra: List[LaporanPerMitra]
    per_bulan: List[LaporanPerBulan]
    total: int                       # jumlah transaksi sesuai filter (untuk pagination)
    data: List[TransaksiJualMitraResponse]

# ========== STOK (BARU) ==========

class StokJenisResponse(BaseModel):
    id_jenis: int
    nama_jenis: str
    kategori: Optional[str] = None
    masuk: Decimal   # total berat dari setoran nasabah
    keluar: Decimal  # total berat yang sudah dijual ke yayasan
    stok: Decimal    # masuk - keluar (bisa negatif kalau data awal belum lengkap)