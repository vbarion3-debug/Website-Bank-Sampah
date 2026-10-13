from pydantic import BaseModel, Field
from typing import Optional
from decimal import Decimal


# ========== REQUEST SCHEMAS ==========

class JenisSampahCreate(BaseModel):
    nama_jenis: str = Field(..., min_length=2, max_length=100)
    kategori: Optional[str] = Field(None, max_length=50, description="Contoh: Logam, Plastik, Kertas, Kaca")
    harga_per_kg: Decimal = Field(..., gt=0, decimal_places=2, description="Harga beli per kg dalam Rupiah")
    kode_ml: Optional[str] = Field(None, max_length=50, description="Label model ML, contoh: metal_can")


class JenisSampahUpdate(BaseModel):
    """Semua field optional - partial update, sama seperti PengumumanUpdate"""
    nama_jenis: Optional[str] = Field(None, min_length=2, max_length=100)
    kategori: Optional[str] = Field(None, max_length=50)
    harga_per_kg: Optional[Decimal] = Field(None, gt=0, decimal_places=2)
    kode_ml: Optional[str] = Field(None, max_length=50)  


# ========== RESPONSE SCHEMAS ==========

class JenisSampahResponse(BaseModel):
    id_jenis: int
    nama_jenis: str
    kategori: Optional[str]
    harga_per_kg: Decimal
    kode_ml: Optional[str] = None  

    class Config:
        from_attributes = True

class JenisSampahNasabahResponse(BaseModel):
    """Versi publik untuk nasabah (tanpa kode_ml / data internal)"""
    id_jenis: int
    nama_jenis: str
    kategori: Optional[str]
    harga_per_kg: Decimal

    class Config:
        from_attributes = True