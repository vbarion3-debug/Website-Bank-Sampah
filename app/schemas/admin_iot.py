from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ========== HEARTBEAT (dikirim alat IoT) ==========
class HeartbeatRequest(BaseModel):
    machine_id: str = Field(..., min_length=1, max_length=50)


# ========== DATABASE PERANGKAT ==========
class PerangkatCreate(BaseModel):
    machine_id: str = Field(..., min_length=1, max_length=50)
    nama: str = Field(..., min_length=1, max_length=100)
    lokasi: Optional[str] = Field(None, max_length=150)
    is_active: bool = True


class PerangkatUpdate(BaseModel):
    nama: Optional[str] = Field(None, min_length=1, max_length=100)
    lokasi: Optional[str] = Field(None, max_length=150)
    is_active: Optional[bool] = None


class PerangkatResponse(BaseModel):
    id: int
    machine_id: str
    nama: str
    lokasi: Optional[str] = None
    is_active: bool
    last_seen: Optional[datetime] = None
    online: bool = False
    status: str  # offline | tersambung | dipakai | nonaktif
    sesi_id: Optional[int] = None
    nik_pemakai: Optional[str] = None
    nama_pemakai: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# ========== SESI SETORAN (admin) ==========
class SesiMulaiAdminRequest(BaseModel):
    machine_id: str = Field(..., min_length=1, max_length=50)
    nik: str = Field(..., min_length=16, max_length=16)


class ItemJenisUpdate(BaseModel):
    id_jenis: int


class ItemSesiResponse(BaseModel):
    id: int
    status: str
    berat: Optional[Decimal] = None
    jenis_terdeteksi: Optional[str] = None   # label mentah dari ML
    confidence: Optional[Decimal] = None
    foto_url: Optional[str] = None
    id_jenis: Optional[int] = None           # jenis final (override admin / hasil pemetaan kode_ml)
    nama_jenis: Optional[str] = None
    harga_per_kg: Optional[Decimal] = None
    subtotal: Optional[Decimal] = None
    diubah_manual: bool = False
    lengkap: bool = False
    perlu_perhatian: bool = False            # confidence rendah / label belum dipetakan
    created_at: datetime


class SesiDetailResponse(BaseModel):
    id_sesi: int
    machine_id: str
    nama_perangkat: Optional[str] = None
    perangkat_online: bool = False
    nik: str
    nama_nasabah: Optional[str] = None
    status: str
    started_at: datetime
    terakhir_aktif: datetime
    items: List[ItemSesiResponse]
    jumlah_item_siap: int = 0                # item yang akan masuk transaksi kalau disimpan sekarang
    total_berat: Decimal = Decimal(0)
    total_nilai: Decimal = Decimal(0)


# ========== SCAN KAMERA (YOLO di backend) ==========
class ScanRequest(BaseModel):
    foto_base64: str = Field(..., min_length=100, description="JPEG hasil capture kamera browser (data URI atau base64 polos)")


class DeteksiItem(BaseModel):
    label: str                              # nama kelas YOLO
    confidence: float                       # 0-100
    nama_jenis: Optional[str] = None        # hasil pemetaan ke JenisSampah.kode_ml; None = belum dipetakan


class ScanHasilResponse(BaseModel):
    terdeteksi: bool
    pesan: str
    foto_url: Optional[str] = None          # foto dengan kotak deteksi
    deteksi: List[DeteksiItem] = []
    sesi: SesiDetailResponse


class KelasModel(BaseModel):
    id: int
    nama: str
    dipetakan_ke: Optional[str] = None      # nama jenis sampah yang kode_ml-nya sama dengan nama kelas


class ModelInfoResponse(BaseModel):
    model_path: str
    jumlah_kelas: int
    kelas: List[KelasModel]
