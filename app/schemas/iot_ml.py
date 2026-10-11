from pydantic import BaseModel, Field
from typing import Optional
from decimal import Decimal
from datetime import datetime
from app.schemas.transaksi import TransaksiResponse

class IoTBeratCreate(BaseModel):
    """Dikirim tim IoT: cuma berat. NIK & scan_id opsional."""
    machine_id: str = Field(..., min_length=1, max_length=50)
    berat: Decimal = Field(..., gt=0, decimal_places=2, description="Berat dalam KG")
    nik: Optional[str] = Field(None, min_length=16, max_length=16)
    scan_id: Optional[str] = Field(None, max_length=64)


class MLDeteksiCreate(BaseModel):
    """Dikirim tim ML: label hasil deteksi + confidence + foto."""
    machine_id: str = Field(..., min_length=1, max_length=50)
    jenis_terdeteksi: str = Field(..., min_length=1, max_length=100, description="Contoh: metal_can")
    confidence: Optional[Decimal] = Field(None, ge=0, le=100, decimal_places=2)
    foto_base64: Optional[str] = None
    nik: Optional[str] = Field(None, min_length=16, max_length=16)
    scan_id: Optional[str] = Field(None, max_length=64)


class ScanResponse(BaseModel):
    id: int
    machine_id: str
    status: str
    pesan: str
    id_transaksi: Optional[int] = None

class SesiMulaiRequest(BaseModel):
    """Dikirim web nasabah setelah scan QR mesin. NIK diambil dari token login, bukan dari body."""
    machine_id: str = Field(..., min_length=1, max_length=50)
 
 
class SesiResponse(BaseModel):
    id_sesi: int
    machine_id: str
    nik: str
    status: str
    pesan: str
    jumlah_item_lengkap: int = 0          # item (berat+label) yang sudah lengkap di sesi ini
    jumlah_item_butuh_review: int = 0     # lengkap tapi label belum dipetakan (tidak ikut transaksi)
    started_at: datetime
    transaksi: Optional[TransaksiResponse] = None