from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Optional
from decimal import Decimal


# ========== REQUEST SCHEMAS ==========

class DetailTransaksiCreate(BaseModel):
    """Admin cuma perlu kirim id_jenis + berat. Harga diambil dari DB (JenisSampah), bukan dari input,
    supaya harga tidak bisa dimanipulasi dari sisi client."""
    id_jenis: int = Field(..., gt=0, description="ID jenis sampah dari dropdown")
    berat: Decimal = Field(..., gt=0, decimal_places=2, description="Berat sampah dalam KG")

class DetailTransaksiUpdate(BaseModel):
    """Admin koreksi 1 item sampah (partial update)."""
    id_jenis: Optional[int] = Field(None, gt=0)
    berat: Optional[Decimal] = Field(None, gt=0, decimal_places=2)


class TransaksiCreate(BaseModel):
    """Dipakai ADMIN untuk mencatat transaksi setoran sampah nasabah"""
    nik: str = Field(..., min_length=16, max_length=16, description="NIK nasabah yang menyetor sampah")
    detail: List[DetailTransaksiCreate] = Field(..., min_length=1)
    keterangan: Optional[str] = None


# ========== RESPONSE SCHEMAS ==========

class DetailTransaksiResponse(BaseModel):
    id_detail: int
    id_jenis: int
    nama_jenis: Optional[str] = None
    berat: Decimal
    harga_per_kg: Decimal
    subtotal: Decimal

    jenis_terdeteksi: Optional[str] = None
    confidence: Optional[Decimal] = None
    foto_url: Optional[str] = None
    is_flagged: bool = False
    ditambahkan_manual: bool = False
    dikoreksi_oleh: Optional[str] = None
    dikoreksi_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TransaksiResponse(BaseModel):
    id_transaksi: int
    nik: str
    tanggal_transaksi: datetime
    total_berat: Decimal
    total_nilai: Decimal
    keterangan: Optional[str]
    saldo_nasabah_sekarang: Decimal
    details: List[DetailTransaksiResponse]
    dibuat_manual: bool = False
    dibuat_oleh: Optional[str] = None

    class Config:
        from_attributes = True

class TransaksiHistoryResponse(BaseModel):
    """Dipakai untuk list riwayat transaksi (nasabah & admin) — tanpa saldo snapshot,
    karena saldo 'sekarang' gak relevan ditampilkan per baris transaksi lama."""
    id_transaksi: int
    nik: str
    nama_nasabah: Optional[str] = None
    tanggal_transaksi: datetime
    total_berat: Decimal
    total_nilai: Decimal
    keterangan: Optional[str]
    ada_flag: bool = False
    details: List[DetailTransaksiResponse]
    dibuat_manual: bool = False
    dibuat_oleh: Optional[str] = None

    class Config:
        from_attributes = True

# ========== SCHEMA KHUSUS IOT ==========

class DetailTransaksiIoT(BaseModel):
    """Detail sampah yang terdeteksi ML, termasuk confidence score"""
    id_jenis: int = Field(..., gt=0)
    berat: Decimal = Field(..., gt=0, decimal_places=2)
    jenis_terdeteksi: Optional[str] = None
    confidence: Optional[Decimal] = Field(None, ge=0, le=100, decimal_places=2)
    foto_base64: Optional[str] = Field(None, description="Foto hasil capture, base64 (boleh dengan prefix data:image/...)")

class TransaksiIoTCreate(BaseModel):
    """Dipakai oleh device IoT untuk mencatat transaksi otomatis dari hasil deteksi ML"""
    nik: str = Field(..., min_length=16, max_length=16, description="NIK nasabah yang menyetor sampah")
    detail: List[DetailTransaksiIoT] = Field(..., min_length=1)
    keterangan: Optional[str] = None

