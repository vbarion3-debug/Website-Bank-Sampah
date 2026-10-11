from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime
from typing import List, Optional
from enum import Enum
from decimal import Decimal


class StatusPenarikanEnum(str, Enum):
    menunggu = "menunggu"
    disetujui = "disetujui"   # cadangan (belum dipakai di alur sekarang)
    ditolak = "ditolak"
    selesai = "selesai"
    dibatalkan = "dibatalkan"  # dibatalkan sendiri oleh nasabah


# ========== REQUEST SCHEMAS ==========

class PenarikanSaldoCreate(BaseModel):
    """Nasabah cuma kirim nominal. Rekening tujuan SELALU diambil dari data nasabah di server."""
    jumlah: Decimal = Field(..., gt=0, decimal_places=2, description="Nominal yang ingin ditarik")


class PenarikanTolak(BaseModel):
    """Admin menolak request - alasan wajib supaya nasabah tahu kenapa."""
    alasan: str = Field(..., min_length=5, max_length=500)


# ========== RESPONSE SCHEMAS ==========

class PenarikanSaldoResponse(BaseModel):
    """Dilihat nasabah (dan jadi dasar response admin)"""
    id_penarikan: int
    nik: str
    tanggal_pengajuan: datetime
    jumlah: Decimal
    status: StatusPenarikanEnum
    nama_bank: str
    no_rekening: str
    nama_pemilik_rekening: str
    keterangan: Optional[str] = None   # catatan admin / alasan penolakan
    tanggal_diproses: Optional[datetime] = None
    ada_bukti: bool = False            # file bukti diambil lewat endpoint /bukti

    model_config = ConfigDict(from_attributes=True)


class PenarikanAdminResponse(PenarikanSaldoResponse):
    """Dilihat admin: tambahan info nasabah & siapa yang memproses"""
    nama_nasabah: Optional[str] = None
    no_hp_nasabah: Optional[str] = None
    diproses_oleh: Optional[str] = None  # username admin


class PenarikanAdminListResponse(BaseModel):
    total: int
    data: List[PenarikanAdminResponse]
